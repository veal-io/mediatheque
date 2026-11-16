"""Credit Generator page: import a Patreon CSV, split the names into three lists,
copy each list into the credits MOGRT in Premiere."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..config import Settings
from ..credits import CreditsError, load_names, split_three


class _Column(QWidget):
    def __init__(self, number: int, page: CreditsPage):
        super().__init__()
        self.number = number
        self.page = page
        self.label = QLabel()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.label.setBuddy(self.text)
        self.copy_btn = QPushButton(f"Copy List &{number}")
        self.copy_btn.clicked.connect(self.copy)
        self._reset = QTimer(self, singleShot=True, interval=1500,
                             timeout=lambda: self.copy_btn.setText(f"Copy List &{number}"))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(self.label)
        col.addWidget(self.text, 1)
        col.addWidget(self.copy_btn)
        self.set_names([])

    def set_names(self, names: list[str]) -> None:
        self.names = names
        self.text.setPlainText("\n".join(names))
        self.label.setText(f"List {self.number} ({len(names)})" if names else f"List {self.number}")
        self.copy_btn.setEnabled(bool(names))

    def copy(self) -> None:
        if not self.names:
            return
        QGuiApplication.clipboard().setText("\n".join(self.names))
        self.copy_btn.setText("Copied!")
        self._reset.start()
        self.page.status.emit(f"Copied List {self.number} ({len(self.names)} names) to the clipboard.")


class CreditsPage(QWidget):
    status = Signal(str)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.state = settings.tab("credits")
        self.setAcceptDrops(True)

        # -- Patron list -------------------------------------------------------------
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("Import a Patreon members CSV, or drag one onto this window")
        self.import_btn = QPushButton("&Import CSV…")
        self.import_btn.setObjectName("primary")  # drawn as the default button
        self.import_btn.clicked.connect(self.browse)
        self.summary = QLabel("No file loaded.")
        self.summary.setObjectName("muted")

        file_label = QLabel("C&SV file:")
        file_label.setBuddy(self.import_btn)
        form = QGridLayout()
        form.setContentsMargins(12, 14, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addWidget(file_label, 0, 0)
        form.addWidget(self.path, 0, 1)
        form.addWidget(self.import_btn, 0, 2)
        form.addWidget(self.summary, 1, 1, 1, 2)
        form.setColumnStretch(1, 1)
        source_box = QGroupBox("Patron List")
        source_box.setLayout(form)

        # -- Three columns -------------------------------------------------------------
        self.columns = [_Column(n, self) for n in (1, 2, 3)]
        cols = QHBoxLayout()
        cols.setContentsMargins(12, 14, 12, 12)
        cols.setSpacing(12)
        for c in self.columns:
            cols.addWidget(c, 1)
        credits_box = QGroupBox("Credits")
        credits_box.setLayout(cols)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(source_box)
        root.addWidget(credits_box, 1)

    def browse(self) -> None:
        start = self.state.get("last_dir") or os.path.expanduser("~/Downloads")
        path, _ = QFileDialog.getOpenFileName(self, "Select Patreon CSV", start,
                                              "CSV files (*.csv);;All files (*)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        try:
            names = load_names(path)
        except CreditsError as e:
            QMessageBox.warning(self, "Credit Generator", str(e))
            return
        except (OSError, UnicodeDecodeError, ValueError) as e:
            QMessageBox.warning(self, "Credit Generator", f"Couldn't read this file:\n\n{e}")
            return
        self.state["last_dir"] = os.path.dirname(path)
        self.settings.save()
        self.path.setText(os.path.normpath(path))
        lists = split_three(names)
        for column, names_in_col in zip(self.columns, lists):
            column.set_names(names_in_col)
        sizes = " / ".join(str(len(c)) for c in lists)
        self.summary.setText(f"{len(names)} names loaded: {sizes}")
        self.status.emit(f"Loaded {len(names)} names from {os.path.basename(path)}.")

    # Drag a CSV from Explorer onto the page.
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(u.toLocalFile().lower().endswith(".csv") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".csv"):
                self.load(u.toLocalFile())
                return
