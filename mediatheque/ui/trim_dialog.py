"""Choose which parts of a script to import: research notes and prompts after the sign-off
are usually left out. Word files don't record pages, so this works by paragraph."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from ..script.docx import Paragraph, fill_blanks


class TrimDialog(QDialog):
    def __init__(self, paragraphs: list[Paragraph], removed: set[int], suggested: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose What to Import")
        self.paragraphs = paragraphs
        self.resize(820, 600)

        intro = QLabel(
            "Untick anything that isn't part of the script, like research notes after the sign-off. "
            "Left-out paragraphs aren't shown, highlighted, downloaded or captured, and you can bring "
            "them back later with Trim Script…"
            + ("\n\nMediatheque guessed where the script ends; check that it's right." if suggested else ""))
        intro.setWordWrap(True)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setUniformItemSizes(False)
        self.rows: list[int] = []  # list row -> paragraph index (blank paragraphs aren't listed)
        for i, p in enumerate(paragraphs):
            if not p.text.strip():
                continue
            text = " ".join(p.text.split())
            text = text if len(text) <= 110 else text[:107] + "…"
            links = f"   ({len(p.links)} link{'s' if len(p.links) != 1 else ''})" if p.links else ""
            item = QListWidgetItem(f"{text}{links}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if i in removed else Qt.Checked)
            if p.heading:
                f = QFont(item.font())
                f.setBold(True)
                item.setFont(f)
            item.setToolTip(p.text[:600])
            self.list.addItem(item)
            self.rows.append(i)
        self.list.itemChanged.connect(self._restyle)
        for r in range(self.list.count()):
            self._restyle(self.list.item(r))
        first_out = next((r for r, i in enumerate(self.rows) if i in removed), None)
        if first_out is not None:
            self.list.scrollToItem(self.list.item(max(0, first_out - 3)), QAbstractItemView.PositionAtTop)

        out_btn = QPushButton("&Leave Out Selected")
        out_btn.clicked.connect(lambda: self._set_selected(False))
        in_btn = QPushButton("&Keep Selected")
        in_btn.clicked.connect(lambda: self._set_selected(True))
        end_btn = QPushButton("Leave Out From &Here to End")
        end_btn.setToolTip("Leave out the selected paragraph and everything after it.")
        end_btn.clicked.connect(self._cut_to_end)
        all_btn = QPushButton("Keep &All")
        all_btn.clicked.connect(lambda: self._set_rows(range(self.list.count()), True))
        tools = QHBoxLayout()
        for b in (out_btn, in_btn, end_btn, all_btn):
            tools.addWidget(b)
        tools.addStretch(1)

        self.count = QLabel()
        self.count.setObjectName("muted")
        ok = QPushButton("&Import")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count, 1)
        bottom.addWidget(ok)
        bottom.addWidget(cancel)

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 14, 14, 12)
        col.setSpacing(8)
        col.addWidget(intro)
        col.addWidget(self.list, 1)
        col.addLayout(tools)
        col.addLayout(bottom)
        self._update_count()

    def set_ok_text(self, text: str) -> None:
        self.findChild(QPushButton, "primary").setText(text)

    def _restyle(self, item: QListWidgetItem) -> None:
        self.list.blockSignals(True)
        item.setForeground(QColor("#000000") if item.checkState() == Qt.Checked else QColor("#909090"))
        self.list.blockSignals(False)
        self._update_count()

    def _set_rows(self, rows, keep: bool) -> None:
        for r in rows:
            self.list.item(r).setCheckState(Qt.Checked if keep else Qt.Unchecked)

    def _set_selected(self, keep: bool) -> None:
        self._set_rows([self.list.row(i) for i in self.list.selectedItems()], keep)

    def _cut_to_end(self) -> None:
        rows = [self.list.row(i) for i in self.list.selectedItems()]
        if rows:
            self._set_rows(range(min(rows), self.list.count()), False)

    def _update_count(self) -> None:
        if not hasattr(self, "count"):
            return
        out = [self.rows[r] for r in range(self.list.count()) if self.list.item(r).checkState() != Qt.Checked]
        links = sum(len(self.paragraphs[i].links) for i in out)
        self.count.setText(f"Leaving out {len(out)} paragraph{'s' if len(out) != 1 else ''}"
                           + (f" and {links} link{'s' if links != 1 else ''}." if links else ".")
                           if out else "Importing the whole document.")

    def removed(self) -> set[int]:
        """Unticked paragraphs, plus the blank lines around them."""
        out = {self.rows[r] for r in range(self.list.count()) if self.list.item(r).checkState() != Qt.Checked}
        return fill_blanks(self.paragraphs, out)
