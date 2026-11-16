"""Suggest: Claude recommends sources for a script passage.

Articles come back as real links (each one must have turned up in Claude's own web
searches this request, so nothing is made up), with the sentence to capture. Videos only
ever come back as *leads*: what to search for, where, and what clip to look for. The
editor finds the video and attaches it.

Claude learns the editor's taste from every link attached in every saved script project
(and from suggestions they skipped): a short sourcing profile, rebuilt as projects grow,
plus the past passages most like this one with what was attached to them.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urldefrag, urlsplit

from ..config import data_dir
from ..platforms import video_platform_in_script
from ..osutil import path_name

MODEL = "claude-opus-5-5"
EFFORT = "high"           # choosing sources is judgement work; "medium" is cheaper but sloppier
KEYRING_SERVICE = "Mediatheque"
KEYRING_USER = "anthropic-api-key"
CONSOLE_URL = "https://platform.claude.com/settings/keys"

# Approximate list prices for the cost shown after each suggestion (USD).
PRICE_INPUT = 4.00 / 1e6
PRICE_CACHE_WRITE = 5.00 / 1e6
PRICE_CACHE_READ = 0.20 / 1e6
PRICE_OUTPUT = 20.00 / 1e6
PRICE_SEARCH = 10.00 / 1000

MODES = {"all": "Suggest", "videos": "Suggest Videos", "articles": "Suggest Articles"}
LEAD_PLATFORMS = ["YouTube", "TikTok", "Instagram", "Facebook", "X", "Any"]

log = logging.getLogger("mediatheque.suggest")


class SuggestError(Exception):
    """Something the editor needs to hear about in plain words."""


# -- the API key (kept in Windows Credential Manager, not in a settings file) -------------

def get_key() -> str:
    try:
        import keyring
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:  # no credential store available
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY", "")


def set_key(key: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.strip())


def clear_key() -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass


def make_client(key: str | None = None):
    import anthropic
    return anthropic.Anthropic(api_key=key or get_key(), max_retries=2, timeout=600)


# -- results -----------------------------------------------------------------------------

@dataclass
class ArticlePick:
    url: str
    title: str
    outlet: str
    why: str
    quote: str = ""

    @property
    def link(self) -> str:
        """The link to attach: with the quote as a text fragment, so the Screenshotter
        opens on that sentence and flashes it."""
        return quote_link(self.url, self.quote) if self.quote else self.url


@dataclass
class VideoLead:
    platform: str
    search: str
    look_for: str
    why: str

    def to_dict(self) -> dict:
        return {"platform": self.platform, "search": self.search, "look_for": self.look_for, "why": self.why}


@dataclass
class Suggestions:
    articles: list[ArticlePick] = field(default_factory=list)
    leads: list[VideoLead] = field(default_factory=list)
    comment: str = ""
    searches: list[str] = field(default_factory=list)
    dropped: int = 0          # article links Claude gave that weren't in its search results
    cost: float = 0.0


def search_url(lead: VideoLead | dict) -> str:
    """Where a lead's search button goes."""
    d = lead.to_dict() if isinstance(lead, VideoLead) else lead
    q = quote(d.get("search", ""), safe="")
    platform = d.get("platform", "Any")
    return {
        "YouTube": f"https://www.youtube.com/results?search_query={q}",
        "TikTok": f"https://www.tiktok.com/search?q={q}",
        "Instagram": f"https://www.google.com/search?q={quote('site:instagram.com ' + d.get('search', ''), safe='')}",
        "Facebook": f"https://www.facebook.com/search/videos/?q={q}",
        "X": f"https://x.com/search?q={q}&f=video",
    }.get(platform, f"https://www.google.com/search?q={q}&tbm=vid")


def quote_link(url: str, sentence: str) -> str:
    """url#:~:text=… for one sentence (long ones become start…end)."""
    base, _ = urldefrag(url)
    words = sentence.strip().split()
    if not words:
        return base
    enc = lambda s: quote(s, safe="")  # noqa: E731 (also encodes , - & which are syntax there)
    if len(words) > 12:
        return f"{base}#:~:text={enc(' '.join(words[:5]))},{enc(' '.join(words[-5:]))}"
    return f"{base}#:~:text={enc(' '.join(words))}"


def _norm(url: str) -> str:
    base, _ = urldefrag(url.strip())
    p = urlsplit(base)
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    return f"{host}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


# -- what the editor has chosen before ---------------------------------------------------

STOP = set("""a an and are as at be been but by for from had has have he her his i if in into is it its
of on or our she so than that the their them then there these they this to was we were what when which
who will with would you your about after all also any can could did do does just more most not now
only other out over some such very""".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2 and w not in STOP}


@dataclass
class Example:
    project: str
    project_file: str
    hid: int
    passage: str
    note: str
    links: list[tuple[str, str, str]]   # (kind, url, what it produced, e.g. a file name)
    declined: list[str]
    leads: list[str]
    pointers: str = ""

    def render(self) -> str:
        lines = [f'From "{self.project}": “{self.passage.strip()}”']
        if self.note:
            lines.append(f"  Editor's note: {self.note.strip()}")
        if self.pointers:
            lines.append(f"  Pointers they gave when asking: {self.pointers.strip()}")
        for kind, url, made in self.links:
            lines.append(f"  - {kind}: {url}" + (f"  ({made})" if made else ""))
        for s in self.leads:
            lines.append(f"  - video lead they kept: {s}")
        for url in self.declined[:4]:
            lines.append(f"  - suggested but skipped: {url}")
        return "\n".join(lines)


def _made(files: list[str]) -> str:
    names = []
    for f in files[:2]:
        stem = os.path.splitext(path_name(f))[0]  # Windows-saved paths too, on a Mac
        names.append(re.sub(r"^\d+[a-z]?_", "", stem))
    return ", ".join(names)


def gather_examples(projects_dir: Path | None = None) -> list[Example]:
    """Every highlight in every saved project that has links attached, links skipped, or
    leads kept: the record of what this editor picks."""
    out: list[Example] = []
    folder = projects_dir or (data_dir() / "projects")
    for f in sorted(folder.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = Path(d.get("script", "")).stem or f.stem
        removed = set(d.get("removed", []))
        for h in d.get("highlights", []):
            if h.get("para") in removed:
                continue
            links = [(l.get("kind", "article"), urldefrag(l.get("url", ""))[0], _made(l.get("files", [])))
                     for l in h.get("links", []) if l.get("url")]
            declined = list(h.get("declined", []))
            leads = [f"{x.get('platform', '')}: {x.get('search', '')}" for x in h.get("leads", [])]
            if links or declined or leads:
                out.append(Example(name, str(f), h.get("id", 0), h.get("text", ""), h.get("note", ""),
                                   links, declined, leads, h.get("pointers", "")))
    return out


def similar(examples: list[Example], passage: str, k: int = 8, skip: tuple[str, int] | None = None,
            project_file: str = "") -> list[Example]:
    """The past passages most like this one (simple word overlap: no extra service needed)."""
    target = _words(passage)
    if not target:
        return []
    scored = []
    for ex in examples:
        if skip and (os.path.normcase(ex.project_file), ex.hid) == (os.path.normcase(skip[0]), skip[1]):
            continue
        w = _words(ex.passage + " " + ex.note)
        if not w:
            continue
        score = len(target & w) / len(target | w)
        if project_file and os.path.normcase(ex.project_file) == os.path.normcase(project_file):
            score += 0.05  # the same script: same topic, often the same sources
        scored.append((score, ex))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [ex for s, ex in scored[:k] if s > 0]


# -- the sourcing profile ----------------------------------------------------------------

PROFILE_MIN = 8        # examples needed before a profile is worth writing
PROFILE_REBUILD = 20   # rewrite it after this many new examples


def _profile_path() -> Path:
    return data_dir() / "suggest_profile.json"


def load_profile() -> dict:
    try:
        return json.loads(_profile_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def profile_stale(examples: list[Example]) -> bool:
    if len(examples) < PROFILE_MIN:
        return False
    prof = load_profile()
    return not prof.get("text") or len(examples) - prof.get("examples", 0) >= PROFILE_REBUILD


PROFILE_PROMPT = """You are helping a video-essay channel's editor get better source suggestions.
Below is every passage from their past scripts that they attached sources to, with the links
they attached (and any suggestions they skipped). Write their sourcing profile: a compact
guide (at most 250 words, plain bullet points) to how they choose sources, that a researcher
could follow. Cover: the kinds of sources they favour (primary documents, press releases,
filings, studies, news outlets, which outlets come up often), the balance of articles and
videos, which platforms the videos come from and what kind of clips they are (news segments,
creators, ads, interviews), how recent sources tend to be, and anything they avoid. State only
what the examples show; don't pad. Reply with the bullet points only."""


def build_profile(client, examples: list[Example]) -> str:
    sample = examples
    if len(sample) > 300:  # keep the request a sensible size: an even spread across projects
        step = len(sample) / 300
        sample = [sample[int(i * step)] for i in range(300)]
    body = "\n\n".join(ex.render() for ex in sample)
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"{PROFILE_PROMPT}\n\n<examples>\n{body}\n</examples>"}],
    )
    if msg.stop_reason == "refusal":
        raise SuggestError("Claude declined to write the sourcing profile.")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    _profile_path().write_text(json.dumps({"text": text, "examples": len(examples), "built": time.time()},
                                          indent=1), encoding="utf-8")
    return text


# -- asking for sources ------------------------------------------------------------------

SYSTEM = """You find sources for a YouTube video-essay channel. The editor is assembling B-roll for a
script: for each highlighted passage they need on-screen evidence, either articles to screenshot
(headlines, key sentences, press releases, studies, filings) or video clips (news segments, viral
posts, ads, interviews, creators' videos).

How to work:
- Search the web for this passage's specific claim, not just its topic. Prefer the original or
  primary source (the company's own press release, the study, the filing, the outlet that broke the
  story) over write-ups of it. Prefer reputable outlets. Check the page really supports the claim.
- Articles: only give pages you found and opened with your tools in this conversation, with their
  real URLs. Read the page (web_fetch) and give, in "quote", one sentence copied exactly, word for
  word, from it that best shows the claim on screen - or "" if you couldn't read the page.
- Videos: NEVER give video links. Give leads instead: the platform, an exact phrase to type into that
  platform's search, what clip to look for (who, what moment, roughly when), and why it fits. Use the
  web to make leads specific (a creator's handle, a news segment's title, the date something aired).
- You have up to 12 web searches and 8 page reads for each request. Plan them: start specific,
  and read only the pages you're likely to suggest.
- If the editor gives pointers for this search, follow them; they override the usual preferences.
- Never repeat a link already attached to the highlight or one the editor skipped before.
- Follow the editor's sourcing profile and learn from their past choices shown below.
- "why" is one short sentence. "comment" is one or two sentences for the editor (what you found,
  or why something wasn't findable) - or "".
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "url": {"type": "string"}, "title": {"type": "string"}, "outlet": {"type": "string"},
                "why": {"type": "string"}, "quote": {"type": "string"},
            },
            "required": ["url", "title", "outlet", "why", "quote"], "additionalProperties": False}},
        "video_leads": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": LEAD_PLATFORMS},
                "search": {"type": "string"}, "look_for": {"type": "string"}, "why": {"type": "string"},
            },
            "required": ["platform", "search", "look_for", "why"], "additionalProperties": False}},
        "comment": {"type": "string"},
    },
    "required": ["articles", "video_leads", "comment"],
    "additionalProperties": False,
}

ASK = {
    "all": "Suggest 2-3 articles and 2-3 video leads for this passage.",
    "articles": "Suggest 3-5 articles for this passage. No video leads (leave video_leads empty).",
    "videos": "Suggest 3-5 video leads for this passage. No articles (leave articles empty).",
}


@dataclass
class Request:
    mode: str                 # "all" | "articles" | "videos"
    script_title: str
    script_text: str          # the whole script, for the argument around the passage
    label: str
    passage: str
    around: str               # the paragraph(s) around it
    note: str = ""
    attached: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    leads: list[str] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    profile: str = ""
    pointers: str = ""        # typed by the editor when asking: extra context, what to look for


def _user_content(r: Request) -> list[dict]:
    script_block = {"type": "text", "cache_control": {"type": "ephemeral"},
                    "text": f'The script, "{r.script_title}", for context:\n<script>\n{r.script_text}\n</script>'}
    parts = []
    if r.examples:
        parts.append("Past passages like this one, with what the editor attached:\n<past_choices>\n"
                     + "\n\n".join(ex.render() for ex in r.examples) + "\n</past_choices>")
    ask = [f"Highlight {r.label}: “{r.passage.strip()}”", f"Where it sits in the script:\n{r.around.strip()}"]
    if r.note:
        ask.append(f"The editor's note on this highlight: {r.note.strip()}")
    if r.attached:
        ask.append("Already attached (don't repeat):\n" + "\n".join(f"- {u}" for u in r.attached))
    if r.declined:
        ask.append("Suggested before and skipped by the editor (don't repeat):\n" + "\n".join(f"- {u}" for u in r.declined))
    if r.leads:
        ask.append("Video leads already kept (don't repeat):\n" + "\n".join(f"- {s}" for s in r.leads))
    if r.pointers.strip():
        ask.append(f"The editor's pointers for this search (follow these):\n{r.pointers.strip()}")
    ask.append(ASK[r.mode])
    parts.append("\n\n".join(ask))
    return [script_block] + [{"type": "text", "text": p} for p in parts]


def _system(profile: str) -> list[dict]:
    blocks = [{"type": "text", "text": SYSTEM}]
    blocks.append({"type": "text", "cache_control": {"type": "ephemeral"},
                   "text": "The editor's sourcing profile (written from their past scripts):\n"
                           + (profile.strip() or "(Not enough past scripts yet. Use good judgement.)")})
    return blocks


# The same for every mode: tools come first in the request, so any difference here would
# stop the script (cached after them) being reused between Suggest and Suggest Videos.
TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8, "max_content_tokens": 8000},
]


def _collect_urls(obj, out: set[str], inside_result: bool = False) -> None:
    """Every URL that came back from a search or fetch (not ones Claude typed itself)."""
    if isinstance(obj, dict):
        is_result = inside_result or str(obj.get("type", "")).endswith("_tool_result")
        for k, v in obj.items():
            if k == "url" and is_result and isinstance(v, str):
                out.add(_norm(v))
            else:
                _collect_urls(v, out, is_result)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, out, inside_result)


def _json_from(texts: list[str]) -> dict:
    for t in reversed(texts):
        t = t.strip()
        if t.startswith("{"):
            try:
                return json.loads(t)
            except ValueError:
                pass
    joined = "".join(texts)
    m = re.search(r"\{.*\}", joined, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise SuggestError("Claude's answer couldn't be read. Try again.")


def _cost(usage) -> float:
    def g(name, obj=usage):
        return getattr(obj, name, 0) or 0
    stu = getattr(usage, "server_tool_use", None)
    searches = g("web_search_requests", stu) if stu else 0
    return (g("input_tokens") * PRICE_INPUT + g("cache_creation_input_tokens") * PRICE_CACHE_WRITE
            + g("cache_read_input_tokens") * PRICE_CACHE_READ + g("output_tokens") * PRICE_OUTPUT
            + searches * PRICE_SEARCH)


PLAIN_JSON = ("Reply with only a JSON object (no other text) with keys: articles (list of {url, title, "
              "outlet, why, quote}), video_leads (list of {platform, search, look_for, why}; platform is one of "
              + ", ".join(LEAD_PLATFORMS) + "), comment.")


def _ask(client, r: Request, structured: bool, cancelled: threading.Event | None):
    content = _user_content(r)
    if not structured:
        content = content + [{"type": "text", "text": PLAIN_JSON}]
    user = {"role": "user", "content": content}
    output_config = {"effort": EFFORT}
    if structured:
        output_config["format"] = {"type": "json_schema", "schema": SCHEMA}
    assistant: list = []
    cost = 0.0
    final = None
    for _ in range(5):  # long research can pause; carry on where it left off
        if cancelled and cancelled.is_set():
            raise SuggestError("Cancelled.")
        messages = [user] + ([{"role": "assistant", "content": assistant}] if assistant else [])
        with client.beta.messages.stream(
            model=MODEL, max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            thinking={"type": "adaptive"}, output_config=output_config,
            system=_system(r.profile), tools=TOOLS, messages=messages,
        ) as stream:
            final = stream.get_final_message()
        cost += _cost(final.usage)
        assistant = assistant + list(final.content)
        if final.stop_reason != "pause_turn":
            break
    return final, assistant, cost


def suggest(client, r: Request, cancelled: threading.Event | None = None) -> Suggestions:
    try:
        final, assistant, cost = _ask(client, r, True, cancelled)
    except Exception as e:  # structured answers not accepted alongside web search: ask for plain JSON
        msg = str(getattr(e, "message", e)).lower()
        if type(e).__name__ != "BadRequestError" or not any(w in msg for w in ("format", "output_config", "citation")):
            raise
        log.warning("Structured output refused (%s); asking for plain JSON instead", msg[:200])
        final, assistant, cost = _ask(client, r, False, cancelled)
    if final.stop_reason == "refusal":
        raise SuggestError("Claude declined this one. Try rewording the passage or adding a note.")
    if final.stop_reason == "max_tokens":
        raise SuggestError("Claude's answer ran too long. Try again, or ask for articles or videos only.")

    blocks = [b.model_dump() if hasattr(b, "model_dump") else b for b in assistant]
    seen: set[str] = set()
    _collect_urls(blocks, seen)
    searches = [b.get("input", {}).get("query", "") for b in blocks
                if b.get("type") == "server_tool_use" and b.get("name") == "web_search"]
    last_tool = max((i for i, b in enumerate(blocks) if str(b.get("type", "")).endswith("tool_result")), default=-1)
    texts = [b.get("text", "") for b in blocks[last_tool + 1:] if b.get("type") == "text"]
    data = _json_from(texts or [b.get("text", "") for b in blocks if b.get("type") == "text"])

    out = Suggestions(comment=data.get("comment", ""), searches=[s for s in searches if s], cost=cost)
    taken = {_norm(u) for u in r.attached + r.declined}
    for a in data.get("articles", []):
        url = a.get("url", "")
        n = _norm(url)
        if not url.startswith("http") or n not in seen or n in taken or video_platform_in_script(url):
            out.dropped += 1
            log.info("Dropped suggested link (not from a search result, already used, or a video): %s", url)
            continue
        taken.add(n)
        out.articles.append(ArticlePick(url, a.get("title", ""), a.get("outlet", ""), a.get("why", ""),
                                        a.get("quote", "")))
    for v in data.get("video_leads", []):
        if v.get("search"):
            out.leads.append(VideoLead(v.get("platform", "Any"), v["search"], v.get("look_for", ""), v.get("why", "")))
    if r.mode == "articles":
        out.leads = []
    if r.mode == "videos":
        out.articles = []
    return out
