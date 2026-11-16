"""A script project: the script's highlights, the links attached to each, and the files
each link produced. Saved under %APPDATA%\\Mediatheque\\projects, keyed to the script."""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import data_dir
from ..osutil import IS_WINDOWS, is_foreign_path, is_foreign_windows_path, path_name
from ..platforms import needs_lookup, video_platform_in_script
from .docx import Link, Paragraph, auto_highlights, fill_blanks, read_docx

PENDING, RUNNING, DONE, FAILED, SKIPPED = "pending", "running", "done", "failed", "skipped"

# Where each kind of file goes inside a video's project folder (the channel's standard layout).
SUBFOLDERS = {"video": "BRoll", "shot": "Screen Captures", "record": "Screen Records", "image": "Images"}


def guess_project_folder(script: str) -> str | None:
    """The folder holding BRoll / Screen Captures / Images, found by climbing up from the
    script (which normally lives in <project>\\Project Files)."""
    d = os.path.dirname(os.path.abspath(script))
    for _ in range(4):
        if any(os.path.isdir(os.path.join(d, sub)) for sub in SUBFOLDERS.values()):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    here = os.path.dirname(os.path.abspath(script))
    if os.path.basename(here).lower() == "project files":
        return os.path.dirname(here)
    return None


def find_moved_folder(old: str) -> str | None:
    """A project folder that isn't where it was saved (another drive, or another computer):
    look for a folder of the same name at the top of each drive, one folder down, and in the
    usual places in your home folder. Only folders that look like a project (a BRoll, Screen
    Captures, Images or Project Files folder inside) count."""
    if not old or os.path.isdir(old):
        return None
    name = path_name(old)
    if not name:
        return None
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        drives = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]
    else:
        try:
            drives = [os.path.join("/Volumes", v) for v in sorted(os.listdir("/Volumes"))]
        except OSError:
            drives = []
    places = drives + [home] + [os.path.join(home, d) for d in ("Desktop", "Documents", "Movies", "Downloads")]
    look = list(places)
    for drive in drives:  # one folder down on each drive, e.g. T7/Projects/Sweet Treats
        try:
            look += [e.path for e in os.scandir(drive) if e.is_dir() and not e.name.startswith((".", "$"))]
        except OSError:
            pass
    marks = list(SUBFOLDERS.values()) + ["Project Files"]
    for base in look:
        cand = os.path.join(base, name)
        if os.path.isdir(cand) and any(os.path.isdir(os.path.join(cand, m)) for m in marks):
            return cand
    return None


@dataclass
class LinkRef:
    url: str
    kind: str              # "video" (Downloader) or "article" (Screenshotter)
    auto: bool = False     # attached from a bracketed marker
    status: str = PENDING
    files: list[str] = field(default_factory=list)
    error: str = ""
    excluded: bool = False  # left out of Execute Screenshotter (the Skip button on its card)
    source: str = ""        # on-screen credit for a video: "Title | Channel" (YouTube), "@user"…
    # X and Reddit posts: how `kind` was decided. "" not yet, "checked" by looking the post up,
    # "unsure" the lookup failed (treated as an article, checked again), "manual" your choice.
    sorted_by: str = ""
    sort_note: str = ""     # why the lookup failed

    @property
    def needs_check(self) -> bool:
        return (needs_lookup(self.url) and self.sorted_by in ("", "unsure") and not self.excluded
                and not self.files and self.status in (PENDING, FAILED, SKIPPED))


@dataclass
class Highlight:
    id: int
    para: int
    start: int
    end: int
    text: str
    links: list[LinkRef] = field(default_factory=list)
    auto: bool = False
    label: str = ""   # "7", or "7a" for one added later after 7. Never changes once given.
    note: str = ""    # the editor's own note on this passage
    images: list[str] = field(default_factory=list)  # image files attached by hand
    leads: list[dict] = field(default_factory=list)   # video leads from Suggest: platform, search, look_for, why
    declined: list[str] = field(default_factory=list)  # suggested articles the editor skipped (Suggest learns from these)
    pointers: str = ""  # what the editor last told Suggest for this highlight (offered again next time)

    @property
    def prefix(self) -> str:
        """File name prefix for everything this highlight downloads: "7_"."""
        return f"{self.label}_" if self.label else ""


def same_page(url: str) -> str:
    """A link reduced to its page: no quote fragment, scheme, "www." or trailing slash."""
    from urllib.parse import urldefrag, urlsplit
    p = urlsplit(urldefrag(url.strip())[0])
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/") + (f"?{p.query}" if p.query else "")


def link_kind(url: str) -> str:
    return "video" if video_platform_in_script(url) else "article"


def _script_key(script: str) -> str:
    """The script's path as Windows would compare it: projects saved on Windows keep their
    names when opened on a Mac (until Locate Script points them at the Mac's copy)."""
    if is_foreign_windows_path(script):
        return script.replace("/", "\\").lower()
    if IS_WINDOWS and script.startswith("/"):  # saved on a Mac: compared as the Mac would
        return script
    return os.path.normcase(os.path.abspath(script))


def script_stem(script: str) -> str:
    return os.path.splitext(path_name(script))[0]


def project_path(script: str) -> Path:
    d = data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(_script_key(script).encode()).hexdigest()[:10]
    stem = re.sub(r"[^\w\- ]+", "", script_stem(script))[:60].strip() or "script"
    return d / f"{stem} [{key}].json"


class Project:
    def __init__(self, script: str, removed: set[int] | None = None):
        """`removed`: paragraphs to leave out (research notes after the script and so on).
        Only used when the project is new; a saved project remembers its own."""
        self.script = script if is_foreign_path(script) else os.path.abspath(script)
        self.path = project_path(self.script)
        # The script's text is also kept inside the project, so a moved or deleted .docx
        # never loses the work: the project opens from that copy (Locate Script reconnects it).
        self.script_missing = not os.path.exists(self.script)
        if self.script_missing:
            snapshot = _read_snapshot(self.path)
            if snapshot is None:
                raise FileNotFoundError(f"Can't find the script: {self.script}")
            self.paragraphs: list[Paragraph] = snapshot
        else:
            self.paragraphs = read_docx(self.script)
        self.highlights: list[Highlight] = []
        self.removed: set[int] = set()
        self.folders: dict[str, str] = {}  # {"project": the video's project folder}
        self._next_id = 1
        self._retired: set[str] = set()  # labels of removed highlights, never handed out again
        self.edits: dict[int, dict] = {}  # paragraph -> {"original": text in the .docx, "text": edited}
        self.saved_at = 0.0
        self.restored = self._load()
        if not self.restored:
            self.removed = set(removed or ())
            for h in auto_highlights(self.paragraphs):
                if h.para in self.removed:
                    continue
                self.add(h.para, h.start, h.end, [LinkRef(u, link_kind(u), auto=True) for u in h.urls], auto=True)
            self.renumber()
            self.save()
        elif any(not h.label for h in self.visible):
            self._backfill_labels()
            self.save()
        elif not self.locked:
            self.renumber()

    # -- highlights ------------------------------------------------------------------

    def add(self, para: int, start: int, end: int, links: list[LinkRef] | None = None,
            auto: bool = False) -> Highlight:
        h = Highlight(self._next_id, para, start, end, self.paragraphs[para].text[start:end], links or [], auto)
        self._next_id += 1
        self.highlights.append(h)
        self.highlights.sort(key=lambda x: (x.para, x.start))
        if self.locked:
            h.label = self._label_after(h)
        else:
            self.renumber()
        return h

    @property
    def locked(self) -> bool:
        """Numbers are fixed once downloads or screenshots have run: they're on files by then.
        Until that point, highlights simply renumber 1, 2, 3… in script order."""
        return any(r.status != PENDING or r.files for h in self.highlights for r in h.links)

    def renumber(self) -> None:
        if self.locked:
            return
        for i, h in enumerate(self.visible, 1):
            h.label = str(i)
        for h in self.highlights:
            if h.para in self.removed:
                h.label = ""  # gets a number if its text is brought back
        self._retired.clear()

    def adjust(self, hid: int, para: int, start: int, end: int) -> Highlight | None:
        """Move a highlight onto new text, keeping its links, files, images, note and number."""
        h = self.get(hid)
        if not h:
            return None
        h.para, h.start, h.end = para, start, end
        h.text = self.paragraphs[para].text[start:end]
        self.highlights.sort(key=lambda x: (x.para, x.start))
        self.renumber()
        return h

    def combine(self, hids: list[int], para: int, start: int, end: int) -> Highlight | None:
        """Merge several highlights into the first of them (in script order), which keeps its
        number. Everything attached to the others (links and their files, images, leads,
        skipped suggestions, notes) moves onto it, and it spans all of their text."""
        hs = sorted((h for h in self.highlights if h.id in hids), key=lambda x: (x.para, x.start))
        if len(hs) < 2:
            return None
        keep, rest = hs[0], hs[1:]
        start = min([start] + [h.start for h in hs])
        end = max([end] + [h.end for h in hs])
        urls = {r.url for r in keep.links}
        for h in rest:
            for r in h.links:
                if r.url not in urls:
                    keep.links.append(r)
                    urls.add(r.url)
            keep.images += [i for i in h.images if i not in keep.images]
            keep.leads += [x for x in h.leads if x not in keep.leads]
            keep.declined += [u for u in h.declined if u not in keep.declined]
            if h.note and h.note not in keep.note:
                keep.note = f"{keep.note}\n\n{h.note}".strip()
            if h.pointers and h.pointers not in keep.pointers:
                keep.pointers = f"{keep.pointers}\n{h.pointers}".strip()
            keep.auto = keep.auto and h.auto
        gone = {h.id for h in rest}
        if self.locked:  # their numbers may already be on files: never hand them out again
            self._retired |= {h.label for h in rest if h.label}
        self.highlights = [h for h in self.highlights if h.id not in gone]
        keep.para, keep.start, keep.end = para, start, end
        keep.text = self.paragraphs[para].text[start:end]
        self.highlights.sort(key=lambda x: (x.para, x.start))
        self.renumber()
        return keep

    def _label_after(self, h: Highlight) -> str:
        """A new highlight takes the number of the one before it plus a letter: 7a, 7b…
        so nothing already numbered (or already named on disk) has to change."""
        i = self.highlights.index(h)
        before = next((x for x in reversed(self.highlights[:i]) if x.label), None)
        base = re.match(r"\d+", before.label).group(0) if before else "0"
        taken = {x.label for x in self.highlights} | self._retired
        for letter in "abcdefghijklmnopqrstuvwxyz":
            if base + letter not in taken:
                return base + letter
        return f"{base}z{len(taken)}"

    def _backfill_labels(self) -> None:
        """Projects saved before numbering existed: number them in script order."""
        if not any(h.label for h in self.highlights):
            for i, h in enumerate(self.highlights, 1):
                h.label = str(i)
            return
        for h in self.highlights:
            if not h.label:
                h.label = self._label_after(h)

    def remove(self, hid: int) -> None:
        if self.locked:
            self._retired |= {h.label for h in self.highlights if h.id == hid and h.label}
        self.highlights = [h for h in self.highlights if h.id != hid]
        self.renumber()

    # -- moving the script ----------------------------------------------------------------

    def relink(self, new_script: str) -> "Project":
        """Point the project at the script's new location (moved or renamed .docx).
        Everything carries over; highlights re-attach to their text in the file."""
        new_script = os.path.abspath(new_script)
        old_path = self.path
        self.script = new_script
        self.path = project_path(new_script)
        self.script_missing = False
        self.save()
        if os.path.normcase(str(old_path)) != os.path.normcase(str(self.path)):
            try:
                old_path.unlink()
            except OSError:
                pass
        return Project(new_script)

    def relocate(self, old_root: str, new_root: str) -> int:
        """The project folder moved (another drive, or another computer): point every
        downloaded file, screenshot, recording and image at the new folder. Works for paths
        saved on Windows and opened on a Mac too. Returns how many paths changed."""
        old = old_root.replace("\\", "/").rstrip("/").lower()
        changed = 0

        def moved(path: str) -> str:
            nonlocal changed
            p = path.replace("\\", "/")
            if not p.lower().startswith(old + "/"):
                return path
            changed += 1
            return os.path.join(new_root, *p[len(old) + 1:].split("/"))

        for h in self.highlights:
            for ref in h.links:
                ref.files = [moved(f) for f in ref.files]
            h.images = [moved(i) for i in h.images]
        self.folders["project"] = new_root
        return changed

    # -- editing the script's text ---------------------------------------------------------

    def edit_paragraph(self, para: int, new_text: str) -> None:
        """Change a paragraph's text (the .docx isn't touched). Links and highlights in it
        move with the words around them."""
        old = self.paragraphs[para].text
        if new_text == old:
            return
        original = self.edits.get(para, {}).get("original", old)
        start_at, end_at = _position_maps(old, new_text)
        self._apply_text(para, new_text, start_at, end_at)
        for h in self.highlights:
            if h.para == para:
                h.start, h.end = start_at[h.start], end_at[h.end]
                if h.end <= h.start:  # its words were deleted: keep a sliver so it isn't lost
                    h.end = min(len(new_text), h.start + 1)
                h.text = new_text[h.start:h.end]
        if new_text == original:
            self.edits.pop(para, None)
        else:
            self.edits[para] = {"original": original, "text": new_text}

    def _apply_text(self, para: int, new_text: str, start_at=None, end_at=None) -> None:
        p = self.paragraphs[para]
        if start_at is None:
            start_at, end_at = _position_maps(p.text, new_text)
        for link in p.links:
            link.start, link.end = start_at[link.start], end_at[link.end]
        p.links = [l for l in p.links if l.end > l.start]
        p.text = new_text

    # -- folders ------------------------------------------------------------------------

    @property
    def project_folder(self) -> str:
        return self.folders.get("project", "")

    def dest(self, kind: str) -> str:
        """Where this kind of file goes: <project folder>\\BRoll and so on."""
        return os.path.join(self.project_folder, SUBFOLDERS[kind])

    # -- left-out paragraphs -------------------------------------------------------------

    @property
    def visible(self) -> list[Highlight]:
        """Highlights in the parts of the script that are kept."""
        return [h for h in self.highlights if h.para not in self.removed]

    def set_removed(self, removed: set[int]) -> None:
        """Change which paragraphs are left out. Text brought back gets its automatic
        highlights (numbered 7a, 7b… if downloads have already run, so no file's number changes)."""
        brought_back = self.removed - set(removed)
        self.removed = set(removed)
        for ah in auto_highlights(self.paragraphs):
            if ah.para not in brought_back:
                continue
            if any(h.para == ah.para and h.start < ah.end and ah.start < h.end for h in self.highlights):
                continue  # it's still there from before
            self.add(ah.para, ah.start, ah.end, [LinkRef(u, link_kind(u), auto=True) for u in ah.urls], auto=True)
        self.renumber()

    def _fill_blanks(self, removed: set[int]) -> set[int]:
        return fill_blanks(self.paragraphs, removed)

    def get(self, hid: int) -> Highlight | None:
        return next((h for h in self.highlights if h.id == hid), None)

    def attached_urls(self) -> set[str]:
        return {l.url for h in self.visible for l in h.links}

    def added_sources(self) -> list[tuple[Highlight, LinkRef]]:
        """Articles attached to highlights that the script itself doesn't link to anywhere
        (added with Add Links… or Suggest), in script order, each page once. Links in
        left-out paragraphs (research notes) count as being in the script."""
        in_script = {same_page(l.url) for q in self.paragraphs for l in q.links}
        out, seen = [], set()
        for h in self.visible:
            for ref in h.links:
                page = same_page(ref.url)
                if ref.kind == "article" and page not in in_script and page not in seen:
                    seen.add(page)
                    out.append((h, ref))
        return out

    def embedded_links(self, para: int, start: int, end: int) -> list[str]:
        """Links embedded in the script within a selection (in order, no repeats)."""
        out: list[str] = []
        for link in self.paragraphs[para].links:
            if link.start < end and link.end > start and link.url not in out:
                out.append(link.url)
        return out

    def refs(self, kind: str | None = None):
        for h in self.visible:
            for ref in h.links:
                if kind is None or ref.kind == kind:
                    yield h, ref

    # -- persistence -------------------------------------------------------------------

    def save(self) -> None:
        data = {
            "script": self.script,
            "next_id": self._next_id,
            "retired_labels": sorted(self._retired),
            "folders": self.folders,
            "para_count": len(self.paragraphs),
            "removed": sorted(self.removed),
            "edits": {str(k): v for k, v in self.edits.items()},
            # Text of left-out paragraphs, to find them again if the script is re-exported with edits.
            "removed_texts": [self.paragraphs[i].text for i in sorted(self.removed) if self.paragraphs[i].text.strip()],
            "highlights": [asdict(h) for h in self.highlights],
        }
        data["updated"] = time.time()
        data["snapshot"] = [{"text": q.text, "heading": q.heading,
                             "links": [[l.start, l.end, l.url] for l in q.links]} for q in self.paragraphs]
        _backup(self.path)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self.saved_at = data["updated"]

    def _load(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        self._next_id = data.get("next_id", 1)
        self.saved_at = data.get("updated", 0.0)
        self._retired = set(data.get("retired_labels", []))
        self.folders = dict(data.get("folders", {}))
        if "project" not in self.folders:  # saved before project folders: work it out
            old_video = self.folders.get("video", "")
            if os.path.basename(old_video).lower() == SUBFOLDERS["video"].lower():
                self.folders = {"project": os.path.dirname(old_video)}
            elif guess := guess_project_folder(self.script):
                self.folders = {"project": guess}
            else:
                self.folders = {}
        # Your text edits, re-applied if that paragraph is still as it was in the .docx.
        for k, e in (data.get("edits") or {}).items():
            i = int(k)
            if self.script_missing:  # the saved copy of the text already has the edits in it
                self.edits[i] = e
                continue
            if not (i < len(self.paragraphs) and self.paragraphs[i].text == e.get("original")):
                i = next((j for j, q in enumerate(self.paragraphs) if q.text == e.get("original")), -1)
            if i >= 0:
                self._apply_text(i, e["text"])
                self.edits[i] = e
        removed = set(data.get("removed", []))
        if data.get("para_count") == len(self.paragraphs):
            self.removed = {i for i in removed if i < len(self.paragraphs)}
        else:  # the script changed: find the left-out paragraphs by their text
            texts = set(data.get("removed_texts", []))
            self.removed = self._fill_blanks({i for i, p in enumerate(self.paragraphs) if p.text.strip() and p.text in texts})
        for raw in data.get("highlights", []):
            links = [LinkRef(**l) for l in raw.pop("links", [])]
            for l in links:  # not run yet: sort it again (e.g. Facebook videos vs posts)
                if l.status == PENDING and not l.files and l.sorted_by not in ("checked", "manual"):
                    l.kind = link_kind(l.url)
            h = Highlight(links=links, **raw)
            if self._reanchor(h):
                if h.links and any(l.status == RUNNING for l in h.links):
                    for l in h.links:
                        if l.status == RUNNING:
                            l.status = PENDING
                self.highlights.append(h)
        self.highlights.sort(key=lambda x: (x.para, x.start))
        return True

    def _reanchor(self, h: Highlight) -> bool:
        """Keep a highlight on its text even if the script was re-exported with edits."""
        paras = self.paragraphs
        if h.para < len(paras) and paras[h.para].text[h.start:h.end] == h.text:
            return True
        for pi, p in enumerate(paras):
            i = p.text.find(h.text)
            if i >= 0:
                h.para, h.start, h.end = pi, i, i + len(h.text)
                return True
        return False  # the passage is gone from the script


def _position_maps(old: str, new: str) -> tuple[list[int], list[int]]:
    """Where each position in `old` lands in `new`. Two maps: for the start of a span (text
    inserted right before it pushes it along) and for the end (text inserted right after it
    doesn't stretch it). Positions inside rewritten text snap to the nearest unchanged edge."""
    starts: list[int | None] = [None] * (len(old) + 1)
    ends: list[int | None] = [None] * (len(old) + 1)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            continue
        for k in range(i1, i2 + 1):
            starts[k] = j1 + (k - i1)       # later blocks win: after anything inserted here
            if ends[k] is None:
                ends[k] = j1 + (k - i1)     # earlier blocks win: before anything inserted here
    nxt = len(new)
    for k in range(len(old), -1, -1):       # starts inside changed text: next unchanged spot
        if starts[k] is None:
            starts[k] = nxt
        else:
            nxt = starts[k]
    prev = 0
    for k in range(len(old) + 1):           # ends inside changed text: previous unchanged spot
        if ends[k] is None:
            ends[k] = prev
        else:
            prev = ends[k]
    if old and not new:
        starts, ends = [0] * (len(old) + 1), [0] * (len(old) + 1)
    return starts, ends


# -- the list of projects, and backups ------------------------------------------------------

BACKUP_EVERY = 10 * 60   # seconds between automatic backups of a project
BACKUPS_KEPT = 15


def _backup_dir() -> Path:
    d = data_dir() / "projects" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup(path: Path) -> None:
    """Keep earlier versions of a project: one copy at most every 10 minutes, latest 15."""
    if not path.exists():
        return
    existing = list_backups(path)
    if existing and time.time() - existing[0].stat().st_mtime < BACKUP_EVERY:
        return
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    try:
        shutil.copy2(path, _backup_dir() / f"{path.stem} @ {stamp}.json")
    except OSError:
        return
    for old in list_backups(path)[BACKUPS_KEPT:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups(path: Path) -> list[Path]:
    """Backups of one project, newest first."""
    prefix = f"{path.stem} @ "
    return sorted((b for b in _backup_dir().glob("*.json") if b.name.startswith(prefix)),
                  key=lambda b: b.stat().st_mtime, reverse=True)


def restore_backup(backup: Path, path: Path) -> None:
    """Put an earlier version back (the current one is backed up first)."""
    if path.exists():
        shutil.copy2(path, _backup_dir() / f"{path.stem} @ {time.strftime('%Y-%m-%d %H%M%S')} (before restore).json")
    shutil.copy2(backup, path)


def _read_snapshot(path: Path) -> list[Paragraph] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    snap = data.get("snapshot")
    if not snap:
        return None
    return [Paragraph(q["text"], q.get("heading", 0), [Link(a, b, u) for a, b, u in q.get("links", [])])
            for q in snap]


@dataclass
class ProjectInfo:
    path: Path
    script: str
    name: str
    folder: str
    updated: float
    highlights: int
    links_done: int
    links_total: int
    images: int

    @property
    def script_missing(self) -> bool:
        return not os.path.exists(self.script)


def list_projects() -> list[ProjectInfo]:
    """Every saved script project, most recently worked on first."""
    out = []
    for f in (data_dir() / "projects").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        script = d.get("script", "")
        removed = set(d.get("removed", []))
        hs = [h for h in d.get("highlights", []) if h.get("para") not in removed]
        links = [l for h in hs for l in h.get("links", [])]
        out.append(ProjectInfo(
            path=f, script=script, name=script_stem(script) or f.stem,
            folder=(d.get("folders") or {}).get("project", ""),
            updated=d.get("updated") or f.stat().st_mtime,
            highlights=len(hs), links_done=sum(l.get("status") == DONE for l in links),
            links_total=len(links), images=sum(len(h.get("images", [])) for h in hs)))
    return sorted(out, key=lambda i: i.updated, reverse=True)
