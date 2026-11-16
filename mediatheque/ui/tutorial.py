"""Help > Script Tutorial…: a guided tour of the Script tool, on a practice script.

A small coach panel floats over the main window beside the part it's talking about, which
gets an outline. The steps (wording, what to outline, what to wait for) are in
mediatheque/tutorial_steps.py. Hands-on steps move on by themselves when the Script page
reports the change (its `changed` signal); nothing is polled.

The practice script is copied into ~/Mediatheque Tutorial/Project Files and opened as a
normal project, so the editor's real work is never touched. Delete the Practice Project
removes that folder (only if it has the tour's marker file) and the project's saved file.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..osutil import MOD_KEY
from ..script.project import list_backups, project_path
from ..tutorial_steps import STEPS
from .theme import FACE, NAVY, TIP, TITLE_END, WHITE, WINDOW, bevel, device_space, ui_font

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "tutorial"
SAMPLE = ASSETS / "Sample Script.docx"
PRACTICE_ROOT = Path.home() / "Mediatheque Tutorial"
PRACTICE_SCRIPT = PRACTICE_ROOT / "Project Files" / "Sample Script.docx"
MARKER = PRACTICE_ROOT / ".mediatheque-tutorial"  # proves the folder is the tour's, before deleting it
SUBFOLDERS = ("BRoll", "Screen Captures", "Screen Records", "Images")
TABS = {"highlights": 0, "downloads": 1, "added": 2}
PANEL_WIDTH = 340
GAP = 12


# -- the practice project ---------------------------------------------------------------------

def practice_file() -> Path:
    return project_path(str(PRACTICE_SCRIPT))


def practice_exists() -> bool:
    return MARKER.exists() or practice_file().exists()


def is_practice(project) -> bool:
    return project is not None and os.path.normcase(str(project.path)) == os.path.normcase(str(practice_file()))


def make_practice() -> str:
    """A fresh copy of the practice script in its own throwaway project folder."""
    (PRACTICE_ROOT / "Project Files").mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (PRACTICE_ROOT / sub).mkdir(exist_ok=True)
    MARKER.write_text("Made by Mediatheque's Script Tutorial. Help > Delete Practice Project removes this folder.\n",
                      encoding="utf-8")
    shutil.copyfile(SAMPLE, PRACTICE_SCRIPT)
    return str(PRACTICE_SCRIPT)


def delete_practice(page, parent: QWidget | None, quiet: bool = False) -> bool:
    """Remove the practice project: its folder (BRoll, Screen Captures… and the script) and its
    saved project file with backups. Nothing else. True if it's gone."""
    pf = practice_file()
    if page.project_busy(pf):
        if not quiet:
            QMessageBox.information(
                parent, "Practice Project",
                "The practice project is still downloading or capturing. Let it finish (or cancel it on the "
                "Video Downloads tab, or press Esc in the capture window), then choose Help > Delete Practice "
                "Project.")
        return False
    page.close_project(pf)
    problems = []
    for f in [pf, *list_backups(pf)]:
        try:
            f.unlink(missing_ok=True)
        except OSError as e:
            problems.append(str(e))
    if MARKER.exists():
        def failed(_func, path, exc) -> None:  # exc: the exception (onexc) or its sys.exc_info() (onerror)
            problems.append(f"{path}: {exc[1] if isinstance(exc, tuple) else exc}")
        # onexc replaced onerror in Python 3.12
        shutil.rmtree(PRACTICE_ROOT, **({"onexc": failed} if sys.version_info >= (3, 12) else {"onerror": failed}))
    root = os.path.normpath(str(PRACTICE_ROOT))
    if root in page.project_store.folders():
        page.project_store.remove(root)
    if os.path.normcase(page.state.get("script") or "") == os.path.normcase(str(PRACTICE_SCRIPT)):
        page.state["script"] = ""
        page.settings.save()
    if problems and not quiet:
        QMessageBox.warning(parent, "Practice Project",
                            "Some of the practice files couldn't be deleted:\n\n" + "\n".join(problems[:5])
                            + f"\n\nYou can delete the folder “{PRACTICE_ROOT}” yourself.")
    return not problems


# -- what the steps can point at and wait for ------------------------------------------------------

def _counts(project) -> dict:
    hs = project.visible if project else []
    refs = [r for h in hs for r in h.links]
    return {
        "open": is_practice(project),
        "n": len(hs),
        "mine": sum(not h.auto for h in hs),
        "notes": sum(bool(h.note) for h in hs),
        "manual": sum(r.sorted_by == "manual" for r in refs),
    }


class _Overlay(QWidget):
    """The outline around what a step is talking about. It covers the window but lets every
    click through."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.rects: list[QRect] = []

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self.rects:
            return
        p = QPainter(self)
        with device_space(p) as px:
            t = px.t
            for r in self.rects:
                d = px.rect(r)
                for i, color in enumerate((QColor("#ffff00"), QColor("#c00000"), QColor("#c00000"),
                                           QColor("#ffff00"))):
                    ring = d.adjusted(-(i + 1) * t, -(i + 1) * t, (i + 1) * t, (i + 1) * t)
                    bevel(p, ring, ((color, color),), t)
        p.end()


class _TitleStrip(QWidget):
    """The panel's little title bar, in the classic gradient. Drag it to move the panel."""
    dragged = Signal(QPoint)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.right = ""  # "Step 3 of 12"
        self.setFixedHeight(22)
        self.setCursor(Qt.SizeAllCursor)
        self._grab: QPoint | None = None

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, NAVY)
        g.setColorAt(1, TITLE_END)
        p.fillRect(self.rect(), g)
        p.setPen(WHITE)
        p.setFont(ui_font(9, bold=True))
        p.drawText(self.rect().adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text)
        p.setFont(ui_font(9))
        p.drawText(self.rect().adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignRight, self.right)
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._grab is not None:
            now = e.globalPosition().toPoint()
            self.dragged.emit(now - self._grab)
            self._grab = now

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._grab = None


class CoachPanel(QFrame):
    """The tour's panel: title, text, an optional "try it", Back / Next / Exit Tour."""
    back = Signal()
    next = Signal()
    exit = Signal()
    action = Signal(str)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.moved_by_hand = False
        self.title_strip = _TitleStrip("Script Tour")
        self.title_strip.dragged.connect(self._drag)
        self.heading = QLabel()
        self.heading.setFont(ui_font(10, bold=True))
        self.heading.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.try_it = QLabel()
        self.try_it.setWordWrap(True)
        self.try_it.setTextFormat(Qt.RichText)
        self.try_it.setStyleSheet(f"background: {TIP.name()}; border: 1px solid #a0a070; padding: 5px 6px;")
        self.action_btn = QPushButton()
        self.action_btn.setObjectName("primary")
        self.action_btn.clicked.connect(lambda: self.action.emit(self.action_btn.property("what")))
        text_host = QWidget()
        tcol = QVBoxLayout(text_host)
        tcol.setContentsMargins(0, 0, 0, 0)
        tcol.setSpacing(8)
        tcol.addWidget(self.heading)
        tcol.addWidget(self.body)
        tcol.addWidget(self.try_it)
        arow = QHBoxLayout()
        arow.addStretch(1)
        arow.addWidget(self.action_btn)
        tcol.addLayout(arow)
        tcol.addStretch(1)
        self.scroll = QScrollArea()  # in a small window, the text scrolls instead of the panel overflowing
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(text_host)
        self.scroll.viewport().setAutoFillBackground(False)
        text_host.setAutoFillBackground(False)
        self.back_btn = QPushButton("< &Back")
        self.next_btn = QPushButton("&Next >")
        self.exit_btn = QPushButton("E&xit Tour")
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        self.exit_btn.clicked.connect(self.exit)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        brow.addStretch(1)
        brow.addWidget(self.back_btn)
        brow.addWidget(self.next_btn)
        brow.addWidget(self.exit_btn)
        inner = QVBoxLayout()
        inner.setContentsMargins(10, 8, 10, 10)
        inner.setSpacing(8)
        inner.addWidget(self.scroll, 1)
        inner.addLayout(brow)
        col = QVBoxLayout(self)
        col.setContentsMargins(3, 3, 3, 3)
        col.setSpacing(0)
        col.addWidget(self.title_strip)
        col.addLayout(inner, 1)
        self.setFixedWidth(PANEL_WIDTH)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        with device_space(p) as px:
            bevel(p, px.rect(self.rect()), WINDOW, px.t, FACE)
        p.end()

    def show_step(self, step: dict, index: int, count: int, done: bool, folder: str) -> None:
        def fmt(text: str) -> str:
            text = text.replace("{mod}", MOD_KEY).replace("{folder}", folder)
            return "<br><br>".join(p.replace("\n", "<br>") for p in re.split(r"\n\s*\n", text.strip()))
        self.heading.setText(step["title"])
        self.body.setText(fmt(step["text"]))
        if step.get("try_it"):
            self.try_it.setText(("<b>Done.</b> Click Next to go on." if done else "<b>Try it:</b> " + fmt(step["try_it"])
                                 + ("<br><span style='color:#606060'>(It moves on by itself once it's done, or "
                                    "click Next to skip it.)</span>" if step.get("wait") else "")))
            self.try_it.show()
        else:
            self.try_it.hide()
        if step.get("action"):
            label, what = step["action"]
            self.action_btn.setText(label)
            self.action_btn.setProperty("what", what)
            self.action_btn.show()
        else:
            self.action_btn.hide()
        self.title_strip.right = f"Step {index + 1} of {count}"
        self.title_strip.update()
        self.back_btn.setEnabled(index > 0)
        last = index == count - 1
        self.next_btn.setText("&Finish" if last else "&Next >")
        self.next_btn.setDefault(True)
        self.scroll.verticalScrollBar().setValue(0)

    def fit(self, room: QSize) -> QSize:
        """Narrower in a small window; tall enough for the text, never taller than the window."""
        width = max(250, min(PANEL_WIDTH, room.width() * 2 // 5))
        self.setFixedWidth(width)
        text_w = width - 3 * 2 - 10 * 2 - 4
        text_h = self.scroll.widget().layout().totalHeightForWidth(text_w)
        chrome = 22 + 3 * 2 + 8 + 10 + 8 + self.next_btn.sizeHint().height()
        return QSize(width, max(150, min(room.height(), text_h + chrome + 2)))

    def _drag(self, delta: QPoint) -> None:
        self.moved_by_hand = True
        parent = self.parentWidget()
        pos = self.pos() + delta
        pos.setX(max(0, min(pos.x(), parent.width() - self.width())))
        pos.setY(max(0, min(pos.y(), parent.height() - self.height())))
        self.move(pos)


class Tour(QObject):
    """One run of the tour over the main window."""
    ended = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.page = window.script
        self.steps = STEPS
        self.index = 0
        self.baseline: dict = {}
        self.done = False
        self.previous_script = ""  # what was open before, to go back to afterwards
        self.overlay = _Overlay(window)
        self.panel = CoachPanel(window)
        self.panel.back.connect(lambda: self.go(self.index - 1))
        self.panel.next.connect(self._next)
        self.panel.exit.connect(self.exit)
        self.panel.action.connect(self._action)
        self._relayout = QTimer(self, singleShot=True, interval=30, timeout=self._place)
        self.page.changed.connect(self._on_changed)
        self.page.side.currentChanged.connect(self._on_changed)
        for bar in (self.page.view.verticalScrollBar(), self.page.cards_scroll.verticalScrollBar()):
            bar.valueChanged.connect(self._soon)
        window.installEventFilter(self)

    # -- running ---------------------------------------------------------------------------

    def start(self) -> None:
        p = self.page.project
        if p is not None and not is_practice(p):
            self.previous_script = p.script
        self.win.show_tool(self.win._tool_index("script"))
        self.overlay.show()
        self.panel.show()
        self.go(0)

    def go(self, index: int) -> None:
        self.index = max(0, min(index, len(self.steps) - 1))
        step = self.steps[self.index]
        self.win.show_tool(self.win._tool_index("script"))
        if (tab := step.get("tab")) in TABS and self.page.project is not None:
            self.page.side.setCurrentIndex(TABS[tab])
        self.baseline = _counts(self.page.project)
        self.done = bool(step.get("wait")) and self._satisfied(step["wait"])
        self.panel.moved_by_hand = False
        self.panel.show_step(step, self.index, len(self.steps), self.done, str(PRACTICE_ROOT))
        self._bring_into_view(step)
        self._place()

    def _next(self) -> None:
        if self.index == len(self.steps) - 1:
            self.finish()
        else:
            self.go(self.index + 1)

    def _satisfied(self, wait: str) -> bool:
        page, now, base = self.page, _counts(self.page.project), self.baseline
        if wait == "practice_open":
            return now["open"]
        if not now["open"]:
            return False
        return {
            "note_added": now["notes"] > base.get("notes", 0),
            "highlight_added": now["mine"] > base.get("mine", 0) and now["n"] > base.get("n", 0),
            "combined": 0 < now["n"] < base.get("n", 0),
            "downloads_started": bool(page.job_rows) or page._sorting is not None,
            "capture_started": bool(page.stops),
            "kind_switched": now["manual"] > base.get("manual", 0),
            "added_tab": page.side.currentIndex() == TABS["added"],
        }.get(wait, False)

    def _on_changed(self, *_args) -> None:
        if not self.panel.isVisible():
            return
        step = self.steps[self.index]
        if (wait := step.get("wait")) and not self.done and self._satisfied(wait):
            self.done = True
            # A moment's pause, so the change is seen before the tour moves on.
            QTimer.singleShot(700, lambda i=self.index: self.index == i and self.panel.isVisible() and self._next())
        self._soon()

    def _soon(self, *_args) -> None:
        """Re-place the outline and panel shortly (once, however many changes come in meanwhile)."""
        if not self._relayout.isActive():
            self._relayout.start()

    def _action(self, what: str) -> None:
        if what == "import_practice":
            self.import_practice()
        elif what == "delete_practice":
            if delete_practice(self.page, self.win):
                self._reopen_previous()
                self.win.show_status("Deleted the practice project.")
                self.finish(ask=False)

    def import_practice(self) -> None:
        if practice_exists() and not delete_practice(self.page, self.win, quiet=True):
            QMessageBox.information(self.win, "Script Tour",
                                    "The practice project from last time is still busy. Let it finish, then try again.")
            return
        try:
            path = make_practice()
        except OSError as e:
            QMessageBox.warning(self.win, "Script Tour", f"Couldn't make the practice project:\n\n{e}")
            return
        self.page.open_script(path)  # shows Choose What to Import, like any new script

    def exit(self) -> None:
        """Exit Tour, at any step: nothing is left half-done (the project saves as it goes)."""
        self.finish()

    def finish(self, ask: bool = True) -> None:
        """End the tour. If the practice project is there, offer to delete it."""
        self.overlay.hide()
        self.panel.hide()
        if ask and practice_exists():
            box = QMessageBox(QMessageBox.Question, "Script Tour",
                              "Delete the practice project now?\n\nIt's only the tour's sample script and anything "
                              f"it downloaded, in “{PRACTICE_ROOT}”. Your own projects aren't touched.", parent=self.win)
            delete = box.addButton("Delete Practice Project", QMessageBox.AcceptRole)
            box.addButton("Keep It for Now", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is delete and delete_practice(self.page, self.win):
                self.win.show_status("Deleted the practice project.")
                self._reopen_previous()
            elif is_practice(self.page.project) and self.previous_script:
                self._reopen_previous()
        self._teardown()

    def _reopen_previous(self) -> None:
        """Back to the script that was open before the tour."""
        prev = self.previous_script
        if prev and (os.path.exists(prev) or project_path(prev).exists()):
            self.page.open_script(prev, quiet=True)

    def _teardown(self) -> None:
        self.win.removeEventFilter(self)
        try:
            self.page.changed.disconnect(self._on_changed)
            self.page.side.currentChanged.disconnect(self._on_changed)
        except (RuntimeError, TypeError):
            pass
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.ended.emit()
        self.deleteLater()

    # -- pointing at things -----------------------------------------------------------------

    def _targets(self, step: dict) -> list[str]:
        t = step.get("target") or []
        return [t] if isinstance(t, str) else list(t)

    def _card(self, which: str):
        cards = self.page.card_widgets()
        p = self.page.project
        if not cards or p is None:
            return None
        if which == "first":
            return cards[0]

        def has(card, test) -> bool:
            h = p.get(card.hid)
            return bool(h and any(test(r) for r in h.links))
        tests = {"video": lambda r: r.kind == "video" and "youtube" in r.url,
                 "x": lambda r: "x.com/" in r.url or "twitter.com/" in r.url,
                 "files": lambda r: bool(r.files)}
        test = tests.get(which)
        found = next((c for c in cards if test and has(c, test)), None)
        if found is None and which == "files":
            found = next((c for c in cards if has(c, tests["video"])), None)
        return found or cards[0]

    def _resolve(self, name: str) -> tuple[QWidget | None, QRect | None]:
        """A target name -> (widget, rectangle in that widget), or (None, None) when not on screen."""
        page = self.page
        simple = {"import_btn": page.import_btn, "trim_btn": page.trim_btn, "script_combo": page.script_combo,
                  "project_folder": page.project_folder, "folder_links": page.dests, "script_view": page.view,
                  "highlight_btn": page.highlight_btn, "exec_videos": page.exec_videos,
                  "exec_shots": page.exec_shots}
        if name == "saved_label":
            w = page.saved_label if page.saved_label.text() else page.summary
            return w, w.rect()
        if name in simple:
            w = simple[name]
            return w, w.rect()
        if name.startswith("tab:"):
            bar = page.side.tabBar()
            i = TABS.get(name[4:], 0)
            return bar, bar.tabRect(i)
        if name.startswith("card:"):
            card = self._card(name[5:])
            return (card, card.rect()) if card else (None, None)
        if name.startswith("para:") and page.project is not None:
            block = page.view.document().findBlockByNumber(int(name[5:]))
            if not block.isValid():
                return None, None
            layout = page.view.document().documentLayout()
            r = layout.blockBoundingRect(block).toRect()
            r.translate(-page.view.horizontalScrollBar().value(), -page.view.verticalScrollBar().value())
            return page.view.viewport(), r.adjusted(-4, -2, 4, 2)
        return None, None

    def _bring_into_view(self, step: dict) -> None:
        for name in self._targets(step):
            if name.startswith("card:") and (card := self._card(name[5:])):
                QTimer.singleShot(0, lambda c=card: self.page.cards_scroll.ensureWidgetVisible(c, 0, 20))
            elif name.startswith("para:") and self.page.project is not None:
                block = self.page.view.document().findBlockByNumber(int(name[5:]))
                if block.isValid():
                    cur = QTextCursor(block)
                    self.page.view.setTextCursor(cur)
                    self.page.view.ensureCursorVisible()

    def _rects(self, step: dict) -> list[QRect]:
        out = []
        for name in self._targets(step):
            w, r = self._resolve(name)
            if w is None or not w.isVisible():
                continue
            # Only the part that's actually showing (a card scrolled half out of view, say): clipped
            # by every widget it sits in, such as the list's scroll area.
            shown = QRect(w.mapTo(self.win, r.topLeft()), r.size())
            a = w
            while a is not None and a is not self.win:
                shown = shown.intersected(QRect(a.mapTo(self.win, QPoint(0, 0)), a.size()))
                a = a.parentWidget()
            if shown.width() > 4 and shown.height() > 4:
                out.append(shown)
        return out

    def _place(self) -> None:
        if not self.panel.isVisible():
            return
        win = self.win
        self.overlay.setGeometry(win.rect())
        step = self.steps[self.index]
        rects = self._rects(step)
        self.overlay.rects = rects
        self.overlay.raise_()
        self.overlay.update()
        area = win.rect().adjusted(GAP, GAP, -GAP, -GAP)
        size = self.panel.fit(area.size())
        self.panel.setFixedHeight(size.height())
        if not self.panel.moved_by_hand:
            self.panel.move(self._spot(rects[0] if rects else None, size, area))
        self.panel.raise_()

    @staticmethod
    def _spot(target: QRect | None, size: QSize, area: QRect) -> QPoint:
        """Beside the target if there's room (right, left, below, above), else the corner that
        covers the least of it."""
        def clamp(x, y) -> QPoint:
            return QPoint(max(area.left(), min(x, area.right() - size.width())),
                          max(area.top(), min(y, area.bottom() - size.height())))
        if target is None:
            return clamp(area.right() - size.width(), area.bottom() - size.height())
        t = target.adjusted(-6, -6, 6, 6)
        y_mid = t.center().y() - size.height() // 2
        x_mid = t.center().x() - size.width() // 2
        options = [
            (area.right() - t.right() - GAP, clamp(t.right() + GAP, y_mid)),
            (t.left() - area.left() - GAP, clamp(t.left() - GAP - size.width(), y_mid)),
            (area.bottom() - t.bottom() - GAP, clamp(x_mid, t.bottom() + GAP)),
            (t.top() - area.top() - GAP, clamp(x_mid, t.top() - GAP - size.height())),
        ]
        for room, spot in options[:2]:
            if room >= size.width():
                return spot
        for room, spot in options[2:]:
            if room >= size.height():
                return spot
        corners = [clamp(area.left(), area.top()), clamp(area.right(), area.top()),
                   clamp(area.left(), area.bottom()), clamp(area.right(), area.bottom())]
        return min(corners, key=lambda c: QRect(c, size).intersected(t).width() * QRect(c, size).intersected(t).height())

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.win and e.type() in (QEvent.Resize, QEvent.Show):
            self._soon()
        return False
