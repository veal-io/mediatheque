"""An editor's copy: "Update available", installing it, Help > Check for Updates and Help > Undo
Last Update. The work is in mediatheque/updates.py."""
from __future__ import annotations

import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout,
)

from .. import __version__
from .. import updates as up


class Checker(QObject):
    found = Signal(object)  # Release, or None when there's nothing newer
    failed = Signal(str)

    def start(self, repo: str) -> None:
        def work():
            try:
                rel = up.latest_release(repo)
                self.found.emit(rel if up.is_newer(rel.version) else None)
            except up.UpdateError as e:
                self.failed.emit(str(e))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"Couldn't check for updates: {e}")
        threading.Thread(target=work, daemon=True, name="check-updates").start()


class _Installer(QObject):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def start(self, rel) -> None:
        def work():
            try:
                path = up.download(rel, lambda d, t: self.progress.emit(d, t))
                self.done.emit(up.install(path))
            except up.UpdateError as e:
                self.failed.emit(str(e))
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"The update couldn't be installed ({e}). The current version was left as it was.")
        threading.Thread(target=work, daemon=True, name="install-update").start()


class UpdateDialog(QDialog):
    """Update available: version X, what's new, Update Now / Later."""

    def __init__(self, rel, busy, parent=None):
        super().__init__(parent)
        self.rel = rel
        self.busy = busy  # callable: downloads or captures still running?
        self.installed = ""
        self.setWindowTitle("Update Available")
        self.resize(540, 420)
        head = QLabel(f"<b>Update available: version {rel.version}</b><br>You have version {__version__}.")
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(rel.notes or "No notes for this version.")
        self.bar = QProgressBar()
        self.bar.hide()
        self.status = QLabel("Your projects, settings and downloads aren't touched.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.now = QPushButton("&Update Now")
        self.now.setDefault(True)
        self.now.clicked.connect(self._update)
        self.later = QPushButton("&Later")
        self.later.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.now)
        row.addWidget(self.later)
        col = QVBoxLayout(self)
        col.addWidget(head)
        col.addWidget(QLabel("What's new:"))
        col.addWidget(notes, 1)
        col.addWidget(self.bar)
        col.addWidget(self.status)
        col.addLayout(row)
        self.installer = _Installer(self)
        self.installer.progress.connect(self._progress)
        self.installer.done.connect(self._done)
        self.installer.failed.connect(self._failed)

    def _update(self) -> None:
        if self.busy():
            QMessageBox.information(self, "Update", "Some downloads or captures are still running. Let them finish "
                                                    "(or cancel them), then click Update Now.")
            return
        self.now.setEnabled(False)
        self.later.setEnabled(False)
        self.bar.show()
        self.status.setText("Downloading…")
        self.installer.start(self.rel)

    def _progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setMaximum(total)
            self.bar.setValue(done)
        else:
            self.bar.setMaximum(0)

    def _done(self, version: str) -> None:
        self.installed = version
        self.accept()

    def _failed(self, message: str) -> None:
        self.bar.hide()
        self.status.setText(message)
        self.now.setEnabled(True)
        self.later.setEnabled(True)
        QMessageBox.warning(self, "Update", message)


def offer_restart(window, text: str) -> None:
    if QMessageBox.question(window, "Mediatheque", text + "\n\nRestart Mediatheque now?",
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
        restart(window)


def restart(window) -> None:
    """Start again the way the editor normally does (Mediatheque.app on a Mac), then quit this one."""
    if not window.close():  # "downloads still running, quit anyway?" was answered No
        return
    try:
        subprocess.Popen(up.restart_command(), cwd=str(up.ROOT), start_new_session=True)
    except OSError as e:
        QMessageBox.warning(None, "Mediatheque", f"Couldn't restart ({e}). Open Mediatheque again yourself.")
