"""Pull links out of script documents and work out what each one asks to capture."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from urllib.parse import unquote, urldefrag

from ..platforms import video_platform_in_script


@dataclass(frozen=True)
class Fragment:
    """A Chrome "text fragment" (#:~:text=...): the exact quote a link points at."""
    start: str
    end: str = ""
    prefix: str = ""
    suffix: str = ""

    @property
    def preview(self) -> str:
        text = f"{self.start} … {self.end}" if self.end else self.start
        return text if len(text) <= 90 else text[:87].rstrip() + "…"


@dataclass
class Capture:
    url: str                      # the page to load (fragment removed)
    fragments: list[Fragment] = field(default_factory=list)  # empty = capture the headline area

    @property
    def quote(self) -> Fragment | None:
        return self.fragments[0] if self.fragments else None


def parse_fragments(fragment: str) -> list[Fragment]:
    """Parse ':~:text=[prefix-,]start[,end][,-suffix]&text=...'."""
    if ":~:" not in fragment:
        return []
    out = []
    for directive in fragment.split(":~:", 1)[1].split("&"):
        if not directive.startswith("text="):
            continue
        parts = directive[5:].split(",")
        prefix = suffix = ""
        if parts and parts[0].endswith("-"):
            prefix = parts.pop(0)[:-1]
        if parts and parts[-1].startswith("-"):
            suffix = parts.pop()[1:]
        if not parts:
            continue
        start, end = parts[0], (parts[1] if len(parts) > 1 else "")
        out.append(Fragment(*(unquote(p).strip() for p in (start, end, prefix, suffix))))
    return out


def to_capture(url: str) -> Capture:
    base, frag = urldefrag(url.strip())
    return Capture(base, parse_fragments(frag))


def is_video_link(url: str) -> bool:
    """Links the Downloader handles (YouTube, TikTok, Instagram)."""
    return video_platform_in_script(url) is not None


# -- Reading scripts -----------------------------------------------------------------

def links_from_pdf(path: str) -> list[str]:
    from pypdf import PdfReader

    links = []
    for page in PdfReader(path).pages:
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A") or {}
            if uri := action.get("/URI"):
                links.append(str(uri))
    return links


def links_from_docx(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "replace")
        doc = z.read("word/document.xml").decode("utf-8", "replace")
    targets = {}
    for rel in re.findall(r"<Relationship\b[^>]*>", rels):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', rel))
        if attrs.get("TargetMode") == "External" and "Id" in attrs:
            targets[attrs["Id"]] = attrs.get("Target", "")
    order = re.findall(r'<w:hyperlink[^>]*?r:id="([^"]+)"', doc)
    links = [_unescape(targets[i]) for i in order if i in targets]
    # Field-code hyperlinks (HYPERLINK "url") from some exporters.
    links += re.findall(r'HYPERLINK\s+&quot;(https?://[^&]+)&quot;', doc)
    return links


def links_from_text(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"']+", text)


def links_from_file(path: str) -> list[str]:
    low = path.lower()
    if low.endswith(".pdf"):
        return links_from_pdf(path)
    if low.endswith(".docx"):
        return links_from_docx(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        return links_from_text(f.read())


def _unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")


def plan(links: list[str]) -> tuple[list[Capture], list[str]]:
    """Split links into page captures (deduplicated, same-page links kept together so each page
    loads once) and video links for the Downloader."""
    videos: list[str] = []
    seen: set[str] = set()
    by_page: dict[str, list[Capture]] = {}
    for link in links:
        if link in seen or not link.lower().startswith(("http://", "https://")):
            continue
        seen.add(link)
        if is_video_link(link):
            videos.append(link)
            continue
        cap = to_capture(link)
        group = by_page.setdefault(cap.url, [])
        if cap.fragments:
            for frag in cap.fragments:
                group.append(Capture(cap.url, [frag]))
        elif not any(not c.fragments for c in group):
            group.append(cap)
    return [c for group in by_page.values() for c in group], videos
