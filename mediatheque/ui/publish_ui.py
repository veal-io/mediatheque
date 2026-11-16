"""Tools > Publish Update… (the owner's copy): the dialog. The steps are in mediatheque/publish.py."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .. import __version__
from ..publish import Publisher, PublishError
from ..updates import next_version, update_repo


class _Worker(QObject):
    said = Signal(str)
    finished = Signal(bool, str, str)  # ok, repo, url or error message

    def start(self, version: str, notes: str, repo_name: str, known: str) -> None:
        def work():
            try:
                repo, url = Publisher(say=self.said.emit).publish(version, notes, __version__, repo_name, known)
                self.finished.emit(True, repo, url)
            except PublishError as e:
                self.finished.emit(False, "", str(e))
            except Exception as e:  # noqa: BLE001
                self.finished.emit(False, "", f"Something unexpected went wrong: {e}")
        threading.Thread(target=work, daemon=True, name="publish").start()


class PublishDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Publish Update")
        self.resize(600, 520)
        self.known = update_repo(settings)
        intro = QLabel("Sends this version of Mediatheque to every editor's copy: it's published on GitHub, and their "
                       "Mediatheque offers it the next time it opens (or right away with Help > Check for Updates).")
        intro.setWordWrap(True)
        self.version = QLineEdit(next_version(__version__))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("What's new, in a few plain lines. Editors see this before they update.\n"
                                      "- The Script tour explains Re-Take\n- Fixed: TikTok downloads stopping halfway")
        self.repo_name = QLineEdit("mediatheque")
        form = QFormLayout()
        form.addRow(f"New version (now {__version__}):", self.version)
        form.addRow("What's new:", self.notes)
        if self.known:
            form.addRow("Published at:", QLabel(f"github.com/{self.known}"))
        else:
            form.addRow("Name for the public page:", self.repo_name)
            first = QLabel("First time: this creates a public page on GitHub for Mediatheque's code under your "
                           "account. Your projects, settings, notes and patron lists are never in it.")
            first.setWordWrap(True)
            first.setObjectName("muted")
            form.addRow("", first)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.hide()
        self.go = QPushButton("&Publish")
        self.go.setDefault(True)
        self.go.clicked.connect(self._publish)
        self.close_btn = QPushButton("Cancel")
        self.close_btn.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.go)
        row.addWidget(self.close_btn)
        col = QVBoxLayout(self)
        col.addWidget(intro)
        col.addLayout(form, 1)
        col.addWidget(self.log, 1)
        col.addLayout(row)
        self.worker = _Worker(self)
        self.worker.said.connect(self.log.appendPlainText)
        self.worker.finished.connect(self._finished)

    def _publish(self) -> None:
        version = self.version.text().strip().lstrip("vV")
        if not version or not all(p.isdigit() for p in version.split(".")):
            QMessageBox.warning(self, "Publish Update", "The version should be numbers like 1.2.0.")
            return
        if not self.notes.toPlainText().strip():
            QMessageBox.warning(self, "Publish Update", "Add a line or two on what's new; editors see it first.")
            return
        for w in (self.version, self.notes, self.repo_name, self.go):
            w.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.log.show()
        self.worker.start(version, self.notes.toPlainText(), self.repo_name.text(), self.known)

    def _finished(self, ok: bool, repo: str, info: str) -> None:
        self.close_btn.setEnabled(True)
        self.close_btn.setText("Close")
        if ok:
            self.settings.data.setdefault("publish", {})["repo"] = repo
            self.settings.save()
            self.log.appendPlainText(f"\nPublished: {info}")
            QMessageBox.information(self, "Publish Update",
                                    f"Published. Editors' copies will offer this version the next time they open.\n\n{info}"
                                    "\n\nRestart Mediatheque to see the new version number in Help > About.")
        else:
            self.log.appendPlainText("\n" + info)
            for w in (self.version, self.notes, self.repo_name, self.go):
                w.setEnabled(True)
            QMessageBox.warning(self, "Publish Update", info)
