"""Suggest: the API key dialog, the background worker that asks Claude, and the window
that shows what came back (articles to attach, video leads to keep)."""
from __future__ import annotations

import logging
import os
import sys
import threading
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..script import suggest as sg
from ..script.previews import PreviewService
from ..script.project import Highlight, Project

log = logging.getLogger("mediatheque.suggest")


# -- the API key ---------------------------------------------------------------------------

class ApiKeyDialog(QDialog):
    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Claude API Key")
        self.resize(560, 0)
        intro = QLabel((reason + "\n\n" if reason else "")
                       + "Suggest asks Claude (Anthropic's API) to research sources. Paste your Anthropic API "
                         f"key below. It's kept in {'the Mac Keychain' if sys.platform == 'darwin' else 'Windows Credential Manager'}, not in a file, and only sent to "
                         "Anthropic. Usage is billed to your Anthropic account (roughly 10–30¢ a highlight).")
        intro.setWordWrap(True)
        link = QLabel(f'<a href="{sg.CONSOLE_URL}">Get or manage API keys in the Anthropic Console</a>')
        link.setOpenExternalLinks(True)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("sk-ant-…")
        self.has_key = bool(sg.get_key())
        if self.has_key:
            self.edit.setPlaceholderText("A key is saved. Paste a new one to replace it.")
        self.removed = False
        save = QPushButton("&Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        remove = QPushButton("&Remove Saved Key")
        remove.setEnabled(self.has_key)
        remove.clicked.connect(self._remove)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(cancel)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addWidget(intro)
        col.addWidget(link)
        col.addWidget(self.edit)
        col.addLayout(row)

    def _save(self) -> None:
        key = self.edit.text().strip()
        if not key:
            if self.has_key:
                self.accept()
            return
        if not key.startswith("sk-ant-"):
            QMessageBox.warning(self, "Claude API Key", "That doesn't look like an Anthropic API key "
                                                         "(they start with “sk-ant-”).")
            return
        try:
            sg.set_key(key)
        except Exception as e:
            QMessageBox.warning(self, "Claude API Key", f"Couldn't save the key:\n\n{e}")
            return
        self.accept()

    def _remove(self) -> None:
        sg.clear_key()
        self.removed = True
        self.accept()


def ensure_key(parent) -> bool:
    if sg.get_key():
        return True
    return ApiKeyDialog(parent, "Suggest needs an Anthropic API key first.").exec() == QDialog.Accepted \
        and bool(sg.get_key())


# -- the pointers box ------------------------------------------------------------------------

class PointersDialog(QDialog):
    """Opens before each Suggest: optional extra context or pointers for Claude."""

    def __init__(self, h: Highlight, mode: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{sg.MODES[mode]}: Highlight {h.label}")
        self.resize(620, 0)
        passage = h.text if len(h.text) <= 200 else h.text[:197].rstrip() + "…"
        head = _label(f"<b>{h.label}.</b> “{passage}”", rich=True)
        self.edit = QPlainTextEdit(h.pointers)
        self.edit.setPlaceholderText("e.g. “Find the 2023 Bloomberg piece on this”, “US sources only”, "
                                     "“The CEO's interview, not the ad”, “Recent TikToks reacting to it”")
        self.edit.setTabChangesFocus(True)
        lh = self.edit.fontMetrics().lineSpacing()
        self.edit.setFixedHeight(lh * 4 + 16)
        self.edit.selectAll()
        go = QPushButton("&Ask Claude")
        go.setObjectName("primary")
        go.setToolTip("Enter")
        go.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.edit.installEventFilter(self)
        row = QHBoxLayout()
        row.addWidget(_label("Enter to ask · Shift+Enter for a new line", muted=True), 1)
        row.addWidget(go)
        row.addWidget(cancel)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addWidget(head)
        col.addWidget(self.edit)
        col.addLayout(row)
        self.edit.setFocus()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if (obj is self.edit and e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not e.modifiers() & Qt.ShiftModifier):
            self.accept()
            return True
        return super().eventFilter(obj, e)

    def text(self) -> str:
        return self.edit.toPlainText().strip()


# -- asking Claude, off the UI thread --------------------------------------------------------

def _friendly(e: Exception) -> str:
    try:
        import anthropic
    except ImportError:
        return str(e)
    if isinstance(e, sg.SuggestError):
        return str(e)
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic refused the API key. Set it again under Tools > Claude API Key…"
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This API key isn't allowed to use that model or web search. Check it in the Anthropic Console."
    if isinstance(e, anthropic.RateLimitError):
        return "Anthropic's rate limit was hit. Wait a minute and try again."
    if isinstance(e, anthropic.BadRequestError):
        msg = getattr(e, "message", str(e))
        if "credit" in msg.lower() or "billing" in msg.lower():
            return "Your Anthropic account is out of credit. Add some in the Anthropic Console."
        return f"Anthropic rejected the request: {msg}"
    if isinstance(e, anthropic.APIStatusError):
        return "Anthropic's servers had a problem. Try again in a moment."
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach Anthropic. Check the internet connection."
    return f"Suggest failed: {str(e).splitlines()[0][:200] if str(e) else type(e).__name__}"


class SuggestWorker(QObject):
    finished = Signal(object, int, str, object)  # project, highlight id, mode, Suggestions
    failed = Signal(object, int, str, str)       # project, highlight id, mode, message
    status = Signal(str)

    def __init__(self, parent=None, client_factory=None):
        super().__init__(parent)
        self.client_factory = client_factory or sg.make_client

    def start(self, project: Project, h: Highlight, mode: str) -> None:
        req = build_request(project, h, mode)
        threading.Thread(target=self._run, args=(project, h.id, mode, req), daemon=True,
                         name=f"suggest-{h.id}").start()

    def _run(self, project: Project, hid: int, mode: str, req: sg.Request) -> None:
        try:
            client = self.client_factory()
            examples = sg.gather_examples()
            if sg.profile_stale(examples):
                self.status.emit("Updating your sourcing profile from past scripts…")
                try:
                    sg.build_profile(client, examples)
                except Exception:
                    log.exception("Couldn't build the sourcing profile; carrying on without the update")
            req.profile = sg.load_profile().get("text", "")
            req.examples = sg.similar(examples, req.passage + " " + req.note, skip=(str(project.path), hid),
                                      project_file=str(project.path))
            self.status.emit(f"Claude is researching highlight {req.label}…")
            result = sg.suggest(client, req)
        except Exception as e:  # noqa: BLE001 (reported to the editor in plain words)
            log.exception("Suggest failed for highlight %s", hid)
            self.failed.emit(project, hid, mode, _friendly(e))
            return
        log.info("Suggest for %s (%s): %d articles, %d leads, %d dropped, ~$%.3f, searches %s",
                 req.label, mode, len(result.articles), len(result.leads), result.dropped, result.cost,
                 result.searches)
        self.finished.emit(project, hid, mode, result)


def build_request(project: Project, h: Highlight, mode: str) -> sg.Request:
    paras = project.paragraphs
    shown = [i for i in range(len(paras)) if i not in project.removed]
    script = "\n\n".join(paras[i].text.replace(" ", "\n") for i in shown if paras[i].text.strip())
    around = "\n\n".join(paras[i].text.replace(" ", "\n") for i in shown
                         if abs(i - h.para) <= 1 and paras[i].text.strip())
    return sg.Request(
        mode=mode, script_title=os.path.splitext(os.path.basename(project.script))[0], script_text=script,
        label=h.label, passage=h.text.replace(" ", " "), around=around, note=h.note,
        attached=[r.url for r in h.links], declined=list(h.declined),
        leads=[f"{x.get('platform', '')}: {x.get('search', '')}" for x in h.leads], pointers=h.pointers)


# -- the results window --------------------------------------------------------------------

def _host(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.")


class _Row(QFrame):
    """One suggestion with its two choices. Tinted once decided."""

    def __init__(self, yes_text: str, parent=None):
        super().__init__(parent)
        self.choice: bool | None = None
        self.setObjectName("suggestRow")
        self.body = QVBoxLayout()
        self.body.setSpacing(3)
        self.yes = QPushButton(yes_text)
        self.yes.setObjectName("primary")
        self.no = QPushButton("Skip")
        self.yes.clicked.connect(lambda: self.decide(True))
        self.no.clicked.connect(lambda: self.decide(False))
        self.buttons = QVBoxLayout()
        self.buttons.setSpacing(4)
        self.buttons.addWidget(self.yes)
        self.buttons.addWidget(self.no)
        self.buttons.addStretch(1)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(10, 8, 10, 8)
        self.row.setSpacing(12)
        self.row.addLayout(self.body, 1)
        self.row.addLayout(self.buttons)
        self._paint()

    def decide(self, yes: bool) -> None:
        self.choice = None if self.choice is yes else yes  # clicking again undoes it
        self._paint()

    def _paint(self) -> None:
        bg = {True: "#e2f4e2", False: "#ececec", None: "#ffffff"}[self.choice]
        self.setStyleSheet(f"QFrame#suggestRow {{ background: {bg}; border: 1px solid #a0a0a0; }}")
        self.yes.setText(("✓ " if self.choice is True else "") + self.yes.text().removeprefix("✓ "))
        self.no.setText("✓ Skip" if self.choice is False else "Skip")


def _label(text: str, *, muted=False, bold=False, wrap=True, rich=False) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    if muted:
        lab.setObjectName("muted")
    if bold:
        f = lab.font()
        f.setBold(True)
        lab.setFont(f)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse | (Qt.LinksAccessibleByMouse if rich else Qt.NoTextInteraction))
    if rich:
        lab.setOpenExternalLinks(True)
    return lab


class SuggestDialog(QDialog):
    """What Claude found for one highlight: Attach / Skip each article, Keep / Skip each lead."""

    def __init__(self, project: Project, h: Highlight, mode: str, result: sg.Suggestions,
                 previews: PreviewService, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{sg.MODES[mode]}: Highlight {h.label}")
        screen = (parent.screen() if parent else self.screen()).availableGeometry()
        self.resize(min(900, int(screen.width() * 0.8)), min(760, int(screen.height() * 0.85)))
        self.result = result
        self.previews = previews
        self.article_rows: list[tuple[_Row, sg.ArticlePick]] = []
        self.lead_rows: list[tuple[_Row, sg.VideoLead]] = []
        self._thumbs: dict[str, QLabel] = {}

        passage = h.text if len(h.text) <= 260 else h.text[:257].rstrip() + "…"
        head = _label(f"<b>{h.label}.</b> “{passage}”", rich=True)
        if project.script:
            head.setToolTip(os.path.basename(project.script))
        top = QVBoxLayout()
        top.addWidget(head)
        if h.pointers:
            top.addWidget(_label(f"Your pointers: {h.pointers}", muted=True))
        if result.comment:
            c = _label(f"Claude: {result.comment}")
            c.setStyleSheet("background: #ffffe1; border: 1px solid #c8c890; padding: 5px 7px;")
            top.addWidget(c)

        host = QWidget()
        lst = QVBoxLayout(host)
        lst.setContentsMargins(0, 0, 4, 0)
        lst.setSpacing(6)
        if result.articles:
            lst.addWidget(_label(f"Articles ({len(result.articles)})", bold=True))
            for a in result.articles:
                lst.addWidget(self._article_row(a))
        if result.leads:
            lst.addWidget(_label(f"Video leads ({len(result.leads)}): search, find the clip, then "
                                 "add its link with Add Links…", bold=True))
            for v in result.leads:
                lst.addWidget(self._lead_row(v))
        if not result.articles and not result.leads:
            lst.addWidget(_label("Claude didn't find anything it was confident in for this passage.", muted=True))
        lst.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        scroll.setFrameShape(QFrame.NoFrame)

        bits = []
        if result.searches:
            bits.append(f"{len(result.searches)} web search{'es' if len(result.searches) != 1 else ''}")
        if result.cost:
            bits.append(f"about {result.cost * 100:.0f}¢" if result.cost >= 0.01 else "under 1¢")
        if result.dropped:
            bits.append(f"{result.dropped} link{'s' if result.dropped != 1 else ''} left out "
                        "(not found in Claude's searches, or already used)")
        foot = _label(" · ".join(bits), muted=True)
        if result.searches:
            foot.setToolTip("Searched for:\n" + "\n".join(f"• {s}" for s in result.searches))
        done = QPushButton("&Done")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(foot, 1)
        bottom.addWidget(done)

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addLayout(top)
        col.addWidget(scroll, 1)
        col.addLayout(bottom)

        previews.ready.connect(self._on_preview)
        for a in result.articles:
            previews.request(a.url)

    def _article_row(self, a: sg.ArticlePick) -> QWidget:
        row = _Row("&Attach")
        thumb = QLabel("Loading preview…")
        thumb.setFixedSize(220, 132)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setFrameShape(QFrame.StyledPanel)
        thumb.setFrameShadow(QFrame.Sunken)
        thumb.setObjectName("muted")
        self._thumbs[a.url] = thumb
        row.row.insertWidget(0, thumb, 0, Qt.AlignTop)
        row.body.addWidget(_label(a.title or _host(a.url), bold=True))
        row.body.addWidget(_label(f'{a.outlet + " · " if a.outlet else ""}<a href="{a.url}">{_host(a.url)}</a>',
                                  muted=True, rich=True))
        if a.why:
            row.body.addWidget(_label(a.why))
        if a.quote:
            q = _label(f"“{a.quote}”")
            q.setStyleSheet("font-style: italic; color: #303030; border-left: 2px solid #c8b450; padding-left: 6px;")
            q.setToolTip("The Screenshotter will open the page on this sentence and flash it.")
            row.body.addWidget(q)
        cached = self.previews.cached(a.url)
        if cached:
            self._on_preview(a.url, *cached)
        self.article_rows.append((row, a))
        return row

    def _lead_row(self, v: sg.VideoLead) -> QWidget:
        row = _Row("&Keep Lead")
        row.yes.setToolTip("Save this lead on the highlight's card, with a Search button.")
        row.body.addWidget(_label(f"{v.platform}: “{v.search}”", bold=True))
        if v.look_for:
            row.body.addWidget(_label(f"Look for: {v.look_for}"))
        if v.why:
            row.body.addWidget(_label(v.why, muted=True))
        go = QPushButton(f"Search {v.platform if v.platform != 'Any' else 'the Web'}")
        go.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(sg.search_url(v))))
        row.buttons.insertWidget(0, go)
        self.lead_rows.append((row, v))
        return row

    def _on_preview(self, url: str, data, title: str) -> None:
        thumb = self._thumbs.get(url)
        if thumb is None:
            return
        pm = QPixmap()
        if data and pm.loadFromData(bytes(data)):
            dpr = self.devicePixelRatioF()
            pm = pm.scaled(int(thumb.width() * dpr), int(thumb.height() * dpr), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            thumb.setPixmap(pm)
        else:
            thumb.setText("No preview")

    # -- what the editor chose -------------------------------------------------------------

    @property
    def attached(self) -> list[sg.ArticlePick]:
        return [a for r, a in self.article_rows if r.choice is True]

    @property
    def skipped(self) -> list[sg.ArticlePick]:
        return [a for r, a in self.article_rows if r.choice is False]

    @property
    def kept_leads(self) -> list[sg.VideoLead]:
        return [v for r, v in self.lead_rows if r.choice is True]

    def done(self, r: int) -> None:  # noqa: D401 (Qt override)
        try:
            self.previews.ready.disconnect(self._on_preview)
        except (RuntimeError, TypeError):
            pass
        super().done(r)
