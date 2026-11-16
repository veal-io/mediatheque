from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpacerItem,
)

from ..osutil import FILE_MANAGER
from ..config import Settings, default_download_dir


def show_in_explorer(path: str) -> None:
    """Show a file in Explorer (Windows) or Finder (Mac); open a folder."""
    from ..osutil import reveal
    reveal(path)


class ElidedLabel(QLabel):
    """A label that shortens long text with an ellipsis instead of growing."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width(), 10)))


# -- Download folders --------------------------------------------------------------

class FolderStore(QObject):
    """The shared list of download folders. Every tab's dropdown shows the same list,
    but each tab remembers its own selection."""

    changed = Signal()
    MAX = 5  # short enough that "Choose another folder…" is always in view

    def __init__(self, settings: Settings, key: str = "folders"):
        super().__init__()
        self.settings = settings
        self.key = key  # which list in the settings: download folders, project folders…
        if not self.settings.data.get(self.key):
            self.settings.data[self.key] = [default_download_dir()]
        self.settings.data[self.key] = self._trim(self.settings.data[self.key])

    def folders(self) -> list[str]:
        return list(self.settings.data[self.key])

    def add(self, path: str) -> str:
        path = os.path.normpath(path)
        folders = [f for f in self.settings.data[self.key] if os.path.normcase(f) != os.path.normcase(path)]
        self.settings.data[self.key] = self._trim([path, *folders])
        self.settings.save()
        self.changed.emit()
        return path

    def _trim(self, folders: list[str]) -> list[str]:
        """Keep the most recent MAX folders, but never drop one a tab is currently saving to."""
        in_use = {os.path.normcase(v) for t in self.settings.data.get("tabs", {}).values() if isinstance(t, dict)
                  for k, v in t.items() if k.endswith("folder") and isinstance(v, str)}
        kept: list[str] = []
        for f in folders:
            if len(kept) < self.MAX or os.path.normcase(f) in in_use:
                kept.append(f)
        return kept

    def remove(self, path: str) -> None:
        folders = [f for f in self.settings.data[self.key] if f != path]
        self.settings.data[self.key] = folders or [default_download_dir()]
        self.settings.save()
        self.changed.emit()


class FolderCombo(QComboBox):
    folder_changed = Signal(str)
    _CHOOSE = "\0choose"

    def __init__(self, store: FolderStore, current: str | None, parent=None):
        super().__init__(parent)
        self.store = store
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(20)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Where downloads from this tab are saved. Right-click to remove a folder from the list.")
        if current and os.path.normpath(current) not in store.folders():
            current = store.add(current)
        self._current = current or store.folders()[0]
        store.changed.connect(self._rebuild)
        self.activated.connect(self._on_activated)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._rebuild()

    def current_folder(self) -> str:
        return self._current

    def set_folder(self, path: str) -> None:
        """Select `path` without announcing it as a user change."""
        path = os.path.normpath(path)
        if path not in self.store.folders():
            path = self.store.add(path)  # rebuilds every dropdown
        self._current = path
        self._rebuild()

    def _rebuild(self) -> None:
        folders = self.store.folders()
        if self._current not in folders:
            self._current = folders[0]
            self.folder_changed.emit(self._current)
        self.blockSignals(True)
        self.clear()
        for f in folders:
            self.addItem(f, f)
            self.setItemData(self.count() - 1, f, Qt.ToolTipRole)
        self.insertSeparator(self.count())
        self.addItem("Choose another folder…", self._CHOOSE)
        self.setCurrentIndex(folders.index(self._current))
        self.blockSignals(False)

    def _on_activated(self, index: int) -> None:
        data = self.itemData(index)
        if data == self._CHOOSE:
            path = QFileDialog.getExistingDirectory(self, "Choose download folder", self._current)
            if path:
                self._current = self.store.add(path)  # triggers _rebuild in every tab
                self.folder_changed.emit(self._current)
            self._rebuild()
        elif data and data != self._current:
            self._current = data
            self.folder_changed.emit(data)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        open_act = menu.addAction(f"Open in {FILE_MANAGER}")
        remove_act = menu.addAction("Remove this folder from the list")
        remove_act.setEnabled(len(self.store.folders()) > 1)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == open_act:
            show_in_explorer(self._current)
        elif chosen == remove_act:
            self.store.remove(self._current)


# -- One download in the list ------------------------------------------------------

class JobRow(QFrame):
    cancel_clicked = Signal(int)
    retry_clicked = Signal(int)
    source_clicked = Signal(int)  # Copy Source, on rows that offer it (offers_source)

    def __init__(self, job_id: int, url: str, quality: str, parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self.url = url
        self.files: list[str] = []
        self.source = ""  # on-screen credit, set by the engine for platforms that have one
        self.state = "queued"

        self.title = ElidedLabel(url)
        self.title.setObjectName("jobTitle")
        self.status = QLabel("Queued")
        self.status.setObjectName("statusActive")
        self.detail = ElidedLabel(quality)
        self.detail.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(18)
        # Busy bars are a sweep of blocks drawn by the style; this keeps them moving.
        self._busy = QTimer(self, interval=40, timeout=self.bar.update)
        self.button = QPushButton("Cancel")
        self.button.clicked.connect(self._on_button)
        # Same width in every state so statuses line up down the list.
        self.button.setFixedWidth(self.button.fontMetrics().horizontalAdvance("Show in folder") + 48)
        self._quality = quality
        # Copy Source: shown once finished, on rows that offer it (the Downloader's).
        self.offers_source = False
        self.source_btn = QPushButton("Copy Source")
        self.source_btn.setToolTip("Copy this video's on-screen source credit.")
        self.source_btn.clicked.connect(lambda: self.source_clicked.emit(self.job_id))
        self.source_btn.hide()

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        grid.addWidget(self.title, 0, 0)
        grid.addWidget(self.status, 0, 1, Qt.AlignRight)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self.source_btn)
        actions.addWidget(self.button)
        grid.addLayout(actions, 0, 2, 2, 1, Qt.AlignVCenter)
        grid.addWidget(self.detail, 1, 0, 1, 2)
        grid.addWidget(self.bar, 2, 0, 1, 3)
        grid.addItem(QSpacerItem(0, 3), 3, 0)
        grid.addWidget(separator, 4, 0, 1, 3)
        grid.setColumnStretch(0, 1)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def _set_status(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setObjectName(kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def set_started(self) -> None:
        self.state = "running"
        self._set_status("Starting…", "statusActive")

    def set_progress(self, pct: float, status: str, detail: str) -> None:
        if self.finished:
            return
        self.state = "running"
        if pct < 0:
            self.bar.setRange(0, 0)  # animated "busy" bar
            self._busy.start()
        else:
            self._busy.stop()
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(pct * 10))
        self._set_status(status, "statusActive")
        self.detail.setText(detail or self._quality)

    def set_finished(self, outcome: str, message: str, files: list[str]) -> None:
        self.state = outcome
        self.files = files
        self._busy.stop()
        self.bar.setRange(0, 1000)
        self.button.setEnabled(True)
        if outcome == "done":
            self.bar.setValue(1000)
            n = len(files)
            self._set_status("Done" if n == 1 else f"Done · {n} files", "statusDone")
            where = os.path.basename(files[0]) if n == 1 else os.path.dirname(files[0])
            self.detail.setText(f"{where}  ·  {message}" if message else where)
            self.button.setText("Show in folder")
            self.source_btn.setVisible(self.offers_source)
        elif outcome == "cancelled":
            self.bar.setValue(0)
            self._set_status("Cancelled", "muted")
            self.detail.setText(self._quality)
            self.button.setText("Retry")
        else:
            self.bar.setValue(0)
            self._set_status("Failed", "statusFailed")
            self.detail.setText(message)
            self.button.setText("Retry")

    def _on_button(self) -> None:
        if self.state == "done":
            show_in_explorer(self.files[0])
        elif self.finished:
            self.retry_clicked.emit(self.job_id)
        else:
            self.button.setEnabled(False)
            self._set_status("Cancelling…", "muted")
            self.cancel_clicked.emit(self.job_id)


class FitScrollArea(QScrollArea):
    """A vertical scroll area that is never squeezed narrower than what it holds, so a splitter
    or a small window can't cut the right-hand buttons off its rows (there's no sideways scroll)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def setWidget(self, widget) -> None:  # noqa: N802
        super().setWidget(widget)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.widget() and event.type() == QEvent.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(obj, event)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        if w := self.widget():
            width = (w.minimumSizeHint().width() + self.verticalScrollBar().sizeHint().width()
                     + 2 * self.frameWidth())
            hint.setWidth(max(hint.width(), width))
        return hint
