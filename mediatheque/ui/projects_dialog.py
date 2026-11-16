"""Manage Projects: every saved script project, with ways to open one, reconnect a moved
script, put back an earlier version, or take a project off the list."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ..script.project import Project, ProjectInfo, list_backups, list_projects, restore_backup, _backup_dir


def _when(t: float) -> str:
    lt = time.localtime(t)
    if time.strftime("%x", lt) == time.strftime("%x"):
        return "today " + time.strftime("%I:%M %p", lt).lstrip("0")
    return time.strftime("%b %d, %Y", lt)


class ProjectsDialog(QDialog):
    def __init__(self, current_script: str, busy: set[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Projects")
        self.resize(780, 480)
        self.current = os.path.normcase(current_script) if current_script else ""
        self.busy = busy or set()  # project files with downloads/captures still running
        self.open_path = ""        # set when the user picks Open
        self.restored: Path | None = None
        self.restored_script = ""

        intro = QLabel("Every script you've worked on. Projects save automatically after every change; "
                       "earlier versions are kept as backups in case you need to go back.")
        intro.setWordWrap(True)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._open())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())

        self.open_btn = QPushButton("&Open")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self._open)
        self.locate_btn = QPushButton("&Locate Script…")
        self.locate_btn.setToolTip("Reconnect a project whose Word file was moved or renamed.")
        self.locate_btn.clicked.connect(self._locate)
        self.restore_btn = QPushButton("&Restore Earlier Version…")
        self.restore_btn.clicked.connect(self._restore)
        self.remove_btn = QPushButton("Remo&ve from List")
        self.remove_btn.setToolTip("Take this project off the list. Its saved file is kept in the backups "
                                   "folder, and nothing in your project folders is touched.")
        self.remove_btn.clicked.connect(self._remove)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        for b in (self.open_btn, self.locate_btn, self.restore_btn, self.remove_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addWidget(intro)
        col.addWidget(self.list, 1)
        col.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for info in list_projects():
            folder = os.path.basename(info.folder) if info.folder else "no project folder"
            line = (f"{info.name}\n    {folder} · {info.highlights} highlights · "
                    f"{info.links_done}/{info.links_total} links done · {info.images} image"
                    f"{'s' if info.images != 1 else ''} · saved {_when(info.updated)}")
            if info.script_missing:
                line += "   · Word file not found"
            item = QListWidgetItem(line)
            item.setData(Qt.UserRole, info)
            item.setToolTip(info.script)
            if info.script_missing:
                item.setForeground(QColor("#a00000"))
            self.list.addItem(item)
            if os.path.normcase(info.script) == self.current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _info(self) -> ProjectInfo | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_buttons(self) -> None:
        info = self._info()
        for b in (self.open_btn, self.restore_btn, self.remove_btn, self.locate_btn):
            b.setEnabled(info is not None)
        if info:
            is_open = os.path.normcase(info.script) == self.current
            busy = os.path.normcase(str(info.path)) in self.busy
            self.restore_btn.setEnabled(bool(list_backups(info.path)) and not busy)
            self.remove_btn.setEnabled(not is_open and not busy)
            self.locate_btn.setEnabled(not is_open and not busy and info.script_missing)
            self.restore_btn.setToolTip("Wait for this project's downloads and captures to finish first." if busy
                                        else "Put back an earlier version of this project.")
            self.remove_btn.setToolTip("Switch to another script first." if is_open else
                                       "Take this project off the list. Its saved file is kept in the backups "
                                       "folder, and nothing in your project folders is touched.")

    def _open(self) -> None:
        if info := self._info():
            self.open_path = info.script
            self.accept()

    def _locate(self) -> None:
        info = self._info()
        if not info:
            return
        start = os.path.dirname(info.script) if os.path.isdir(os.path.dirname(info.script)) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, f"Where is “{os.path.basename(info.script)}” now?", start,
                                              "Word documents (*.docx)")
        if path:
            Project(info.script).relink(path)
            self._fill()

    def _restore(self) -> None:
        info = self._info()
        if not info:
            return
        backups = list_backups(info.path)
        labels = [f"{_when(b.stat().st_mtime)}  ({time.strftime('%I:%M %p', time.localtime(b.stat().st_mtime)).lstrip('0')})"
                  + ("  · before a restore" if "before restore" in b.name else "") for b in backups]
        choice, ok = QInputDialog.getItem(self, "Restore Earlier Version",
                                          f"Put back an earlier version of “{info.name}”.\n"
                                          "The current version is backed up first, so this can be undone.",
                                          labels, 0, False)
        if not ok:
            return
        restore_backup(backups[labels.index(choice)], info.path)
        self.restored, self.restored_script = info.path, info.script
        self._fill()

    def _remove(self) -> None:
        info = self._info()
        if not info:
            return
        if QMessageBox.question(self, "Remove from List",
                                f"Take “{info.name}” off the list?\n\nIts saved project is moved to the backups "
                                "folder (so it can be recovered), and your files aren't touched.") != QMessageBox.Yes:
            return
        shutil.move(str(info.path), str(_backup_dir() / f"{info.path.stem} (removed {time.strftime('%Y-%m-%d %H%M%S')}).json"))
        self._fill()
