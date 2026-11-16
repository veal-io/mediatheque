"""The tool bar down the left of the window (Outlook-bar style): one big button per tool."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QVBoxLayout

from .theme import BLACK, LIGHT, SHADOW, SUNKEN, WHITE, bevel, device_space

ICON = 32
BUTTON = QSize(96, 78)
PRESSED_FILL = QColor("#6c6c6c")


class SidebarButton(QAbstractButton):
    def __init__(self, icon: QIcon, text: str, parent=None):
        super().__init__(parent)
        self.setIcon(icon)
        self.setText(text)
        self.setCheckable(True)
        self.setFixedSize(BUTTON)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)  # repaint on hover for the raised outline

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        active = self.isChecked() or self.isDown()
        with device_space(p) as px:
            r = px.rect(self.rect())
            if active:
                bevel(p, r, ((BLACK, LIGHT),), px.t, PRESSED_FILL)
            elif self.underMouse():
                bevel(p, r, ((LIGHT, BLACK),), px.t)
        shift = 1 if active else 0
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        icon_rect = QRect((self.width() - ICON) // 2 + shift, 8 + shift, ICON, ICON)
        p.drawPixmap(icon_rect, self.icon().pixmap(QSize(ICON, ICON), self.devicePixelRatioF()))
        p.setPen(WHITE)
        p.drawText(QRect(shift, icon_rect.bottom() + 4 + shift, self.width(), 22),
                   Qt.AlignHCenter | Qt.AlignTop, self.text())


class Sidebar(QFrame):
    current_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(BUTTON.width() + 12)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 8, 6, 8)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        # Toggled rather than clicked, so keyboard and accessibility presses (VoiceOver) switch tools too.
        self._group.idToggled.connect(lambda i, on: on and self.current_changed.emit(i))

    def add_tool(self, icon: QIcon, text: str) -> int:
        index = len(self._group.buttons())
        button = SidebarButton(icon, text)
        self._group.addButton(button, index)
        self._layout.insertWidget(index, button, 0, Qt.AlignHCenter)
        return index

    def set_current(self, index: int) -> None:
        if button := self._group.button(index):
            button.setChecked(True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        with device_space(p) as px:
            bevel(p, px.rect(self.rect()), SUNKEN, px.t, SHADOW)
