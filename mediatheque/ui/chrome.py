"""Classic window chrome: the gradient title bar and its caption buttons."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QLinearGradient, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from .theme import (
    BLACK, FACE, GLYPH_CLOSE, GLYPH_MAX, GLYPH_MIN, GLYPH_RESTORE, NAVY, PRESSED, RAISED,
    TITLE_END, TITLE_INACTIVE, TITLE_INACTIVE_END, WHITE, bevel, device_space, glyph, ui_font,
)

CAPTION_HEIGHT = 26
BUTTON_SIZE = QSize(22, 19)


class CaptionButton(QAbstractButton):
    def __init__(self, rows: list[str], tooltip: str, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setFixedSize(BUTTON_SIZE)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(tooltip)

    def set_glyph(self, rows: list[str]) -> None:
        self.rows = rows
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        with device_space(p) as px:
            down = self.isDown()
            r = bevel(p, px.rect(self.rect()), PRESSED if down else RAISED, px.t, FACE)
            g = max(1, round(px.d * BUTTON_SIZE.height() / 14))  # classic glyphs are drawn for 16×14
            off = px.t if down else 0
            glyph(p, r.translated(off, off), self.rows, BLACK, g)


class TitleBar(QWidget):
    def __init__(self, window: QWidget):
        super().__init__(window)
        self.win = window
        self.setFixedHeight(CAPTION_HEIGHT)
        self.min_btn = CaptionButton(GLYPH_MIN, "Minimize")
        self.max_btn = CaptionButton(GLYPH_MAX, "Maximize")
        self.close_btn = CaptionButton(GLYPH_CLOSE, "Close")
        self.min_btn.clicked.connect(window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_maximized)
        self.close_btn.clicked.connect(window.close)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 3, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(self.min_btn)
        row.addWidget(self.max_btn)
        row.addSpacing(3)
        row.addWidget(self.close_btn)

    def toggle_maximized(self) -> None:
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def sync_state(self) -> None:
        self.max_btn.set_glyph(GLYPH_RESTORE if self.win.isMaximized() else GLYPH_MAX)
        self.max_btn.setToolTip("Restore" if self.win.isMaximized() else "Maximize")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        active = self.win.isActiveWindow()
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0, NAVY if active else TITLE_INACTIVE)
        grad.setColorAt(1, TITLE_END if active else TITLE_INACTIVE_END)
        p.fillRect(self.rect(), grad)

        icon_rect = QRect(4, (self.height() - 16) // 2, 16, 16)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(icon_rect, self.win.windowIcon().pixmap(QSize(16, 16), self.devicePixelRatioF()))

        p.setFont(ui_font(10, bold=True))
        p.setPen(WHITE if active else FACE)
        text_rect = QRect(icon_rect.right() + 6, 0, self.min_btn.x() - icon_rect.right() - 12, self.height())
        title = p.fontMetrics().elidedText(self.win.windowTitle(), Qt.ElideRight, text_rect.width())
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, title)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.toggle_maximized()
