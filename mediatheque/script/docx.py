"""Read a script exported from Google Docs as .docx: paragraphs, headings and links,
with each link's exact position in its paragraph's text."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class Link:
    start: int   # offsets into Paragraph.text
    end: int
    url: str

    def text(self, para: "Paragraph") -> str:
        return para.text[self.start:self.end]


@dataclass
class Paragraph:
    text: str
    heading: int = 0  # 0 = body text, 1-6 = heading level
    links: list[Link] = field(default_factory=list)


def read_docx(path: str) -> list[Paragraph]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
        rels_xml = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels_xml
               if r.get("TargetMode") == "External"}

    paragraphs: list[Paragraph] = []
    body = root.find(f"{W}body")
    for p in body.iter(f"{W}p"):
        para = Paragraph(text="")
        style = p.find(f"{W}pPr/{W}pStyle")
        if style is not None:
            m = re.match(r"(?:Heading|heading)\s*(\d)", style.get(f"{W}val", ""))
            if m:
                para.heading = int(m.group(1))
            elif style.get(f"{W}val") in ("Title",):
                para.heading = 1
        _collect(p, para, targets, None)
        paragraphs.append(para)
    return paragraphs


def _collect(el, para: Paragraph, targets: dict, url: str | None) -> None:
    """Walk a paragraph in document order, appending text and recording link spans."""
    for child in el:
        tag = child.tag
        if tag == f"{W}hyperlink":
            target = targets.get(child.get(f"{R}id"))
            start = len(para.text)
            _collect(child, para, targets, target)
            if target and len(para.text) > start:
                para.links.append(Link(start, len(para.text), target))
        elif tag == f"{W}r":
            for part in child:
                if part.tag == f"{W}t":
                    para.text += part.text or ""
                elif part.tag == f"{W}tab":
                    para.text += "\t"
                elif part.tag in (f"{W}br", f"{W}cr"):
                    para.text += "\n"
        elif tag in (f"{W}pPr",):
            continue
        else:
            _collect(child, para, targets, url)  # smart tags, fields, insertions…


# -- Automatic highlights from bracketed links -----------------------------------------

@dataclass
class AutoHighlight:
    para: int
    start: int
    end: int
    urls: list[str]


# Sentence ends. Not "…": the scripts use it mid-sentence ("released with… Poptarts").
_SENTENCE_END = re.compile(r"[.?!]+[\"'”’)\]]*\s+")
_MARKER = re.compile(r"\[[^\]]*\]")
_EDGE_PUNCT = " \t\n.?!\"'”’)"


def _sentence_start(text: str, floor: int, end: int) -> int:
    """Start of the sentence that ends at `end` (not reaching back past `floor`)."""
    core = text[floor:end]
    search_in = core[:-1] if core and core[-1] in ".?!" else core  # the stop that ends it
    start = floor
    for m in _SENTENCE_END.finditer(search_in):
        start = floor + m.end()
    while start < end and text[start] in " \t\n,;:":
        start += 1
    return start


def _bracket_groups(para: Paragraph) -> list[tuple[int, int, list[str]]]:
    """Bracketed links as (start of "[", end after "]", urls), adjacent markers grouped."""
    t = para.text
    groups: list[tuple[int, int, list[str]]] = []
    for link in para.links:
        lb, rb = link.start - 1, link.end
        if lb < 0 or t[lb] != "[" or rb >= len(t) or t[rb] != "]":
            continue
        if groups and not t[groups[-1][1]:lb].strip():
            groups[-1] = (groups[-1][0], rb + 1, groups[-1][2] + [link.url])
        else:
            groups.append((lb, rb + 1, [link.url]))
    return groups


def _has_words(text: str) -> bool:
    """Script text, as opposed to markers or a production note like "(signoff)"."""
    text = re.sub(r"\([^)]*\)", "", _MARKER.sub("", text))
    return any(c.isalpha() for c in text)


def auto_highlights(paragraphs: list[Paragraph]) -> list[AutoHighlight]:
    """Every bracketed link ("[source]", "[video]", …) attaches to the text before it:
    from the start of that sentence, or from just after the previous marker if that's
    closer, up to the marker. Markers right next to each other share one highlight;
    markers that open a paragraph cite the end of the previous paragraph."""
    out: list[AutoHighlight] = []
    last_text_para = -1
    for pi, para in enumerate(paragraphs):
        t = para.text
        floor = 0  # text before this belongs to an earlier marker
        for g_start, g_end, urls in _bracket_groups(para):
            end = floor + len(t[floor:g_start].rstrip())
            start = _sentence_start(t, floor, end)
            if end > start:
                out.append(AutoHighlight(pi, start, end, urls))
            elif floor == 0 and last_text_para >= 0:
                _attach_to_paragraph_end(out, paragraphs, last_text_para, urls)
            floor = g_end
        if _has_words(t):
            last_text_para = pi
    return out


def _attach_to_paragraph_end(out: list[AutoHighlight], paragraphs: list[Paragraph], pi: int,
                             urls: list[str]) -> None:
    """Add links to the highlight that ends paragraph `pi`, or highlight its last sentence."""
    t = paragraphs[pi].text
    for h in reversed(out):
        if h.para == pi and not _MARKER.sub("", t[h.end:]).strip(_EDGE_PUNCT):
            h.urls.extend(urls)
            return
    end = len(_MARKER.sub("", t).rstrip()) if not _MARKER.search(t) else len(t.rstrip())
    start = _sentence_start(t, 0, end)
    if end > start:
        out.append(AutoHighlight(pi, start, end, list(urls)))


# -- Where the script ends -------------------------------------------------------------

_SIGN_OFF = re.compile(r"^\s*\(?\s*sign\s*-?\s*off\s*\)?\s*\.?\s*$", re.I)


def suggest_removed(paragraphs: list[Paragraph]) -> set[int]:
    """Guess the research notes tacked on after the script: everything after a "(sign off)"
    line, or after a long run of blank lines near the end. Paragraphs right after the
    sign-off that hold only [source] markers still belong to the script."""
    n = len(paragraphs)
    end = None
    for i, p in enumerate(paragraphs):
        if _SIGN_OFF.match(p.text):
            end = i
    if end is None:
        blank_run = 0
        for i in range(int(n * 0.5), n):
            if paragraphs[i].text.strip():
                if blank_run >= 4:
                    end = i - blank_run - 1
                    break
                blank_run = 0
            else:
                blank_run += 1
    if end is None:
        return set()
    # A line of nothing but markers after the sign-off still cites the script.
    while end + 1 < n:
        j = end + 1
        while j < n and not paragraphs[j].text.strip():
            j += 1
        if j < n and paragraphs[j].links and not _has_words(paragraphs[j].text):
            end = j
        else:
            break
    return set(range(end + 1, n))


def fill_blanks(paragraphs: list[Paragraph], removed: set[int]) -> set[int]:
    """Blank lines go with what's around them: a blank paragraph is left out if the text
    before it or the text after it is left out (so no empty gap is left behind)."""
    out = set(removed)
    n = len(paragraphs)
    has_text = [bool(p.text.strip()) for p in paragraphs]
    prev_out = False
    for i in range(n):
        if has_text[i]:
            prev_out = i in removed
            continue
        j = i + 1
        while j < n and not has_text[j]:
            j += 1
        next_out = j < n and j in removed
        if prev_out or next_out:
            out.add(i)
    return out
