"""The screen recorder's control bar: a small always-on-top window with the Start, End and
Save buttons. Windows is told to leave it out of screen captures, so you see it and the
video doesn't. It never takes focus, so the page keeps its scrolling and keyboard.
"""
from __future__ import annotations

import ctypes
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..osutil import IS_MAC, exclude_from_capture, open_path

WDA_EXCLUDEFROMCAPTURE = 0x11
SETTLE_MS = 4500


class RecordBar(QFrame):
    choice = Signal(str)  # "start" | "cancel" | "stop" | "next" | "more" | "discard"

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFrameShape(QFrame.Panel)
        self.setFrameShadow(QFrame.Raised)
        self.path = ""
        self.started = 0.0
        self.timer = QTimer(self, interval=500, timeout=self._tick)

        self.status = QLabel()
        f = self.status.font()
        f.setBold(True)
        self.status.setFont(f)
        self.context = QLabel()
        self.context.setObjectName("muted")
        self.context.setMaximumWidth(760)

        self.buttons = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.status, 1)
        for key, text, tip in (
            ("start", "● Start Recording", "Records the whole screen. Scroll the article to get it where you want first."),
            ("cancel", "Back", "Back to the Screenshotter, without recording."),
            ("stop", "■ End Recording", ""),
            ("next", "Save, Next Article", "Save this recording and go to the next link."),
            ("more", "Save, Record Another", "Save this recording and record another on this page."),
            ("discard", "Discard", "Delete this recording and go back to the Screenshotter on this page."),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._clicked(k))
            row.addWidget(b)
            self.buttons[key] = b
        self.watch = QPushButton("Watch")
        self.watch.setToolTip("Open the recording in your video player.")
        self.watch.setFocusPolicy(Qt.NoFocus)
        self.watch.clicked.connect(lambda: self.path and open_path(self.path))
        row.insertWidget(row.indexOf(self.buttons["next"]), self.watch)

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 6, 10, 6)
        col.setSpacing(3)
        col.addLayout(row)
        col.addWidget(self.context)

    def set_state(self, state: str, info: dict) -> None:
        self.timer.stop()
        if state == "hidden":
            self.hide()
            return
        if "context" in info:
            label, text = info.get("label", ""), info["context"]
            line = (f"{label}: " if label else "") + (f"“{text}”" if text else "")
            if info.get("note"):
                line += f"   Note: {info['note']}"
            self.context.setText(self.context.fontMetrics().elidedText(line, Qt.ElideRight, 760))
            self.context.setToolTip(line)
            self.context.setVisible(bool(line))
        shown = {"ready": ("start", "cancel"), "recording": ("stop",), "review": ("next", "more", "discard")}[state]
        for key, b in self.buttons.items():
            b.setVisible(key in shown)
        self.watch.setVisible(state == "review")
        if state == "recording" and IS_MAC:
            # macOS puts this bar in the video, so it stays hidden; Control+S ends the recording.
            self.hide()
            return
        if state == "ready":
            self.status.setText(info.get("message") or (
                "Ready to record. Scroll to where you want to start. This bar hides while recording: "
                "press Control+S to end." if IS_MAC else "Ready to record. Scroll to where you want to start."))
            if info.get("settle"):  # until Edge's "To exit full screen" notice has faded
                start = self.buttons["start"]
                start.setEnabled(False)
                QTimer.singleShot(SETTLE_MS, lambda: start.setEnabled(True))
        elif state == "recording":
            self.started = time.monotonic()
            self.timer.start()
            self._tick()
        else:
            self.path = info.get("path", "")
            self.status.setText(f"Recorded {self._clock(info.get('seconds', 0))}. Keep it?")
        self.adjustSize()
        self._place(info.get("device", ""))
        # Visible to you, left out of the recording. On a Mac this also lets it float over the
        # browser's full-screen Space, which has to be set before it first appears.
        self.winId()
        exclude_from_capture(self)
        self.show()
        exclude_from_capture(self)

    def _clicked(self, key: str) -> None:
        if key == "start" and IS_MAC:
            self.hide()  # macOS would record it: out of the way before recording starts
        self.choice.emit(key)

    def _place(self, device: str) -> None:
        if device:
            self._screen = next((s for s in QGuiApplication.screens() if s.name() == device), None)
        screen = getattr(self, "_screen", None) or QGuiApplication.primaryScreen()
        g = screen.geometry()
        # At the bottom: moving the mouse to the top of the screen brings up Edge's exit-full-screen button.
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 16)

    @staticmethod
    def _clock(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 60}:{s % 60:02d}"

    def _tick(self) -> None:
        self.status.setText(f"● Recording  {self._clock(time.monotonic() - self.started)}")
