"""Help > Report a Problem… and Help > Check Setup… (the logic is in mediatheque/diagnostics.py)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .. import diagnostics as diag
from ..osutil import FILE_MANAGER
from .widgets import show_in_explorer

MARKS = {"ok": ("✓", "#008000"), "warn": ("!", "#a06000"), "fail": ("✗", "#c00000")}


class ReportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report a Problem")
        self.resize(520, 340)
        intro = QLabel(
            "This makes a zip on your Desktop with Mediatheque's logs and a note of its version, your "
            f"system's, Python's and the download engine's. Send it to whoever gave you Mediatheque.\n\n"
            "It doesn't include your projects, cookies, passwords or settings.")
        intro.setWordWrap(True)
        self.what = QPlainTextEdit()
        self.what.setPlaceholderText("Optional: what were you doing when it went wrong?")
        make = QPushButton("&Make Report")
        make.setDefault(True)
        make.clicked.connect(self._make)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make)
        row.addWidget(cancel)
        col = QVBoxLayout(self)
        col.addWidget(intro)
        col.addWidget(self.what, 1)
        col.addLayout(row)
        self.path = None

    def _make(self) -> None:
        try:
            self.path = diag.make_report(what_happened=self.what.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Report a Problem", f"Couldn't make the report:\n\n{e}")
            return
        show_in_explorer(str(self.path))
        QMessageBox.information(self, "Report a Problem",
                                f"Saved “{self.path.name}” on your Desktop and showed it in {FILE_MANAGER}. "
                                "Attach it to an email or message to send it.")
        self.accept()


class _Checker(QObject):
    done = Signal(list)

    def start(self) -> None:
        threading.Thread(target=lambda: self.done.emit(diag.run_checks()), daemon=True,
                         name="check-setup").start()


class CheckSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check Setup")
        self.resize(640, 460)
        intro = QLabel("What Mediatheque needs on this computer. Anything with a fix beside it needs your attention.")
        intro.setWordWrap(True)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setColumnStretch(2, 1)
        self.grid.setVerticalSpacing(10)
        self.grid.setHorizontalSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.host)
        self.again = QPushButton("Check &Again")
        self.again.clicked.connect(self.run)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.again)
        row.addWidget(close)
        col = QVBoxLayout(self)
        col.addWidget(intro)
        col.addWidget(scroll, 1)
        col.addLayout(row)
        self.checker = _Checker(self)
        self.checker.done.connect(self.show_checks)
        self.run()

    def _clear(self) -> None:
        while self.grid.count():
            if w := self.grid.takeAt(0).widget():
                w.deleteLater()

    def run(self) -> None:
        self._clear()
        self.again.setEnabled(False)
        self.grid.addWidget(QLabel("Checking… (a few seconds)"), 0, 0, 1, 3)
        self.checker.start()

    def show_checks(self, checks: list) -> None:
        self._clear()
        for r, c in enumerate(checks):
            mark, color = MARKS[c.status]
            m = QLabel(mark)
            m.setStyleSheet(f"color: {color}; font-weight: bold;")
            m.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            name = QLabel(f"<b>{c.name}</b>")
            name.setAlignment(Qt.AlignTop)
            text = c.detail + (f"<br><span style='color:{color}'>Fix: {c.fix}</span>" if c.fix else "")
            detail = QLabel(text)
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(m, r, 0)
            self.grid.addWidget(name, r, 1)
            self.grid.addWidget(detail, r, 2)
        self.grid.setRowStretch(len(checks), 1)
        self.again.setEnabled(True)
