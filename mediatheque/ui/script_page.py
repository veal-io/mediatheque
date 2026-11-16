"""Script page: import a script (.docx), highlight passages, attach video and article links
to each, then run the Downloader and the Screenshotter over all of them. Every file that
comes back is listed under its highlight, ready to copy (or drag) into Premiere."""
from __future__ import annotations

import os
import re
import threading
import time
from html import unescape as html_unescape
from urllib.parse import urldefrag

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QDrag, QFont, QFontMetrics, QGuiApplication, QImage, QKeySequence, QPainter, QShortcut, QPalette, QPixmap, QPolygon,
    QTextBlockFormat, QTextCharFormat,
    QTextCursor, QTextFormat,
)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTabWidget,
    QTextBrowser, QTextEdit, QToolTip, QVBoxLayout, QWidget,
)

from ..osutil import FILE_MANAGER, MOD_KEY, path_name
from ..config import Settings
from ..engine import DownloadWorker
from ..platforms import PLATFORMS, needs_lookup, platform_for
from ..screenshots.links import links_from_text, to_capture
from ..screenshots.session import SessionWorker
from ..script.docx import read_docx, suggest_removed
from ..script.images import IMAGE_EXTS, bring_in, download, is_image, unique_in
from ..script.previews import PreviewService
from ..script.project import (
    DONE, FAILED, PENDING, RUNNING, SUBFOLDERS, SKIPPED, Highlight, LinkRef, Project, find_moved_folder,
    guess_project_folder,
    link_kind, list_projects, project_path,
)
from ..script import suggest as sg
from .projects_dialog import ProjectsDialog
from .suggest_ui import PointersDialog, SuggestDialog, SuggestWorker, ensure_key
from .theme import ui_font
from .trim_dialog import TrimDialog
from .widgets import ElidedLabel, FitScrollArea, FolderCombo, FolderStore, JobRow, show_in_explorer

GUTTER = 56  # left margin for highlight numbers, in pixels
HIGHLIGHT = QColor("#fff27a")
HIGHLIGHT_ALT = QColor("#ffe2a8")  # every other highlight, so neighbours don't merge into one band
SELECTED = QColor("#9fd0ff")  # the highlight whose card is selected
STATUS_TEXT = {PENDING: "Not yet", RUNNING: "Working…", DONE: "Done", FAILED: "Failed", SKIPPED: "Skipped"}
STATUS_COLOR = {PENDING: "#606060", RUNNING: "#000080", DONE: "#008000", FAILED: "#c00000", SKIPPED: "#806000"}


def copy_files(paths: list[str]) -> None:
    """Put files on the clipboard the way Explorer's Ctrl+C does, for pasting into Premiere."""
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    md.setData("Preferred DropEffect", QByteArray(b"\x01\x00\x00\x00"))  # copy, not move
    QGuiApplication.clipboard().setMimeData(md)


def _handle_from_files(url: str, files: list[str]) -> str:
    """TikTok and Instagram files are named after the creator: "7_@user (2).mp4" -> "@user"."""
    plat = platform_for(url)
    if not plat or not plat.rename or not files:
        return ""
    stem = os.path.splitext(path_name(files[0]))[0]
    stem = re.sub(r"^\d+[a-z]*_", "", stem)
    return "@" + re.sub(r" \(\d+\)$", "", stem).lstrip("@")


def _credit_from_name(url: str, files: list[str]) -> str:
    """YouTube files named "7_Title - Channel.mp4" carry their own credit: "Title | Channel"."""
    plat = platform_for(url)
    if not plat or not plat.source or not files:
        return ""
    stem = re.sub(r"^\d+[a-z]*_", "", os.path.splitext(path_name(files[0]))[0])
    stem = re.sub(r" \(\d+\)$", "", stem)
    if " - " not in stem or re.search(r"\[[\w-]{6,}\]$", stem):  # older "Title [id]" names have no channel
        return ""
    title, channel = stem.rsplit(" - ", 1)
    return f"{title} | {channel}"


def start_file_drag(source: QWidget, paths: list[str]) -> None:
    """Drag files out to another program (Premiere's Project panel, a bin, the timeline,
    Explorer). Offers copy *and* link, as Explorer does: Premiere imports by linking to the
    media, and can refuse a drag that only offers copying. Never offers move, so a drop into
    Explorer can't take the file out of the project folder."""
    found = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not found:
        _page_status(source, "That file isn't where the project says it is (it may still point at the other "
                             "computer). Use Locate Script… or Project folder to reconnect the project.")
        return
    paths = found
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    drag = QDrag(source)
    drag.setMimeData(md)
    drag.setPixmap(_drag_badge(source, paths))
    drag.exec(Qt.CopyAction | Qt.LinkAction, Qt.CopyAction)


def _drag_badge(source: QWidget, paths: list[str]) -> QPixmap:
    """The little label that follows the cursor while dragging files."""
    font = ui_font(9)
    fm = QFontMetrics(font)
    text = path_name(paths[0]) if len(paths) == 1 else f"{len(paths)} files"
    text = fm.elidedText(text, Qt.ElideMiddle, 360)
    dpr = source.devicePixelRatioF()
    w, h = fm.horizontalAdvance(text) + 18, fm.height() + 8
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#ffffe1"))
    painter = QPainter(pm)
    painter.setPen(QColor("#000000"))
    painter.drawRect(0, 0, w - 1, h - 1)
    painter.setFont(font)
    painter.drawText(QRect(9, 0, w - 18, h), Qt.AlignVCenter | Qt.AlignLeft, text)
    painter.end()
    return pm


def _page_status(w: QWidget, text: str) -> None:
    while w is not None and not isinstance(w, ScriptPage):
        w = w.parent()
    if w:
        w.status.emit(text)


class DragAllButton(QPushButton):
    """Press and drag to take every file of a highlight into Premiere in one go."""

    def __init__(self, paths: list[str], parent=None):
        super().__init__(f"Drag All ({len(paths)})", parent)
        self.paths = paths
        self._press: QPoint | None = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag this button onto Premiere's Project panel, a bin or the timeline "
                        "to import every file in this highlight.")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._press = e.position().toPoint()
        self.setDown(True)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is not None and (e.position().toPoint() - self._press).manhattanLength() >= 6:
            self._press = None
            self.setDown(False)
            start_file_drag(self, self.paths)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self.setDown(False)
        if self._press is not None:  # clicked rather than dragged
            _page_status(self, "Drag the Drag All button into Premiere (press, hold and move) to import the files.")
        self._press = None


def read_drop(md: QMimeData) -> dict:
    """What was dropped: local files, web image addresses, or raw image data."""
    files, urls = [], []
    for u in md.urls():
        if u.isLocalFile():
            files.append(u.toLocalFile())
        elif u.scheme() in ("http", "https"):
            urls.append(u.toString())
    if not urls and md.hasHtml():  # images dragged from a page often only carry <img src>
        m = re.search(r'<img[^>]+src="(https?://[^"]+)"', md.html())
        if m:
            urls.append(html_unescape(m.group(1)))
    image = md.imageData() if md.hasImage() and not files and not urls else None
    return {"files": files, "urls": urls, "image": image}


def drop_has_images(md: QMimeData) -> bool:
    d = read_drop(md)
    return bool(d["urls"] or d["image"] is not None or any(is_image(f) for f in d["files"]))


def _short_url(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(url)
    return (u.hostname or "").removeprefix("www.") + u.path


# -- The script ----------------------------------------------------------------------------

class ScriptView(QTextBrowser):
    """The script, read-only, with highlights drawn over it. One text block per paragraph,
    so (block number, position in block) == (paragraph, character offset)."""
    highlight_clicked = Signal(int)
    images_dropped = Signal(int, dict)
    paragraph_double_clicked = Signal(int, int)  # paragraph, character offset clicked
    selection_changed_range = Signal(bool)
    suggest_requested = Signal(int, str)  # highlight id, "all" | "videos" | "articles"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.suggesting: set[int] = set()  # highlights Claude is working on right now
        self.setOpenLinks(False)
        self.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        self.setFont(ui_font(11))
        self.document().setDocumentMargin(14)
        self._highlights: list[Highlight] = []
        self._selected = 0
        self._highlight_extras: list = []
        self._matches: list[QTextCursor] = []   # Ctrl+F results
        self._match_i = -1
        self.setAcceptDrops(True)  # images dropped onto a highlight attach to it
        self.selectionChanged.connect(lambda: self.selection_changed_range.emit(self.selection_range() is not None))

    def load(self, paragraphs, removed: set[int] = frozenset()) -> None:
        doc = self.document()
        doc.clear()
        cur = QTextCursor(doc)
        body = QTextCharFormat()
        link_fmt = QTextCharFormat()
        link_fmt.setForeground(QColor("#0000c0"))
        link_fmt.setFontUnderline(True)
        link_fmt.setAnchor(True)
        for i, para in enumerate(paragraphs):
            if i:
                cur.insertBlock()
            if i in removed:
                # Left out: an empty, zero-height line, so block numbers still match paragraphs.
                gone = cur.blockFormat()
                gone.setTopMargin(0)
                gone.setBottomMargin(0)
                gone.setLineHeight(0, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                cur.setBlockFormat(gone)
                tiny = QTextCharFormat()
                tiny.setFontPointSize(1)
                cur.setCharFormat(tiny)
                continue
            block_fmt = cur.blockFormat()
            block_fmt.setLineHeight(100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_fmt.setBottomMargin(8 if para.heading else 10)
            block_fmt.setTopMargin(12 if para.heading else 0)
            cur.setBlockFormat(block_fmt)
            base = QTextCharFormat(body)
            if para.heading:
                base.setFontWeight(QFont.Bold)
                base.setFontPointSize({1: 16, 2: 14}.get(para.heading, 12))
            pos = 0
            for link in sorted(para.links, key=lambda l: l.start):
                cur.insertText(para.text[pos:link.start].replace("\n", "\u2028"), base)
                f = QTextCharFormat(base)
                f.merge(link_fmt)
                f.setAnchorHref(link.url)
                f.setToolTip(link.url)
                cur.insertText(para.text[link.start:link.end].replace("\n", "\u2028"), f)
                pos = link.end
            cur.insertText(para.text[pos:].replace("\n", "\u2028"), base)
        root = doc.rootFrame().frameFormat()
        root.setLeftMargin(GUTTER)  # room for the highlight numbers
        doc.rootFrame().setFrameFormat(root)
        self.moveCursor(QTextCursor.Start)

    def paintEvent(self, e) -> None:  # noqa: N802
        """Draw each highlight's number in the left margin, level with where it starts."""
        super().paintEvent(e)
        if not self._highlights:
            return
        doc = self.document()
        lines: dict[int, list[Highlight]] = {}
        heights: dict[int, int] = {}
        for h in self._highlights:
            block = doc.findBlockByNumber(h.para)
            if not block.isValid():
                continue
            cur = QTextCursor(block)
            cur.setPosition(block.position() + h.start)
            r = self.cursorRect(cur)
            lines.setdefault(r.top(), []).append(h)
            heights[r.top()] = r.height()
        p = QPainter(self.viewport())
        font = ui_font(8, bold=True)
        p.setFont(font)
        fm = p.fontMetrics()
        for top, hs in lines.items():
            text = ",".join(h.label for h in hs)
            text = fm.elidedText(text, Qt.ElideRight, GUTTER - 10)
            line_h = heights[top]
            w = max(fm.horizontalAdvance(text) + 8, 22)
            box = QRect(GUTTER - w - 6, top + (line_h - fm.height() - 4) // 2, w, fm.height() + 4)
            if box.bottom() < 0 or box.top() > self.viewport().height():
                continue
            chosen = any(h.id == self._selected for h in hs)
            p.fillRect(box, QColor("#1060c0") if chosen else QColor("#000080"))
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, text)
            if any(h.note for h in hs):  # folded yellow corner: this highlight has a note
                c = max(5, box.height() // 3)
                tri = QPolygon([QPoint(box.right() - c + 1, box.top()), QPoint(box.right() + 1, box.top()),
                                QPoint(box.right() + 1, box.top() + c)])
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#ffd800"))
                p.drawPolygon(tri)
        p.end()

    def set_highlights(self, highlights: list[Highlight], selected: int = 0) -> None:
        self._highlights = highlights
        self._selected = selected
        extras = []
        doc = self.document()
        for i, h in enumerate(highlights):
            block = doc.findBlockByNumber(h.para)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(block)
            cur.setPosition(block.position() + h.start)
            cur.setPosition(block.position() + h.end, QTextCursor.KeepAnchor)
            sel.cursor = cur
            sel.format.setBackground(SELECTED if h.id == selected else (HIGHLIGHT_ALT if i % 2 else HIGHLIGHT))
            extras.append(sel)
        self._highlight_extras = extras
        self._apply_extras()

    def _apply_extras(self) -> None:
        extras = list(self._highlight_extras)
        for i, cur in enumerate(self._matches):  # Ctrl+F results on top of the highlights
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format.setBackground(QColor("#ff9632") if i == self._match_i else QColor("#ffd27f"))
            sel.format.setForeground(QColor("#000000"))
            extras.append(sel)
        self.setExtraSelections(extras)
        self.viewport().update()

    # -- Ctrl+F ------------------------------------------------------------------------

    def set_search(self, term: str) -> int:
        """Mark every match of `term` (any case). Returns how many there are."""
        self._matches, self._match_i = [], -1
        doc = self.document()
        if term:
            cur = QTextCursor(doc)
            while True:
                cur = doc.find(term, cur)
                if cur.isNull():
                    break
                self._matches.append(QTextCursor(cur))
        self._apply_extras()
        return len(self._matches)

    def find_step(self, forward: bool = True) -> tuple[int, int]:
        """Go to the next (or previous) match; returns (which one, how many)."""
        if not self._matches:
            return 0, 0
        if self._match_i < 0:  # first jump: the nearest match after the cursor
            here = self.textCursor().selectionEnd() if forward else self.textCursor().selectionStart()
            after = [i for i, c in enumerate(self._matches) if c.selectionStart() >= here]
            before = [i for i, c in enumerate(self._matches) if c.selectionStart() < here]
            self._match_i = (after[0] if after else 0) if forward else (before[-1] if before else len(self._matches) - 1)
        else:
            self._match_i = (self._match_i + (1 if forward else -1)) % len(self._matches)
        self.setTextCursor(QTextCursor(self._matches[self._match_i]))
        self.ensureCursorVisible()
        self._apply_extras()
        return self._match_i + 1, len(self._matches)

    def scroll_to(self, h: Highlight) -> None:
        block = self.document().findBlockByNumber(h.para)
        cur = QTextCursor(block)
        cur.setPosition(block.position() + h.start)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def selection_range(self) -> tuple[int, int, int] | None:
        cur = self.textCursor()
        if not cur.hasSelection():
            return None
        doc = self.document()
        a, b = doc.findBlock(cur.selectionStart()), doc.findBlock(cur.selectionEnd())
        if a.blockNumber() != b.blockNumber():
            return None  # highlights stay within one paragraph
        start = cur.selectionStart() - a.position()
        end = cur.selectionEnd() - a.position()
        text = a.text()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return (a.blockNumber(), start, end) if end > start else None

    def spans_paragraphs(self) -> bool:
        cur = self.textCursor()
        doc = self.document()
        return cur.hasSelection() and doc.findBlock(cur.selectionStart()) != doc.findBlock(cur.selectionEnd())

    def highlight_at(self, pos) -> Highlight | None:
        cur = self.cursorForPosition(pos)
        block, offset = cur.block().blockNumber(), cur.positionInBlock()
        return next((h for h in self._highlights if h.para == block and h.start <= offset <= h.end), None)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if drop_has_images(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if self.highlight_at(e.position().toPoint()) and drop_has_images(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e) -> None:  # noqa: N802
        h = self.highlight_at(e.position().toPoint())
        if h:
            self.images_dropped.emit(h.id, read_drop(e.mimeData()))
            e.acceptProposedAction()
        else:
            e.ignore()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.anchorAt(e.position().toPoint()):
            return super().mouseDoubleClickEvent(e)
        cur = self.cursorForPosition(e.position().toPoint())
        self.paragraph_double_clicked.emit(cur.block().blockNumber(), cur.positionInBlock())

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = self.createStandardContextMenu(e.pos())
        cur = self.cursorForPosition(e.pos())
        block, pos = cur.block().blockNumber(), cur.positionInBlock()
        h = next((x for x in self._highlights if x.para == block and x.start <= pos <= x.end), None)
        if h:
            first = menu.actions()[0] if menu.actions() else None
            busy = h.id in self.suggesting
            for mode, text in sg.MODES.items():
                act = menu.addAction(f"{text} for Highlight {h.label}" + ("  (working…)" if busy else ""))
                act.setEnabled(not busy)
                act.triggered.connect(lambda _=False, hid=h.id, m=mode: self.suggest_requested.emit(hid, m))
                if first:
                    menu.removeAction(act)
                    menu.insertAction(first, act)
            if first:
                menu.insertSeparator(first)
        menu.exec(e.globalPos())

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        super().mouseReleaseEvent(e)
        if self.textCursor().hasSelection() or self.anchorAt(e.position().toPoint()):
            return
        cur = self.cursorForPosition(e.position().toPoint())
        block, pos = cur.block().blockNumber(), cur.positionInBlock()
        for h in self._highlights:
            if h.para == block and h.start <= pos <= h.end:
                self.highlight_clicked.emit(h.id)
                return


# -- Added Sources -----------------------------------------------------------------------------

class AddedSources(QWidget):
    """Articles attached to highlights that aren't linked in the script, for the sources
    document at the end of a project."""
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[Highlight, LinkRef]] = []
        intro = QLabel("Articles attached to highlights that the script doesn't link to, such as ones added "
                       "with Add Links… or Suggest. Copy them for the sources document.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        self.rows_host = QWidget()
        self.rows_host.setBackgroundRole(QPalette.Base)
        self.rows_host.setAutoFillBackground(True)
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.rows_host)
        self.scroll = scroll
        self.empty = QLabel("Nothing added yet. Every article attached to a highlight is linked in the script.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        copy_links = QPushButton("Copy All &Links")
        copy_links.setToolTip("Copy every link, one per line.")
        copy_links.clicked.connect(lambda: self._copy(self.items, False))
        copy_full = QPushButton("Copy All with &Highlights")
        copy_full.setToolTip("Copy every link with its highlight's number and passage, one per line.")
        copy_full.clicked.connect(lambda: self._copy(self.items, True))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(copy_links)
        row.addWidget(copy_full)
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)
        col.addWidget(intro)
        col.addWidget(scroll, 1)
        col.addWidget(self.empty, 1)
        col.addLayout(row)
        self.show_sources([])

    def show_sources(self, items: list[tuple[Highlight, LinkRef]]) -> None:
        self.items = items
        while self.rows.count() > 1:
            if w := self.rows.takeAt(0).widget():
                w.deleteLater()
        for i, (h, ref) in enumerate(items):
            self.rows.insertWidget(i, self._row(h, ref))
        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)

    def _row(self, h: Highlight, ref: LinkRef) -> QWidget:
        link = urldefrag(ref.url)[0]
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        num = QLabel(f"<b>{h.label}.</b>")
        url = QLabel(f'<a href="{link}">{_short_url(link)}</a>')
        url.setOpenExternalLinks(True)
        url.setToolTip(f"{link}\nClick to open it in your browser.")
        url.setTextInteractionFlags(Qt.TextBrowserInteraction)
        url.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        passage = h.text if len(h.text) <= 140 else h.text[:137].rstrip() + "…"
        quote = QLabel(f"“{passage}”")
        quote.setObjectName("muted")
        quote.setWordWrap(True)
        quote.setToolTip(f"Highlight {h.label}: “{h.text}”")
        copy = QPushButton("Copy")
        copy.setToolTip("Copy this link.")
        copy.setFixedWidth(copy.fontMetrics().horizontalAdvance("Copy") + 36)
        copy.clicked.connect(lambda _=False, item=(h, ref): self._copy([item], False))
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        grid.addWidget(num, 0, 0, Qt.AlignTop)
        grid.addWidget(url, 0, 1)
        grid.addWidget(copy, 0, 2, 2, 1, Qt.AlignVCenter)
        grid.addWidget(quote, 1, 1)
        grid.addWidget(sep, 2, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        return frame

    def _copy(self, chosen: list[tuple[Highlight, LinkRef]], with_highlights: bool) -> None:
        if not chosen:
            self.status.emit("No added sources to copy.")
            return
        if with_highlights:
            lines = [f"{h.label}. “{h.text}” {urldefrag(r.url)[0]}" for h, r in chosen]
        else:
            lines = [urldefrag(r.url)[0] for _, r in chosen]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.emit(f"Copied {lines[0]}" if len(lines) == 1 and not with_highlights
                         else f"Copied {len(lines)} added source{'s' if len(lines) != 1 else ''}.")


# -- Editing a paragraph -------------------------------------------------------------------

class EditParagraphDialog(QDialog):
    """Double-click a paragraph to change its words. Saved in the project only; the .docx
    isn't touched. Highlights and links in the paragraph follow their words."""

    def __init__(self, text: str, original: str | None, cursor_at: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Paragraph")
        self.resize(720, 360)
        self.reverted = False
        intro = QLabel("Changes are saved in this project only; the Word file isn't changed. "
                       "Highlights and links in this paragraph move with their words.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        self.edit = QPlainTextEdit(text)
        self.edit.setFont(ui_font(11))
        cur = self.edit.textCursor()
        cur.setPosition(max(0, min(cursor_at, len(text))))
        self.edit.setTextCursor(cur)
        save = QPushButton("&Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        if original is not None:
            revert = QPushButton("&Revert to Original")
            revert.setToolTip("Go back to the paragraph as it is in the Word file.")
            revert.clicked.connect(lambda: (self.edit.setPlainText(original), None))
            row.addWidget(revert)
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(cancel)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.addWidget(intro)
        col.addWidget(self.edit, 1)
        col.addLayout(row)
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.toPlainText()


# -- Attach / Skip prompt --------------------------------------------------------------------

class AttachDialog(QDialog):
    """Asks about each embedded link in a new highlight, one at a time, with a preview."""

    def __init__(self, urls: list[str], previews: PreviewService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Attach Links")
        self.urls = urls
        self.previews = previews
        self.chosen: list[str] = []
        self.i = 0

        self.counter = QLabel()
        self.counter.setObjectName("muted")
        self.image = QLabel("Loading preview…")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedSize(560, 340)
        self.image.setFrameShape(QFrame.StyledPanel)
        self.image.setFrameShadow(QFrame.Sunken)
        self.image.setAutoFillBackground(True)
        self.image.setBackgroundRole(QPalette.Base)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setObjectName("jobTitle")
        self.url = ElidedLabel()
        self.url.setObjectName("muted")

        attach = QPushButton("&Attach")
        attach.setObjectName("primary")
        attach.setDefault(True)
        attach.clicked.connect(lambda: self._answer(True))
        skip = QPushButton("&Skip")
        skip.clicked.connect(lambda: self._answer(False))
        attach_all = QPushButton("Attach A&ll")
        attach_all.clicked.connect(self._attach_all)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(attach_all)
        buttons.addStretch(1)
        buttons.addWidget(attach)
        buttons.addWidget(skip)
        buttons.addWidget(cancel)

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addWidget(self.counter)
        col.addWidget(self.image)
        col.addWidget(self.title)
        col.addWidget(self.url)
        col.addSpacing(6)
        col.addLayout(buttons)

        previews.ready.connect(self._on_preview)
        for u in urls:
            previews.request(u)
        self._show()

    def _show(self) -> None:
        url = self.urls[self.i]
        kind = "Video" if link_kind(url) == "video" else "Article"
        self.counter.setText(f"Embedded link {self.i + 1} of {len(self.urls)} · {kind}")
        self.url.setText(url)
        self.title.setText("")
        self.image.setPixmap(QPixmap())
        self.image.setText("Loading preview…")
        cached = self.previews.cached(url)
        if cached:
            self._on_preview(url, *cached)

    def _on_preview(self, url: str, data, title: str) -> None:
        if self.i >= len(self.urls) or url != self.urls[self.i]:
            return
        self.title.setText(title or _short_url(url))
        pm = QPixmap()
        if data and pm.loadFromData(bytes(data)):
            dpr = self.devicePixelRatioF()
            pm = pm.scaled(int(self.image.width() * dpr), int(self.image.height() * dpr),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.image.setPixmap(pm)
        else:
            self.image.setText("No preview available")

    def _answer(self, attach: bool) -> None:
        if attach:
            self.chosen.append(self.urls[self.i])
        self.i += 1
        if self.i >= len(self.urls):
            self.accept()
        else:
            self._show()

    def _attach_all(self) -> None:
        self.chosen += self.urls[self.i:]
        self.accept()


# -- A highlight in the list -----------------------------------------------------------------

class FileChip(QWidget):
    """A downloaded file: its name, a Copy button, and drag-and-drop into Premiere."""

    def __init__(self, path: str, parent=None, on_detach=None, highlighted: bool = False, on_copy_source=None):
        super().__init__(parent)
        self.path = path
        # A blue band when this file belongs to the highlight selected in the script.
        self._band = QColor("#b9dcff") if highlighted else None
        self._press: QPoint | None = None
        grip = QLabel("⠿")
        grip.setObjectName("muted")
        grip.setToolTip("Drag this file into Premiere")
        name = ElidedLabel(path_name(path))
        if highlighted:
            f = name.font()
            f.setBold(True)
            name.setFont(f)
        name.setToolTip(f"{path}\nDrag this row onto Premiere's Project panel, a bin or the timeline.")
        if not os.path.exists(path):
            name.setObjectName("muted")
            name.setToolTip(f"{path}\n(missing: moved or deleted)")
        if on_copy_source:  # videos: the credit for the on-screen source (files go into Premiere by dragging)
            copy = QPushButton("Copy Source")
            copy.setToolTip("Copy the on-screen source credit: “Title | Channel” for YouTube, the @handle "
                            "for TikTok and Instagram.")
            copy.setFixedWidth(copy.fontMetrics().horizontalAdvance("Copy Source") + 30)
            copy.clicked.connect(on_copy_source)
        else:
            copy = QPushButton("Copy")
            copy.setFixedWidth(copy.fontMetrics().horizontalAdvance("Copy") + 36)
            copy.clicked.connect(lambda: (copy_files([path]), self._copied()))
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(grip)
        row.addWidget(name, 1)
        row.addWidget(copy)
        if on_detach:
            detach = QPushButton("Detach")
            detach.setToolTip("Remove this image from the highlight (the file stays in the Images folder).")
            detach.setFixedWidth(detach.fontMetrics().horizontalAdvance("Detach") + 30)
            detach.clicked.connect(on_detach)
            row.addWidget(detach)
        self.setCursor(Qt.OpenHandCursor)

    def paintEvent(self, e) -> None:  # noqa: N802
        if self._band is not None:  # painted directly: the app stylesheet ignores palette backgrounds
            p = QPainter(self)
            p.fillRect(self.rect(), self._band)
            p.end()
        super().paintEvent(e)

    def _copied(self) -> None:
        _page_status(self, f"Copied {path_name(self.path)}. Paste it into {FILE_MANAGER}, or drag the row into Premiere.")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._press = e.position().toPoint()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None or (e.position().toPoint() - self._press).manhattanLength() < 6:
            return
        self._press = None
        start_file_drag(self, [self.path])


class HighlightCard(QFrame):
    clicked = Signal(int)
    remove_clicked = Signal(int)
    add_links_clicked = Signal(int)
    note_clicked = Signal(int)
    copy_all_clicked = Signal(int)
    retry_clicked = Signal(int, str)
    add_images_clicked = Signal(int)
    images_dropped = Signal(int, dict)    # highlight id, {"files": [...], "urls": [...], "image": QImage|None}
    image_detached = Signal(int, str)
    link_removed = Signal(int, str)       # highlight id, url
    link_excluded = Signal(int, str, bool)  # highlight id, url, leave out of the Screenshotter
    suggest_clicked = Signal(int, str)    # highlight id, mode
    lead_removed = Signal(int, int)       # highlight id, lead index
    copy_source_clicked = Signal(int, str)  # highlight id, video url
    retake_clicked = Signal(int, str)       # highlight id, article url
    kind_switched = Signal(str)             # url of an X or Reddit post to switch Video <-> Article

    def __init__(self, h: Highlight, index: int, selected: bool, parent=None, suggesting: str = ""):
        super().__init__(parent)
        self.hid = h.id
        self.setAcceptDrops(True)
        self._selected = selected
        self._bg = QColor("#e3f1ff") if selected else QColor("#ffffff")

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 8, 10, 6)
        col.setSpacing(4)

        top = QHBoxLayout()
        text = h.text if len(h.text) <= 220 else h.text[:217].rstrip() + "…"
        quote = QLabel(f"<b>{h.label}.</b> “{text}”")
        quote.setWordWrap(True)
        quote.setTextFormat(Qt.RichText)
        remove = QPushButton("Remove")
        remove.setToolTip("Remove this highlight (downloaded files stay on disk).")
        remove.clicked.connect(lambda: self.remove_clicked.emit(self.hid))
        suggest = QPushButton("Suggesting…" if suggesting else "Suggest")
        suggest.setToolTip("Claude is researching this passage. Its suggestions open when it's done." if suggesting
                           else "Ask Claude to find articles and video leads for this passage.")
        suggest.setEnabled(not suggesting)
        menu = QMenu(suggest)
        for mode, text in sg.MODES.items():
            menu.addAction(text, lambda m=mode: self.suggest_clicked.emit(self.hid, m))
        suggest.setMenu(menu)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(remove)
        right.addWidget(suggest)
        right.addStretch(1)
        top.addWidget(quote, 1)
        top.addLayout(right)
        col.addLayout(top)
        if h.note:
            note = QLabel(h.note)
            note.setWordWrap(True)
            note.setTextInteractionFlags(Qt.TextSelectableByMouse)
            note.setStyleSheet("background: #ffffe1; border: 1px solid #c8c890; padding: 4px 6px; color: #303000;")
            note.setToolTip("Your note. Click Note… to change it.")
            col.addWidget(note)

        for i, lead in enumerate(h.leads):
            line = QHBoxLayout()
            line.setSpacing(8)
            tag = QLabel("Lead")
            tag.setFixedWidth(tag.fontMetrics().horizontalAdvance("Article") + 10)
            tag.setObjectName("muted")
            what = QLabel(f"{lead.get('platform', '')}: “{lead.get('search', '')}”")
            what.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            what.setToolTip("\n".join(x for x in (lead.get("look_for", ""), lead.get("why", "")) if x)
                            + "\n\nA video lead from Suggest. Search, find the clip, then add it with Add Links….")
            go = QPushButton("Search")
            go.clicked.connect(lambda _=False, ld=lead: QDesktopServices.openUrl(QUrl(sg.search_url(ld))))
            drop = QPushButton("✕")
            drop.setFixedWidth(drop.fontMetrics().horizontalAdvance("✕") + 22)
            drop.setToolTip("Remove this lead.")
            drop.clicked.connect(lambda _=False, i=i: self.lead_removed.emit(self.hid, i))
            line.addWidget(tag)
            line.addWidget(what, 1)
            line.addWidget(go)
            line.addWidget(drop)
            col.addLayout(line)

        if not h.links and not h.images and not h.leads:
            none = QLabel("No links or images attached. Drop images here, or use Add Images…")
            none.setObjectName("muted")
            col.addWidget(none)
        for ref in h.links:
            line = QHBoxLayout()
            line.setSpacing(8)
            word = "Video" if ref.kind == "video" else "Article"
            if needs_lookup(ref.url) and ref.status in (PENDING, FAILED, SKIPPED) and not ref.files:
                unsure = ref.sorted_by == "unsure"
                tag = QPushButton(word + ("?" if unsure else ""))
                tag.setFixedWidth(tag.fontMetrics().horizontalAdvance("Article?") + 18)
                other = "Article" if ref.kind == "video" else "Video"
                how = {"checked": "Checked: this is what the post holds.\n",
                       "manual": "You chose this.\n",
                       "unsure": f"Couldn't check this post, so it counts as an article for now. {ref.sort_note}\n",
                       "": "This post gets checked when you click Execute.\n"}.get(ref.sorted_by, "")
                tag.setToolTip(f"{how}Click to make it {'an ' if other == 'Article' else 'a '}{other}.")
                tag.clicked.connect(lambda _=False, u=ref.url: self.kind_switched.emit(u))
            else:
                tag = QLabel(word)
                tag.setFixedWidth(tag.fontMetrics().horizontalAdvance("Article") + 10)
                tag.setObjectName("muted")
            url = QLabel(f'<a href="{ref.url}">{_short_url(ref.url)}</a>')
            url.setOpenExternalLinks(True)
            url.setToolTip(ref.url)
            url.setTextInteractionFlags(Qt.TextBrowserInteraction)
            url.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            skippable = ref.kind == "article" and ref.status not in (RUNNING, DONE)
            if ref.excluded and skippable:
                status = QLabel("Left out")
                status.setStyleSheet("color: #808080; font-weight: bold;")
                status.setToolTip("Left out of Execute Screenshotter. Click Include to capture it again.")
                url.setText(f'<a href="{ref.url}" style="color:#808080">{_short_url(ref.url)}</a>')
            else:
                status = QLabel(STATUS_TEXT.get(ref.status, ref.status))
                status.setStyleSheet(f"color: {STATUS_COLOR.get(ref.status, '#000')}; font-weight: bold;")
                if ref.error:
                    status.setToolTip(ref.error)
            line.addWidget(tag)
            line.addWidget(url, 1)
            line.addWidget(status)
            if skippable:
                skip = QPushButton("Include" if ref.excluded else "Skip")
                skip.setFixedWidth(skip.fontMetrics().horizontalAdvance("Include") + 26)
                skip.setToolTip("Capture this article with the others again." if ref.excluded else
                                "Leave this article out of Execute Screenshotter (the link stays on the highlight).")
                skip.clicked.connect(lambda _=False, u=ref.url, x=not ref.excluded:
                                     self.link_excluded.emit(self.hid, u, x))
                line.addWidget(skip)
            if ref.kind == "article" and ref.status == DONE:
                retake = QPushButton("Re-Take")
                retake.setToolTip("Capture this article again. The shots you have are kept; new ones are "
                                  "numbered on, like “(2)”.")
                retake.clicked.connect(lambda _=False, u=ref.url: self.retake_clicked.emit(self.hid, u))
                line.addWidget(retake)
            if ref.status in (FAILED, SKIPPED) and not (ref.excluded and skippable):
                retry = QPushButton("Retry")
                retry.clicked.connect(lambda _=False, u=ref.url: self.retry_clicked.emit(self.hid, u))
                line.addWidget(retry)
            drop_link = QPushButton("✕")
            drop_link.setFixedWidth(drop_link.fontMetrics().horizontalAdvance("✕") + 22)
            drop_link.setToolTip("Remove this link from the highlight (anything it downloaded stays on disk).")
            drop_link.setEnabled(ref.status != RUNNING)
            drop_link.clicked.connect(lambda _=False, u=ref.url: self.link_removed.emit(self.hid, u))
            line.addWidget(drop_link)
            col.addLayout(line)
            for path in ref.files:
                copy_source = ((lambda _=False, u=ref.url: self.copy_source_clicked.emit(self.hid, u))
                               if ref.kind == "video" else None)
                col.addWidget(FileChip(path, highlighted=selected, on_copy_source=copy_source))
        for path in h.images:
            line = QHBoxLayout()
            tag = QLabel("Image")
            tag.setFixedWidth(tag.fontMetrics().horizontalAdvance("Article") + 10)
            tag.setObjectName("muted")
            chip = FileChip(path, on_detach=lambda _=False, pth=path: self.image_detached.emit(self.hid, pth),
                            highlighted=selected)
            chip.layout().setContentsMargins(0, 0, 0, 0)
            line.addWidget(tag)
            line.addWidget(chip, 1)
            col.addLayout(line)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        note_btn = QPushButton("Edit Note…" if h.note else "Note…")
        note_btn.clicked.connect(lambda: self.note_clicked.emit(self.hid))
        bottom.addWidget(note_btn)
        add = QPushButton("Add Links…")
        add.clicked.connect(lambda: self.add_links_clicked.emit(self.hid))
        bottom.addWidget(add)
        add_img = QPushButton("Add Images…")
        add_img.setToolTip("Attach images from the Images folder (or anywhere: they're copied in). "
                           "You can also drop images onto this card.")
        add_img.clicked.connect(lambda: self.add_images_clicked.emit(self.hid))
        bottom.addWidget(add_img)
        files = [p for ref in h.links for p in ref.files] + list(h.images)
        if files:
            bottom.addWidget(DragAllButton(files))
            copy_all = QPushButton(f"Copy All ({len(files)})")
            copy_all.setToolTip(f"Copy the files to the clipboard, to paste into {FILE_MANAGER}.")
            copy_all.clicked.connect(lambda: self.copy_all_clicked.emit(self.hid))
            bottom.addWidget(copy_all)
        col.addLayout(bottom)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        col.addWidget(sep)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit(self.hid)
        super().mousePressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)  # painted directly: the app stylesheet ignores palette backgrounds
        p.fillRect(self.rect(), self._bg)
        p.end()
        super().paintEvent(e)

    def _tint(self, color: str) -> None:
        self._bg = QColor(color)
        self.update()

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if drop_has_images(e.mimeData()):
            e.acceptProposedAction()
            self._tint("#d8f0d0")  # green-ish: "drop here"

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._tint("#e3f1ff" if self._selected else "#ffffff")

    def dropEvent(self, e) -> None:  # noqa: N802
        self.dragLeaveEvent(e)
        drop = read_drop(e.mimeData())
        e.acceptProposedAction()
        self.images_dropped.emit(self.hid, drop)


# -- The page ----------------------------------------------------------------------------------

class ScriptPage(QWidget):
    status = Signal(str)
    image_downloaded = Signal(int, str, str)  # highlight id, saved path, error
    source_found = Signal(object, str, str, str)  # project, url, credit, error
    posts_checked = Signal(object, dict, str)     # project, {url: (kind, note)}, Execute button to carry on with

    def __init__(self, settings: Settings, folders: FolderStore, session: SessionWorker, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.state = settings.tab("script")
        self.project: Project | None = None
        self.selected = 0
        self.session = session
        self.previews = PreviewService(self)
        self.suggester = SuggestWorker(self)
        self.suggester.finished.connect(self._on_suggested)
        self.suggester.failed.connect(self._on_suggest_failed)
        self.suggester.status.connect(self.status)
        self._suggesting: dict[tuple[str, int], str] = {}  # (project file, highlight id) -> mode
        self._job_sources: dict[tuple[str, int], str] = {}  # (platform, job id) -> source credit
        self._looking_up: set[str] = set()  # videos whose credit is being fetched
        self._retakes: set[int] = set()  # capture stops that add to an article's shots instead of replacing them
        self._sorting: Project | None = None  # the project whose X and Reddit posts are being checked
        self.posts_checked.connect(self._on_posts_checked)
        self.source_found.connect(self._on_source_found)
        self._suggest_queue: list[tuple] = []  # results waiting for the window to be free
        self._showing_suggestions = False
        self.workers: dict[str, DownloadWorker] = {}
        self.jobs: dict[tuple[str, int], tuple[Project, str]] = {}  # (platform, job id) -> (project, url)
        self.job_rows: dict[tuple[str, int], JobRow] = {}
        self.stops: dict[int, tuple[Project, str]] = {}  # capture stop id -> (project, url)
        # Projects opened this session. Downloads and captures keep updating (and saving) their
        # own project even after you switch to another script, and reopening one reuses it.
        self._open: dict[str, Project] = {}
        self._offered_move: set = set()  # projects already offered a moved-folder reconnect this session
        self.setAcceptDrops(True)

        session.stop_started.connect(self._on_stop_started)
        session.shot_saved.connect(self._on_shot_saved)
        session.stop_finished.connect(self._on_stop_finished)
        session.session_ended.connect(self._on_session_ended)

        # -- top: script + folders -----------------------------------------------------
        self.script_combo = QComboBox()
        self.script_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.script_combo.setToolTip("Switch between your scripts. Everything is saved as you go.")
        self.script_combo.activated.connect(self._script_chosen)
        self.locate_btn = QPushButton("Locate Script…")
        self.locate_btn.setToolTip("This script's Word file was moved or renamed. Find it to reconnect the "
                                   "project (nothing is lost meanwhile: the project keeps a copy of the text).")
        self.locate_btn.clicked.connect(self.locate_script)
        self.locate_btn.hide()
        import_btn = QPushButton("&Import Script…")
        import_btn.clicked.connect(self.browse)
        self.trim_btn = QPushButton("T&rim Script…")
        self.trim_btn.setToolTip("Choose which paragraphs to leave out (research notes and the like).")
        self.trim_btn.clicked.connect(self.trim)
        self.trim_btn.setEnabled(False)
        script_buttons = QHBoxLayout()
        script_buttons.setSpacing(6)
        script_buttons.addWidget(self.locate_btn)
        script_buttons.addWidget(import_btn)
        script_buttons.addWidget(self.trim_btn)

        # One project folder; videos, screenshots and images go to its standard subfolders.
        self.project_store = FolderStore(settings, key="project_folders")
        self.project_folder = FolderCombo(self.project_store, self.state.get("project_folder"))
        self.project_folder.setToolTip("The video's project folder. Videos go to its BRoll folder, screenshots "
                                       "to Screen Captures, images to Images.")
        self.project_folder.folder_changed.connect(self._project_folder_changed)
        open_p = QPushButton("Open")
        open_p.clicked.connect(lambda: show_in_explorer(self.project_folder.current_folder()))
        self.dests = QLabel()
        self.dests.setObjectName("muted")
        self.dests.linkActivated.connect(self._open_dest)
        self.image_downloaded.connect(self._on_image_downloaded)

        form = QGridLayout()
        form.setContentsMargins(12, 12, 12, 10)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        for row, (label, buddy, field, button) in enumerate((
                ("Scri&pt:", self.script_combo, self.script_combo, script_buttons),
                ("Pro&ject folder:", self.project_folder, self.project_folder, open_p))):
            lab = QLabel(label)
            lab.setBuddy(buddy)
            form.addWidget(lab, row, 0)
            form.addWidget(field, row, 1)
            if isinstance(button, QHBoxLayout):
                form.addLayout(button, row, 2)
            else:
                form.addWidget(button, row, 2)
        form.addWidget(self.dests, 2, 1, 1, 2)
        self._show_dests()
        form.setColumnStretch(1, 1)
        top_box = QGroupBox("Script Project")
        top_box.setLayout(form)

        # -- left: the script ------------------------------------------------------------
        self.view = ScriptView()
        self.view.highlight_clicked.connect(self.select_from_script)
        self.view.paragraph_double_clicked.connect(self.edit_paragraph)
        self.view.images_dropped.connect(self.attach_images)
        self.view.suggest_requested.connect(self.suggest)
        self.highlight_btn = QPushButton("Hi&ghlight Selection")
        self.highlight_btn.setObjectName("primary")
        self.highlight_btn.setToolTip("Highlight the selected words and choose which of their links to attach.")
        self.highlight_btn.clicked.connect(self.highlight_selection)
        self.highlight_btn.setEnabled(False)
        self.view.selection_changed_range.connect(self._selection_changed)

        # Ctrl+F: find in the script.
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find in script")
        self.find_box.textChanged.connect(self._find_changed)
        self.find_box.returnPressed.connect(lambda: self._find_step(True))
        self.find_count = QLabel("")
        self.find_count.setObjectName("muted")
        prev_btn = QPushButton("Previous")
        prev_btn.clicked.connect(lambda: self._find_step(False))
        next_btn = QPushButton("Next")
        next_btn.clicked.connect(lambda: self._find_step(True))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._close_find)
        self.find_bar = QWidget()
        frow = QHBoxLayout(self.find_bar)
        frow.setContentsMargins(0, 0, 0, 0)
        frow.setSpacing(6)
        frow.addWidget(QLabel("Find:"))
        frow.addWidget(self.find_box, 1)
        frow.addWidget(self.find_count)
        frow.addWidget(prev_btn)
        frow.addWidget(next_btn)
        frow.addWidget(close_btn)
        self.find_bar.hide()
        QShortcut(QKeySequence.Find, self, self._open_find, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.Save, self, self.save_now, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Shift+Return"), self.find_box, lambda: self._find_step(False))
        QShortcut(QKeySequence("Escape"), self.find_box, self._close_find)
        hint = QLabel("Select words, then Highlight Selection (or select over a highlight to adjust it). "
                      f"Double-click to edit text. {MOD_KEY}+F finds.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        left = QWidget()
        lcol = QVBoxLayout(left)
        lcol.setContentsMargins(0, 0, 0, 0)
        lcol.setSpacing(6)
        lcol.addWidget(self.find_bar)
        lcol.addWidget(self.view, 1)
        lrow = QHBoxLayout()
        lrow.addWidget(hint, 1)
        lrow.addWidget(self.highlight_btn)
        lcol.addLayout(lrow)

        # -- right: highlights + video downloads --------------------------------------------
        self.cards_host = QWidget()
        self.cards_host.setBackgroundRole(QPalette.Base)
        self.cards_host.setAutoFillBackground(True)
        self.cards = QVBoxLayout(self.cards_host)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(0)
        self.cards.addStretch(1)
        self.cards_scroll = FitScrollArea()
        self.cards_scroll.setWidget(self.cards_host)

        self.dl_host = QWidget()
        self.dl_host.setBackgroundRole(QPalette.Base)
        self.dl_host.setAutoFillBackground(True)
        self.dl_list = QVBoxLayout(self.dl_host)
        self.dl_list.setContentsMargins(0, 0, 0, 0)
        self.dl_list.setSpacing(0)
        self.dl_empty = QLabel("Execute Video Downloads to fetch every video link in the script.\n"
                               "Each download's progress and any failures show here.")
        self.dl_empty.setObjectName("empty")
        self.dl_empty.setAlignment(Qt.AlignCenter)
        self.dl_empty.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.dl_list.addWidget(self.dl_empty)
        self.dl_list.addStretch(1)
        dl_scroll = FitScrollArea()
        dl_scroll.setWidget(self.dl_host)

        self.side = QTabWidget()
        self.side.addTab(self.cards_scroll, "Highlights")
        self.side.addTab(dl_scroll, "Video Downloads")
        self.added = AddedSources()
        self.added.status.connect(self.status)
        self.side.addTab(self.added, "Added Sources")

        self.exec_videos = QPushButton("Execute Video &Downloads")
        self.exec_videos.clicked.connect(lambda: self.execute_videos())  # not the button's "checked" flag
        self.exec_shots = QPushButton("Execute S&creenshotter")
        self.exec_shots.clicked.connect(lambda: self.execute_screenshots())
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)
        rcol.setSpacing(6)
        rcol.addWidget(self.side, 1)
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("muted")
        self.saved_label.setToolTip("Projects save automatically after every change, with earlier versions "
                                    f"kept as backups (Manage Projects…). {MOD_KEY}+S saves right now.")
        srow = QHBoxLayout()
        srow.addWidget(self.summary, 1)
        srow.addWidget(self.saved_label)
        rcol.addLayout(srow)
        rrow = QHBoxLayout()
        rrow.addWidget(self.exec_videos)
        rrow.addWidget(self.exec_shots)
        rcol.addLayout(rrow)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(top_box)
        root.addWidget(split, 1)

        self._update_buttons()
        self._refresh_script_combo()
        if (last := self.state.get("script")) and (os.path.exists(last) or project_path(last).exists()):
            QTimer.singleShot(0, lambda: self.open_script(last, quiet=True))

    # -- helpers ---------------------------------------------------------------------

    def _save_state(self, key: str, value) -> None:
        self.state[key] = value
        self.settings.save()

    def dest(self, kind: str) -> str:
        """Where files of this kind go right now: <project folder>\\BRoll and so on."""
        if self.project and self.project.project_folder:
            return self.project.dest(kind)
        return os.path.join(self.project_folder.current_folder(), SUBFOLDERS[kind])

    def _show_dests(self) -> None:
        links = " · ".join(f'{name} → <a href="{kind}">{SUBFOLDERS[kind]}</a>'
                           for kind, name in (("video", "Videos"), ("shot", "Screenshots"), ("record", "Recordings"),
                                               ("image", "Images")))
        self.dests.setText(links)
        self.dests.setToolTip("\n".join(self.dest(k) for k in SUBFOLDERS))

    def _open_dest(self, kind: str) -> None:
        path = self.dest(kind)
        show_in_explorer(path if os.path.isdir(path) else self.project_folder.current_folder())

    def _project_folder_changed(self, path: str) -> None:
        self._save_state("project_folder", path)
        p = self.project
        if p:
            old = p.project_folder
            moved = 0
            if old and os.path.normcase(old) != os.path.normcase(path):
                gone = not os.path.isdir(old)
                if gone or QMessageBox.question(
                        self, "Project Folder",
                        f"Has this project moved from\n{old}\nto\n{path}?\n\nIf so, the files on its cards will "
                        "be looked for in the new folder.") == QMessageBox.Yes:
                    moved = p.relocate(old, path)
            p.folders["project"] = path
            self._save()
            if moved:
                self.refresh()
                self.status.emit(f"Updated {moved} file location{'s' if moved != 1 else ''} to {path}.")
        self._show_dests()

    def _use_project_folders(self) -> None:
        """A project keeps its own folder. A new one is found from where the script lives
        (normally <project>\\Project Files), or else starts with the last one used."""
        p = self.project
        if not p.project_folder:
            p.folders["project"] = guess_project_folder(p.script) or self.project_folder.current_folder()
        self.project_folder.set_folder(p.project_folder)
        self._save_state("project_folder", p.project_folder)
        self._show_dests()
        self._save()

    def _save(self, project: Project | None = None) -> None:
        p = project or self.project
        if p:
            try:
                p.save()
            except OSError as e:
                QMessageBox.warning(self, "Script", f"Couldn't save the project:\n\n{e}")
                return
        if p is self.project:
            self._show_saved()

    def save_now(self) -> None:
        if self.project:
            self._save()
            self.status.emit(f"Saved {os.path.basename(self.project.script)}.")

    def save_all(self) -> None:
        for proj in self._open.values():
            try:
                proj.save()
            except OSError:
                pass

    def _show_saved(self) -> None:
        p = self.project
        if not p or not p.saved_at:
            self.saved_label.setText("")
            return
        when = time.localtime(p.saved_at)
        clock = time.strftime("%I:%M %p", when).lstrip("0")
        day = "" if time.strftime("%x", when) == time.strftime("%x") else time.strftime("%b %d, ", when)
        self.saved_label.setText(f"Saved {day}{clock}")

    def active_count(self) -> int:
        return sum(not r.finished for r in self.job_rows.values())

    def shutdown(self) -> None:
        for w in self.workers.values():
            w.cancel_all()
        self.previews.shutdown()

    # -- opening a script -------------------------------------------------------------

    _IMPORT, _MANAGE = "\0import", "\0manage"

    def _refresh_script_combo(self) -> None:
        c = self.script_combo
        c.blockSignals(True)
        c.clear()
        current = os.path.normcase(self.project.script) if self.project else ""
        index = -1
        infos = list_projects()
        if self.project and not any(os.path.normcase(i.script) == current for i in infos):
            infos.insert(0, None)  # the open project, not saved yet
        for info in infos[:12]:
            if info is None:
                c.addItem(os.path.basename(self.project.script), self.project.script)
            else:
                label = info.name + ("   (Word file not found)" if info.script_missing else "")
                c.addItem(label, info.script)
                c.setItemData(c.count() - 1, f"{info.script}\n{info.highlights} highlights, "
                              f"{info.links_done}/{info.links_total} links done", Qt.ToolTipRole)
            if os.path.normcase(c.itemData(c.count() - 1)) == current:
                index = c.count() - 1
        if c.count():
            c.insertSeparator(c.count())
        c.addItem("Import Another Script…", self._IMPORT)
        c.addItem("Manage Projects…", self._MANAGE)
        if index < 0:
            c.insertItem(0, "No script open", "")
            index = 0
        c.setCurrentIndex(index)
        c.blockSignals(False)

    def _script_chosen(self, index: int) -> None:
        data = self.script_combo.itemData(index)
        if data == self._IMPORT:
            self._refresh_script_combo()
            self.browse()
        elif data == self._MANAGE:
            self._refresh_script_combo()
            self.manage_projects()
        elif data and (not self.project or os.path.normcase(data) != os.path.normcase(self.project.script)):
            self.open_script(data)
        else:
            self._refresh_script_combo()

    def manage_projects(self) -> None:
        busy = {os.path.normcase(str(proj.path)) for k, (proj, _) in self.jobs.items()
                if (row := self.job_rows.get(k)) and not row.finished}
        busy |= {os.path.normcase(str(proj.path)) for proj, _ in self.stops.values()}
        dlg = ProjectsDialog(self.project.script if self.project else "", busy, self)
        dlg.exec()
        if dlg.open_path:
            self.open_script(dlg.open_path)
        elif dlg.restored:  # an earlier version was put back: reload it
            key = os.path.normcase(str(dlg.restored))
            for k in [k for k in self._open if k == key]:
                self._open.pop(k)
            if self.project and os.path.normcase(str(self.project.path)) == key:
                self.project = None
                self.open_script(dlg.restored_script, quiet=True)
                self.status.emit("Earlier version restored.")
        self._refresh_script_combo()

    def locate_script(self) -> None:
        p = self.project
        if not p:
            return
        start = os.path.dirname(p.script) if os.path.isdir(os.path.dirname(p.script)) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, f"Where is “{os.path.basename(p.script)}” now?", start,
                                              "Word documents (*.docx)")
        if path:
            self._relink(p, path)

    def _relink(self, p: Project, path: str) -> None:
        """Point a project at its script's new location; if its folder moved too (another
        drive or computer), the files on the cards follow."""
        old_key = os.path.normcase(str(p.path))
        new = p.relink(path)
        self._open.pop(old_key, None)
        for k, (proj, url) in list(self.jobs.items()):
            if proj is p:
                self.jobs[k] = (new, url)
        for k, (proj, url) in list(self.stops.items()):
            if proj is p:
                self.stops[k] = (new, url)
        old_folder = new.project_folder
        moved = 0
        if old_folder and not os.path.isdir(old_folder) and (found := guess_project_folder(new.script)):
            moved = new.relocate(old_folder, found)  # the whole project moved (another drive or computer)
            new.save()
        self.project = None
        self.open_script(new.script, quiet=True)
        if moved:
            self.status.emit(f"Reconnected to {os.path.basename(path)}, and found the project folder at "
                             f"{found}: updated {moved} file location{'s' if moved != 1 else ''}.")
        elif old_folder and not os.path.isdir(old_folder):
            self.status.emit(f"Reconnected to {os.path.basename(path)}. Now choose the project folder's new "
                             "location in Project folder, and the files on the cards will follow.")
        else:
            self.status.emit(f"Reconnected to {os.path.basename(path)}. Everything carried over.")

    def browse(self) -> None:
        start = os.path.dirname(self.state.get("script") or "") or os.path.expanduser("~/Documents")
        path, _ = QFileDialog.getOpenFileName(self, "Import a script", start, "Word documents (*.docx)")
        if path:
            self.open_script(path)

    def open_script(self, path: str, quiet: bool = False) -> None:
        key = os.path.normcase(str(project_path(path)))
        try:
            if key in self._open:
                project = self._open[key]  # already open (maybe still downloading): same project
            elif project_path(path).exists():
                project = Project(path)
            else:
                paragraphs = read_docx(path)
                removed = suggest_removed(paragraphs)
                if not quiet:
                    dlg = TrimDialog(paragraphs, removed, suggested=bool(removed), parent=self)
                    if dlg.exec() != QDialog.Accepted:
                        return
                    removed = dlg.removed()
                project = Project(path, removed=removed)
        except Exception as e:
            if not quiet:
                QMessageBox.warning(self, "Script", f"Couldn't read that script:\n\n{e}")
            return
        if self.project is not None and self.project is not project:
            self._save()  # leaving a project: make sure its latest state is on disk
        self._open[key] = project
        self.project = project
        self.selected = 0
        self._save_state("script", project.script)
        self._refresh_script_combo()
        self.locate_btn.setVisible(project.script_missing)
        self._show_saved()
        if project.script_missing and not quiet:
            self.status.emit("This script's Word file was moved or renamed. Showing the saved copy; "
                             "use Locate Script… to reconnect it.")
        self._use_project_folders()
        if project.path not in self._offered_move:
            self._offered_move.add(project.path)
            QTimer.singleShot(300, lambda p=project: self._offer_moved_folder(p))
        self.view.load(project.paragraphs, project.removed)
        self.trim_btn.setEnabled(True)
        self.refresh()
        # Warm up previews for embedded links that aren't attached yet (the ones you'll be asked about).
        attached = project.attached_urls()
        for i, p in enumerate(project.paragraphs):
            if i in project.removed:
                continue
            for link in p.links:
                if link.url not in attached:
                    self.previews.request(link.url)
        if not quiet:
            n_auto = sum(1 for h in project.highlights if h.auto)
            what = "Reopened" if project.restored else f"Imported, with {n_auto} automatic highlights from bracketed links"
            self.status.emit(f"{what}: {os.path.basename(path)}.")

    def _offer_moved_folder(self, p: Project) -> None:
        """The project's folder isn't where it was saved (it was on the other computer, or another
        drive). If a folder of the same name turns up on a drive or in the home folder, offer to
        reconnect the project to it: the script and every file on the cards."""
        if p is not self.project or not (found := find_moved_folder(p.project_folder)):
            return
        if QMessageBox.question(
                self, "Project Folder",
                f"This project's files were in\n{p.project_folder}\nwhich isn't here. Found a folder with that "
                f"name at\n{found}\n\nReconnect the project to it?") != QMessageBox.Yes:
            return
        docx = os.path.join(found, "Project Files", path_name(p.script))
        if p.script_missing and os.path.exists(docx):
            self._relink(p, docx)  # moves the files on the cards over too
            return
        moved = p.relocate(p.project_folder, found)
        self._save()
        self.project_folder.set_folder(found)
        self.refresh()
        self.status.emit(f"Reconnected to {found}: updated {moved} file location{'s' if moved != 1 else ''}.")

    def trim(self) -> None:
        p = self.project
        if not p:
            return
        dlg = TrimDialog(p.paragraphs, p.removed, suggested=False, parent=self)
        dlg.set_ok_text("&Apply")
        if dlg.exec() != QDialog.Accepted:
            return
        before = len(p.visible)
        p.set_removed(dlg.removed())
        self._save()
        self.view.load(p.paragraphs, p.removed)
        self.refresh()
        self.status.emit(f"Script trimmed: {len(p.visible)} highlights in use (was {before}).")

    # -- highlights --------------------------------------------------------------------

    def refresh(self, center_selected: bool = False) -> None:
        """Redraw the highlights on the script and the cards on the right. With
        center_selected, scroll the list so the selected highlight's card is in the middle."""
        p = self.project
        bar = self.cards_scroll.verticalScrollBar()
        keep = bar.value()
        while self.cards.count() > 1:
            item = self.cards.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        if p:
            self.view.set_highlights(p.visible, self.selected)
            self.view.suggesting = {hid for (f, hid) in self._suggesting if f == str(p.path)}
            for i, h in enumerate(p.visible, 1):
                card = HighlightCard(h, i, h.id == self.selected,
                                     suggesting=self._suggesting.get((str(p.path), h.id), ""))
                card.suggest_clicked.connect(self.suggest)
                card.lead_removed.connect(self.remove_lead)
                card.copy_source_clicked.connect(self.copy_source)
                card.retake_clicked.connect(self.retake_article)
                card.kind_switched.connect(self.switch_kind)
                card.clicked.connect(self.select)
                card.remove_clicked.connect(self.remove_highlight)
                card.add_links_clicked.connect(self.add_links)
                card.note_clicked.connect(self.edit_note)
                card.add_images_clicked.connect(self.add_images)
                card.images_dropped.connect(self.attach_images)
                card.image_detached.connect(self.detach_image)
                card.link_removed.connect(self.remove_link)
                card.link_excluded.connect(self.exclude_link)
                card.copy_all_clicked.connect(self.copy_all)
                card.retry_clicked.connect(self.retry_link)
                self.cards.insertWidget(self.cards.count() - 1, card)
                if center_selected and h.id == self.selected:
                    target = card
            self.side.setTabText(0, f"Highlights ({len(p.visible)})")
        sources = p.added_sources() if p else []
        self.added.show_sources(sources)
        self.side.setTabText(2, f"Added Sources ({len(sources)})" if sources else "Added Sources")
        if center_selected and "target" in locals():
            def bring_into_view(card=target, tries=20):
                # The list grows to fit the new cards a moment later; wait for that, then scroll.
                self.cards.activate()
                if self.cards_host.height() < card.y() + card.height() and tries:
                    QTimer.singleShot(25, lambda: bring_into_view(card, tries - 1))
                    return
                middle = card.y() + card.height() // 2 - self.cards_scroll.viewport().height() // 2
                bar.setValue(max(0, min(middle, bar.maximum())))
            QTimer.singleShot(0, bring_into_view)
        else:
            QTimer.singleShot(0, lambda: bar.setValue(keep))
        self._update_buttons()

    def _update_buttons(self) -> None:
        p = self.project
        if not p:
            self.summary.setText("No script loaded.")
            self.exec_videos.setEnabled(False)
            self.exec_shots.setEnabled(False)
            return
        vids = [r for _, r in p.refs("video")]
        arts = [r for _, r in p.refs("article")]
        vids_todo = sum(r.status in (PENDING, FAILED, SKIPPED) for r in vids)
        arts_todo = sum(r.status in (PENDING, FAILED, SKIPPED) and not r.excluded for r in arts)
        self.summary.setText(
            f"{len(p.visible)} highlights · videos {sum(r.status == DONE for r in vids)}/{len(vids)} done"
            f" · articles {sum(r.status == DONE for r in arts)}/{len(arts)} captured"
            f" · {(n_img := sum(len(h.images) for h in p.visible))} image{'s' if n_img != 1 else ''}")
        to_check = any(r.needs_check for _, r in p.refs())
        self.exec_videos.setEnabled((vids_todo > 0 or to_check) and self._sorting is None)
        self.exec_videos.setText(f"Execute Video &Downloads ({vids_todo})" if vids_todo else "Execute Video &Downloads")
        self.exec_shots.setEnabled(arts_todo > 0 and self._sorting is None)
        self.exec_shots.setText(f"Execute S&creenshotter ({arts_todo})" if arts_todo else "Execute S&creenshotter")

    def select_from_script(self, hid: int) -> None:
        """A highlight clicked in the script: bring its card (and files) to the middle of the list."""
        self.selected = hid
        self.side.setCurrentIndex(0)
        self.refresh(center_selected=True)

    def select(self, hid: int) -> None:
        self.selected = hid
        if self.project and (h := self.project.get(hid)):
            self.view.scroll_to(h)
        self.refresh()

    def _all_overlapping(self, sel) -> list[Highlight]:
        if not self.project or not sel:
            return []
        para, start, end = sel
        return [h for h in self.project.visible if h.para == para and h.start < end and start < h.end]

    def _overlapping(self, sel) -> Highlight | None:
        """The highlight a selection overlaps (the selected one first, else the biggest overlap)."""
        if not self.project or not sel:
            return None
        para, start, end = sel
        hits = [h for h in self.project.visible if h.para == para and h.start < end and start < h.end]
        if not hits:
            return None
        chosen = next((h for h in hits if h.id == self.selected), None)
        return chosen or max(hits, key=lambda h: min(end, h.end) - max(start, h.start))

    def _selection_changed(self, ok: bool) -> None:
        sel = self.view.selection_range() if ok else None
        hits = self._all_overlapping(sel)
        self.highlight_btn.setEnabled(ok and self.project is not None)
        if len(hits) > 1:
            labels = [h.label for h in hits]
            self.highlight_btn.setText("Combine Hi&ghlights " + (", ".join(labels[:-1]) + " & " + labels[-1]
                                                                  if len(labels) <= 4 else f"({len(labels)})"))
            self.highlight_btn.setToolTip(
                f"Merge these into highlight {labels[0]}, covering all their text. Their links, files, images, "
                "leads and notes all move onto it.")
            return
        h = hits[0] if hits else None
        self.highlight_btn.setText(f"Adjust Hi&ghlight {h.label}" if h else "Hi&ghlight Selection")
        self.highlight_btn.setToolTip(
            "Move this highlight onto the selected words, keeping its links, files, images and note." if h
            else "Highlight the selected words and choose which of their links to attach.")

    def _open_find(self) -> None:
        self.find_bar.show()
        self.find_box.setFocus()
        self.find_box.selectAll()
        if self.find_box.text():
            self._find_changed(self.find_box.text())

    def _close_find(self) -> None:
        self.find_bar.hide()
        self.view.set_search("")
        self.view.setFocus()

    def _find_changed(self, text: str) -> None:
        n = self.view.set_search(text)
        self.find_count.setText("" if not text else (f"{n} found" if n else "Not found"))
        if n:
            self._find_step(True)

    def _find_step(self, forward: bool) -> None:
        i, n = self.view.find_step(forward)
        if n:
            self.find_count.setText(f"{i} of {n}")

    def edit_paragraph(self, para: int, offset: int) -> None:
        p = self.project
        if not p or para in p.removed or para >= len(p.paragraphs):
            return
        original = p.edits.get(para, {}).get("original")
        dlg = EditParagraphDialog(p.paragraphs[para].text, original, offset, self)
        if dlg.exec() != QDialog.Accepted or dlg.text() == p.paragraphs[para].text:
            return
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        p.edit_paragraph(para, dlg.text())
        self._save()
        self.view.load(p.paragraphs, p.removed)
        self.refresh()
        QTimer.singleShot(0, lambda: bar.setValue(keep))
        self.status.emit("Paragraph updated (saved in this project; the Word file is unchanged).")

    def highlight_selection(self) -> None:
        p = self.project
        sel = self.view.selection_range()
        if len(hits := self._all_overlapping(sel)) > 1:
            return self.combine_highlights(hits, sel)
        if (h := self._overlapping(sel)) is not None:
            return self.adjust_highlight(h, sel)
        if not p or not sel:
            if self.view.spans_paragraphs():
                self.status.emit("Highlights stay within one paragraph. Select part of a single paragraph.")
            return
        para, start, end = sel
        candidates = [u for u in p.embedded_links(para, start, end) if u not in p.attached_urls()]
        chosen: list[str] = []
        if candidates:
            dlg = AttachDialog(candidates, self.previews, self)
            if dlg.exec() != QDialog.Accepted and not dlg.chosen:
                return
            chosen = dlg.chosen
        h = p.add(para, start, end, [LinkRef(u, link_kind(u)) for u in chosen])
        self._save()
        self.selected = h.id
        self.refresh()
        self.status.emit(f"Highlighted, with {len(chosen)} link{'s' if len(chosen) != 1 else ''} attached."
                         + ("" if candidates else " Use Add Links… on its card to attach videos or articles."))

    def adjust_highlight(self, h: Highlight, sel) -> None:
        """Move a highlight onto the selected words, keeping everything attached to it."""
        p = self.project
        para, start, end = sel
        have = {r.url for r in h.links}
        candidates = [u for u in p.embedded_links(para, start, end) if u not in have and u not in p.attached_urls()]
        if candidates:
            dlg = AttachDialog(candidates, self.previews, self)
            if dlg.exec() != QDialog.Accepted and not dlg.chosen:
                return
            h.links += [LinkRef(u, link_kind(u)) for u in dlg.chosen]
        p.adjust(h.id, para, start, end)
        self._save()
        self.selected = h.id
        self.refresh()
        self.status.emit(f"Highlight {h.label} adjusted; its links, files and note came with it.")

    def combine_highlights(self, hits: list[Highlight], sel) -> None:
        """Merge the highlights a selection touches into the first one."""
        p = self.project
        if any((str(p.path), h.id) in self._suggesting for h in hits):
            self.status.emit("Claude is still researching one of these highlights. Combine them when it's done.")
            return
        para, start, end = sel
        start, end = min([start] + [h.start for h in hits]), max([end] + [h.end for h in hits])
        have = {r.url for h in hits for r in h.links}
        candidates = [u for u in p.embedded_links(para, start, end) if u not in have and u not in p.attached_urls()]
        chosen: list[str] = []
        if candidates:
            dlg = AttachDialog(candidates, self.previews, self)
            if dlg.exec() != QDialog.Accepted and not dlg.chosen:
                return
            chosen = dlg.chosen
        locked = p.locked
        labels = [h.label for h in sorted(hits, key=lambda x: (x.para, x.start))]
        keep = p.combine([h.id for h in hits], para, start, end)
        if not keep:
            return
        keep.links += [LinkRef(u, link_kind(u)) for u in chosen]
        self._save()
        self.selected = keep.id
        self.refresh()
        others = ", ".join(labels[1:])
        msg = f"Combined highlights {', '.join(labels)} into highlight {keep.label}; everything attached came with it."
        if locked and any(r.files for r in keep.links):
            msg += f" Files already downloaded keep their old numbers ({others})."
        self.status.emit(msg)

    def remove_highlight(self, hid: int) -> None:
        if not self.project:
            return
        self.project.remove(hid)
        self._save()
        self.refresh()

    def add_links(self, hid: int) -> None:
        h = self.project.get(hid) if self.project else None
        if not h:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Add Links", "Paste TikTok, Instagram, YouTube or article links (one per line):")
        if not ok:
            return
        have = {r.url for r in h.links}
        added = [u for u in dict.fromkeys(links_from_text(text)) if u not in have]
        h.links += [LinkRef(u, link_kind(u)) for u in added]
        self._save()
        self.selected = hid
        self.refresh()
        self.status.emit(f"Added {len(added)} link{'s' if len(added) != 1 else ''}.")

    # -- Suggest (Claude) ---------------------------------------------------------------

    def suggest(self, hid: int, mode: str) -> None:
        p = self.project
        h = p.get(hid) if p else None
        if not h or (str(p.path), hid) in self._suggesting:
            return
        if not ensure_key(self):
            return
        ask = PointersDialog(h, mode, self)
        if ask.exec() != QDialog.Accepted:
            return
        if ask.text() != h.pointers:
            h.pointers = ask.text()
            self._save()
        self._suggesting[(str(p.path), hid)] = mode
        self.suggester.start(p, h, mode)
        self.selected = hid
        self.refresh()
        self.status.emit(f"Claude is researching highlight {h.label} ({sg.MODES[mode]}). This takes "
                         "a minute or so; keep working meanwhile.")

    def _on_suggest_failed(self, project: Project, hid: int, mode: str, message: str) -> None:
        self._suggesting.pop((str(project.path), hid), None)
        if project is self.project:
            self.refresh()
        h = project.get(hid)
        QMessageBox.warning(self, sg.MODES[mode], f"Highlight {h.label if h else ''}: {message}")

    def _on_suggested(self, project: Project, hid: int, mode: str, result) -> None:
        self._suggesting.pop((str(project.path), hid), None)
        if project is self.project:
            self.refresh()
        self._suggest_queue.append((project, hid, mode, result))
        if not self._showing_suggestions:
            self._show_suggestions()

    def _show_suggestions(self) -> None:
        self._showing_suggestions = True
        try:
            while self._suggest_queue:
                project, hid, mode, result = self._suggest_queue.pop(0)
                h = project.get(hid)
                if not h:
                    continue
                dlg = SuggestDialog(project, h, mode, result, self.previews, self)
                dlg.exec()
                self._apply_suggestions(project, h, dlg)
        finally:
            self._showing_suggestions = False

    def _apply_suggestions(self, project: Project, h: Highlight, dlg: SuggestDialog) -> None:
        have = {urldefrag(r.url)[0] for r in h.links}
        added = [a for a in dlg.attached if urldefrag(a.url)[0] not in have]
        h.links += [LinkRef(a.link, "article") for a in added]
        for a in dlg.skipped:
            if a.url not in h.declined:
                h.declined.append(a.url)
        searches = {(x.get("platform"), x.get("search")) for x in h.leads}
        kept = [v for v in dlg.kept_leads if (v.platform, v.search) not in searches]
        h.leads += [v.to_dict() for v in kept]
        if not (added or dlg.skipped or kept):
            return
        self._save(project)
        if project is self.project:
            self.selected = h.id
            self.refresh()
        bits = []
        if added:
            bits.append(f"attached {len(added)} article{'s' if len(added) != 1 else ''}")
        if kept:
            bits.append(f"kept {len(kept)} video lead{'s' if len(kept) != 1 else ''}")
        if bits:
            self.status.emit(f"Highlight {h.label}: " + " and ".join(bits) + ".")

    def remove_lead(self, hid: int, index: int) -> None:
        h = self.project.get(hid) if self.project else None
        if not h or not 0 <= index < len(h.leads):
            return
        lead = h.leads.pop(index)
        self._save()
        self.refresh()
        self.status.emit(f"Removed the lead “{lead.get('search', '')}” from highlight {h.label}.")

    def edit_note(self, hid: int) -> None:
        h = self.project.get(hid) if self.project else None
        if not h:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, f"Note for Highlight {h.label}", f"“{h.text[:120]}{'…' if len(h.text) > 120 else ''}”", h.note)
        if not ok:
            return
        h.note = text.strip()
        self._save()
        self.selected = hid
        self.refresh()
        self.status.emit(f"Note saved on highlight {h.label}." if h.note else f"Note removed from highlight {h.label}.")

    # -- images ------------------------------------------------------------------------

    def add_images(self, hid: int) -> None:
        folder = self.dest("image")
        patterns = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Images", folder,
                                                f"Images ({patterns});;All files (*)")
        if paths:
            self.attach_images(hid, {"files": paths, "urls": [], "image": None})

    def attach_images(self, hid: int, drop: dict) -> None:
        h = self.project.get(hid) if self.project else None
        if not h:
            return
        folder = self.dest("image")
        added, not_images, errors = [], 0, []
        for f in drop.get("files", []):
            if not is_image(f):
                not_images += 1
                continue
            try:
                path = bring_in(f, folder)
            except OSError as e:
                errors.append(f"{os.path.basename(f)}: {e.strerror or e}")
                continue
            if path not in h.images:
                h.images.append(path)
                added.append(path)
        for url in drop.get("urls", []):
            threading.Thread(target=self._download_image, args=(hid, url, folder), daemon=True).start()
        if drop.get("image") is not None and not drop.get("files") and not drop.get("urls"):
            os.makedirs(folder, exist_ok=True)
            path = unique_in(folder, "image.png")
            if QImage(drop["image"]).save(path):
                h.images.append(path)
                added.append(path)
        self._save()
        self.selected = hid
        self.refresh()
        parts = []
        if added:
            parts.append(f"attached {len(added)} image{'s' if len(added) != 1 else ''} to highlight {h.label}")
        if drop.get("urls"):
            parts.append("downloading the web image" + ("s" if len(drop["urls"]) > 1 else "") + "…")
        if not_images:
            parts.append("skipped 1 file that isn't an image" if not_images == 1
                         else f"skipped {not_images} files that aren't images")
        if errors:
            parts.append("couldn't copy " + "; ".join(errors))
        if parts:
            msg = ", ".join(parts) + "."
            self.status.emit(msg[0].upper() + msg[1:])

    def _download_image(self, hid: int, url: str, folder: str) -> None:
        try:
            self.image_downloaded.emit(hid, download(url, folder), "")
        except Exception as e:
            self.image_downloaded.emit(hid, "", str(e).splitlines()[0][:160] if str(e) else type(e).__name__)

    def _on_image_downloaded(self, hid: int, path: str, error: str) -> None:
        h = self.project.get(hid) if self.project else None
        if error or not h:
            self.status.emit(f"Couldn't save that web image: {error}")
            return
        if path not in h.images:
            h.images.append(path)
        self._save()
        self.refresh()
        self.status.emit(f"Saved {os.path.basename(path)} to highlight {h.label}.")

    def detach_image(self, hid: int, path: str) -> None:
        h = self.project.get(hid) if self.project else None
        if h and path in h.images:
            h.images.remove(path)
            self._save()
            self.refresh()
            self.status.emit(f"Detached {os.path.basename(path)} from highlight {h.label} (the file is still there).")

    def remove_link(self, hid: int, url: str) -> None:
        h = self.project.get(hid) if self.project else None
        ref = next((r for r in h.links if r.url == url), None) if h else None
        if not ref:
            return
        if ref.status == RUNNING:
            self.status.emit("That link is downloading or being captured right now. Remove it when it's finished.")
            return
        h.links.remove(ref)
        self._save()
        self.refresh()
        kept = ("" if not ref.files else " Its file stays on disk." if len(ref.files) == 1
                else f" Its {len(ref.files)} files stay on disk.")
        self.status.emit(f"Removed {_short_url(url)} from highlight {h.label}.{kept}")

    def exclude_link(self, hid: int, url: str, excluded: bool) -> None:
        h = self.project.get(hid) if self.project else None
        ref = next((r for r in h.links if r.url == url), None) if h else None
        if not ref or ref.status == RUNNING:
            return
        ref.excluded = excluded
        self._save()
        self.refresh()
        self.status.emit(f"{_short_url(url)} will be left out of the Screenshotter." if excluded
                         else f"{_short_url(url)} will be captured with the others.")

    # -- Copy Source (videos) ----------------------------------------------------------

    def copy_source(self, hid: int, url: str) -> None:
        p = self.project
        h = p.get(hid) if p else None
        ref = next((r for r in h.links if r.url == url), None) if h else None
        if not ref:
            return
        credit = ref.source or _handle_from_files(url, ref.files)
        if credit:
            QGuiApplication.clipboard().setText(credit)
            self.status.emit(f"Copied source: {credit}")
            return
        if url in self._looking_up:
            return
        self._looking_up.add(url)
        self.status.emit("Looking up the video's title and channel…")

        plat = platform_for(url)
        cookies = self._download_options(plat.key)[1] if plat else None
        named = _credit_from_name(url, ref.files)

        def work(project=p):
            from ..engine import fetch_info, oembed
            # 1. The platform's public oEmbed: title and channel, no sign-in or bot check.
            if plat and (o := oembed(plat.key, url)) and o.get("author_name"):
                credit = (f"{o.get('title', '')} | {o['author_name']}" if plat.source
                          else o["author_name"])
                self.source_found.emit(project, url, credit.strip(" |"), "")
                return
            # 2. The file's own name ("Title - Channel"), when it has one.
            if named:
                self.source_found.emit(project, url, named, "")
                return
            # 3. Ask yt-dlp, with the Downloader's cookies for that platform.
            try:
                info = fetch_info(url, cookies)
                credit = (plat.source(info) if plat and plat.source else
                          info.get("channel") or info.get("uploader") or info.get("uploader_id") or "")
                self.source_found.emit(project, url, credit.strip(" |"), "" if credit else "No channel name found.")
            except Exception as e:  # noqa: BLE001
                msg = str(e).splitlines()[0][:160] if str(e) else "failed"
                if "not a bot" in msg.lower() or "sign in" in msg.lower():
                    msg = ("YouTube wants a sign-in to look this video up. Sign in to YouTube in Firefox and set "
                           "the Downloader's YouTube Cookies to Firefox.")
                self.source_found.emit(project, url, "", msg)
        threading.Thread(target=work, daemon=True, name="copy-source").start()

    def _on_source_found(self, project: Project, url: str, credit: str, error: str) -> None:
        self._looking_up.discard(url)
        if not credit:
            self.status.emit(f"Couldn't get the source for {_short_url(url)}: {error}")
            return
        for _, ref in project.refs("video"):
            if ref.url == url:
                ref.source = credit
        self._save(project)
        QGuiApplication.clipboard().setText(credit)
        self.status.emit(f"Copied source: {credit}")

    def copy_all(self, hid: int) -> None:
        h = self.project.get(hid) if self.project else None
        files = [f for f in [*(x for r in h.links for x in r.files), *h.images] if os.path.exists(f)] if h else []
        if files:
            copy_files(files)
            self.status.emit(f"Copied {len(files)} file{'s' if len(files) != 1 else ''}. "
                             "Paste into Premiere's Project panel.")

    def retry_link(self, hid: int, url: str) -> None:
        for _, ref in self.project.refs():
            if ref.url == url and ref.status in (FAILED, SKIPPED):
                ref.status, ref.error = PENDING, ""
        self._save()
        self.refresh()
        if any(r.url == url and r.kind == "video" for _, r in self.project.refs()):
            self.execute_videos(only={url})
        else:
            self.execute_screenshots(only={url})

    # -- Execute Video Downloads ----------------------------------------------------------

    def _worker(self, key: str) -> DownloadWorker:
        if key not in self.workers:
            platform = next(p for p in PLATFORMS if p.key == key)
            w = DownloadWorker(platform, self)
            w.job_started.connect(lambda jid, k=key: self._row(k, jid, "start"))
            w.job_title.connect(lambda jid, t, k=key: (r := self.job_rows.get((k, jid))) and r.set_title(t))
            w.job_progress.connect(lambda jid, pct, s, d, k=key:
                                   (r := self.job_rows.get((k, jid))) and r.set_progress(pct, s, d))
            w.job_source.connect(lambda jid, src, k=key: self._job_sources.__setitem__((k, jid), src))
            w.job_finished.connect(lambda jid, o, m, f, k=key: self._on_video_finished(k, jid, o, m, f))
            self.workers[key] = w
        return self.workers[key]

    def _row(self, key: str, jid: int, what: str) -> None:
        if row := self.job_rows.get((key, jid)):
            row.set_started()

    def _download_options(self, key: str) -> tuple[str, dict | None]:
        """The Downloader's Quality and Cookies choices, as that platform's preset."""
        from ..platforms import format_for
        from .downloader_page import cookie_options, downloader_state
        st = downloader_state(self.settings)
        platform = next(p for p in PLATFORMS if p.key == key)
        return format_for(platform, st.get("quality", "")), cookie_options(st)

    def _check_posts_first(self, then: str) -> bool:
        """X and Reddit posts might hold a video or just text, and their addresses don't say:
        look each one up (once) so it goes to the right Execute. True if that's now running,
        and `then` ("videos" or "shots") carries on when it's done."""
        p = self.project
        urls = list(dict.fromkeys(r.url for _, r in p.refs() if r.needs_check))
        if self._sorting is not None:
            self.status.emit("Still checking X and Reddit posts…")
            return True
        if not urls:
            return False
        self._sorting = p
        self._update_buttons()
        cookie_opts = {}
        for url in urls:
            if plat := platform_for(url):
                cookie_opts[url] = self._download_options(plat.key)[1]

        def work(project=p):
            from ..engine import post_kind
            results = {}
            for i, url in enumerate(urls, 1):
                self.status.emit(f"Checking X and Reddit posts for videos ({i} of {len(urls)})…")
                results[url] = post_kind(url, cookie_opts.get(url))
            self.posts_checked.emit(project, results, then)
        threading.Thread(target=work, daemon=True, name="check-posts").start()
        return True

    def _on_posts_checked(self, project: Project, results: dict, then: str) -> None:
        self._sorting = None
        for _, ref in project.refs():
            if ref.url in results and ref.needs_check:  # (not if you switched it meanwhile)
                kind, note = results[ref.url]
                ref.kind = "video" if kind == "video" else "article"
                ref.sorted_by, ref.sort_note = ("unsure", note) if kind == "unsure" else ("checked", "")
        self._save(project)
        kinds = [k for k, _ in results.values()]
        summary = f"Checked {len(kinds)} X and Reddit post{'s' if len(kinds) != 1 else ''}: " \
                  f"{kinds.count('video')} with video, {kinds.count('article')} without"
        if n := kinds.count("unsure"):
            why = next(note for k, note in results.values() if k == "unsure")
            summary += f", {n} couldn't be checked (treated as articles). {why}"
        else:
            summary += "."
        if project is self.project:
            self.refresh()
            if then == "videos":  # checked=True: "unsure" posts wait for the next Execute
                self.execute_videos(checked=True)
            else:
                self.execute_screenshots(checked=True)
        self.status.emit(summary)

    def switch_kind(self, url: str) -> None:
        """Your call on an X or Reddit post: Video <-> Article. Applies wherever it's linked."""
        p = self.project
        refs = [r for _, r in p.refs() if r.url == url] if p else []
        if not refs or any(r.status == RUNNING for r in refs):
            return
        kind = "article" if refs[0].kind == "video" else "video"
        for ref in refs:
            ref.kind, ref.sorted_by, ref.sort_note = kind, "manual", ""
            ref.status, ref.error, ref.excluded = PENDING, "", False
        self._save()
        self.refresh()
        self.status.emit(f"{_short_url(url)} is now {'a video' if kind == 'video' else 'an article'}.")

    def execute_videos(self, only: set[str] | None = None, checked: bool = False) -> None:
        p = self.project
        if not p:
            return
        if only is None and not checked and self._check_posts_first("videos"):
            return
        folder = self.dest("video")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Script", f"Can't use the videos folder:\n\n{e.strerror or e}")
            return
        urls = list(dict.fromkeys(
            r.url for _, r in p.refs("video")
            if r.status in (PENDING, FAILED, SKIPPED) and (only is None or r.url in only)))
        if not urls:
            self.status.emit("Every video link is already downloaded.")
            return
        for url in urls:
            self._submit_video(url, folder)
        self._save()
        self.refresh()
        self.side.setCurrentIndex(1)
        self.status.emit(f"Downloading {len(urls)} video{'s' if len(urls) != 1 else ''} to {folder}.")

    def _submit_video(self, url: str, folder: str, project: Project | None = None) -> None:
        project = project or self.project
        platform = platform_for(url)
        quality, cookies = self._download_options(platform.key)
        owner = next((h for h, r in project.refs("video") if r.url == url), None)
        prefix = owner.prefix if owner else ""
        job = self._worker(platform.key).submit(url, folder, quality, cookies, prefix=prefix)
        self.jobs[(platform.key, job.id)] = (project, url)
        name = os.path.splitext(os.path.basename(project.script))[0]
        name = name if len(name) <= 28 else name[:27] + "…"
        row = JobRow(job.id, url, f"{name} · Highlight {owner.label} · {platform.name} · {quality}" if owner
                     else f"{name} · {platform.name} · {quality}")
        row.cancel_clicked.connect(lambda jid, k=platform.key: self.workers[k].cancel(jid))
        row.retry_clicked.connect(lambda jid, k=platform.key, u=url: self._retry_video(k, jid, u))
        self.job_rows[(platform.key, job.id)] = row
        self.dl_empty.hide()
        self.dl_list.insertWidget(self.dl_list.count() - 2, row)
        for _, ref in project.refs("video"):
            if ref.url == url:
                ref.status, ref.error = RUNNING, ""

    def _retry_video(self, key: str, jid: int, url: str) -> None:
        project = (self.jobs.get((key, jid)) or (self.project, url))[0]
        if row := self.job_rows.pop((key, jid), None):
            self.dl_list.removeWidget(row)
            row.deleteLater()
        self._submit_video(url, project.dest("video"), project)
        self._save(project)
        self.refresh()

    def _on_video_finished(self, key: str, jid: int, outcome: str, message: str, files: list) -> None:
        project, url = self.jobs.get((key, jid)) or (None, None)
        if row := self.job_rows.get((key, jid)):
            if not (row.state == "cancelled" and outcome == "cancelled"):
                row.set_finished(outcome, message, files)
        if not url or not project:
            return
        for _, ref in project.refs("video"):
            if ref.url != url:
                continue
            if outcome == "done":
                ref.status, ref.files, ref.error = DONE, list(files), ""
                ref.source = self._job_sources.get((key, jid), "") or ref.source
            elif outcome == "failed":
                ref.status, ref.error = FAILED, message
            else:
                ref.status = PENDING
        self._save(project)  # saved to its own project, even if you've switched scripts since
        if project is self.project:
            self.refresh()
        if not self.active_count() and self.project:
            vids = [r for _, r in self.project.refs("video")]
            failed = sum(r.status == FAILED for r in vids)
            self.status.emit("Video downloads finished" + (f": {failed} failed (see Video Downloads)." if failed else "."))

    # -- Execute Screenshotter -------------------------------------------------------------

    def execute_screenshots(self, only: set[str] | None = None, checked: bool = False) -> None:
        p = self.project
        if not p:
            return
        if only is None and not checked and self._check_posts_first("shots"):
            return
        folder = self.dest("shot")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Script", f"Can't use the screenshots folder:\n\n{e.strerror or e}")
            return
        queued: list[str] = []
        for h, ref in p.refs("article"):
            if ref.status not in (PENDING, FAILED, SKIPPED) or ref.excluded or ref.url in queued:
                continue
            if only is not None and ref.url not in only:
                continue
            cap = to_capture(ref.url)
            stop = self.session.add(cap.url, cap.quote, folder, context=h.text.replace("\u2028", " "), prefix=h.prefix,
                                    label=h.label, note=h.note)
            self.stops[stop.id] = (p, ref.url)
            queued.append(ref.url)
        for _, ref in p.refs("article"):
            if ref.url in queued and not ref.excluded:
                ref.status, ref.error = RUNNING, ""
        self._save()
        self.refresh()
        if queued:
            self.status.emit(f"Opening the capture window for {len(queued)} article{'s' if len(queued) != 1 else ''}.")
        else:
            self.status.emit("Every article is already captured or set to Skip.")

    def retake_article(self, hid: int, url: str) -> None:
        """Capture one article again. Its existing shots stay; new ones are numbered on."""
        p = self.project
        h = p.get(hid) if p else None
        if not h or not any(r.url == url and r.status == DONE for r in h.links):
            return
        folder = self.dest("shot")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Script", f"Can't use the screenshots folder:\n\n{e.strerror or e}")
            return
        cap = to_capture(url)
        stop = self.session.add(cap.url, cap.quote, folder, context=h.text.replace("\u2028", " "), prefix=h.prefix,
                                label=h.label, note=h.note)
        self.stops[stop.id] = (p, url)
        self._retakes.add(stop.id)
        for _, ref in p.refs("article"):
            if ref.url == url:
                ref.status, ref.error = RUNNING, ""
        self._save()
        self.refresh()
        self.status.emit(f"Re-taking {_short_url(url)} for highlight {h.label}. Your existing shots are kept.")

    def _refs_for_stop(self, stop_id: int):
        project, url = self.stops.get(stop_id) or (None, None)
        if url and project:
            for _, ref in project.refs("article"):
                if ref.url == url:
                    yield ref

    def _stop_project(self, stop_id: int) -> Project | None:
        return (self.stops.get(stop_id) or (None, None))[0]

    def _on_stop_started(self, stop_id: int) -> None:
        if stop_id in self.stops and stop_id not in self._retakes:
            for ref in self._refs_for_stop(stop_id):
                ref.files = []  # a fresh pass replaces what this link had before (a Re-Take adds to it)

    def _on_shot_saved(self, stop_id: int, path: str) -> None:
        for ref in self._refs_for_stop(stop_id):
            ref.files.append(path)
        if (project := self._stop_project(stop_id)) is not None:
            self._save(project)
            if project is self.project:
                self.refresh()

    def _on_stop_finished(self, stop_id: int, outcome: str) -> None:
        if stop_id not in self.stops:
            return
        retake = stop_id in self._retakes
        self._retakes.discard(stop_id)
        for ref in self._refs_for_stop(stop_id):
            if retake and ref.files:  # the earlier shots still stand, whatever happened this time
                ref.status, ref.error = DONE, ""
                if outcome.startswith("failed:"):
                    self.status.emit(f"The re-take of {_short_url(ref.url)} failed: {outcome[7:]}")
            elif outcome == "done" and ref.files:
                ref.status = DONE
            elif outcome.startswith("failed:"):
                ref.status, ref.error = FAILED, outcome[7:]
            else:
                ref.status = SKIPPED
        project = self._stop_project(stop_id)
        self.stops.pop(stop_id, None)
        self._save(project)
        if project is self.project:
            self.refresh()

    def _on_session_ended(self, saved: int) -> None:
        for project in self._open.values():
            if any(r.status == RUNNING for _, r in project.refs("article")):
                for _, r in project.refs("article"):
                    if r.status == RUNNING:
                        r.status = DONE if r.files else SKIPPED
                self._save(project)
        self.refresh()

    # -- drag a script onto the page ----------------------------------------------------------

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(u.toLocalFile().lower().endswith(".docx") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".docx"):
                self.open_script(u.toLocalFile())
                return
