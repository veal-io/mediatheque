"""Retro Windows 95/98 look.

A QProxyStyle over Fusion that paints the classic 3D bevels itself. All bevels are
drawn in whole *device* pixels (lines are round(scale) pixels thick), so the look
stays crisp on 4K displays at any Windows scaling level instead of going blurry.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QProxyStyle, QStyle, QStyleFactory

# -- The Windows 98 palette ---------------------------------------------------------
FACE = QColor("#c0c0c0")
LIGHT = QColor("#dfdfdf")
WHITE = QColor("#ffffff")
SHADOW = QColor("#808080")
BLACK = QColor("#000000")
NAVY = QColor("#000080")
TITLE_END = QColor("#1084d0")
TITLE_INACTIVE = QColor("#808080")
TITLE_INACTIVE_END = QColor("#b5b5b5")
TIP = QColor("#ffffe1")

# Bevels as (top-left colour, bottom-right colour) per ring, outermost first.
RAISED = ((WHITE, BLACK), (LIGHT, SHADOW))      # buttons, tab pages
WINDOW = ((LIGHT, BLACK), (WHITE, SHADOW))      # window frame, menus
SUNKEN = ((SHADOW, WHITE), (BLACK, LIGHT))      # text fields, lists
PRESSED = ((BLACK, WHITE), (SHADOW, LIGHT))     # pushed-in buttons
THIN_SUNKEN = ((SHADOW, WHITE),)                # status bar panels, progress bars

S = QStyle
STATE = S.StateFlag

# -- Pixel helpers ------------------------------------------------------------------


class Px:
    """Converts logical coordinates to whole device pixels for the current painter."""

    def __init__(self, p: QPainter):
        dev = p.device()
        self.d = dev.devicePixelRatioF() if dev is not None else 1.0
        self.t = max(1, round(self.d))  # one "classic pixel", in device pixels

    def rect(self, r) -> QRect:
        r = QRectF(r)
        x0, y0 = round(r.left() * self.d), round(r.top() * self.d)
        x1, y1 = round((r.left() + r.width()) * self.d), round((r.top() + r.height()) * self.d)
        return QRect(x0, y0, x1 - x0, y1 - y0)


@contextmanager
def device_space(p: QPainter):
    """Paint in device pixels: no antialiasing, no fractional scaling."""
    px = Px(p)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    p.scale(1 / px.d, 1 / px.d)
    try:
        yield px
    finally:
        p.restore()


def bevel(p: QPainter, r: QRect, rings, t: int, fill: QColor | None = None) -> QRect:
    for tl, br in rings:
        if r.width() <= 0 or r.height() <= 0:
            return r
        p.fillRect(QRect(r.left(), r.top(), r.width(), t), tl)
        p.fillRect(QRect(r.left(), r.top(), t, r.height()), tl)
        p.fillRect(QRect(r.left(), r.bottom() - t + 1, r.width(), t), br)
        p.fillRect(QRect(r.right() - t + 1, r.top(), t, r.height()), br)
        r = r.adjusted(t, t, -t, -t)
    if fill is not None and r.width() > 0 and r.height() > 0:
        p.fillRect(r, fill)
    return r


def glyph(p: QPainter, r: QRect, rows: list[str], color: QColor, g: int) -> None:
    """Draw a little bitmap ('K' = pixel) centred in r, each pixel g×g device pixels."""
    w, h = len(rows[0]) * g, len(rows) * g
    x0, y0 = r.left() + (r.width() - w) // 2, r.top() + (r.height() - h) // 2
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == "K":
                p.fillRect(QRect(x0 + x * g, y0 + y * g, g, g), color)


ARROW_DOWN = ["KKKKKKK", ".KKKKK.", "..KKK..", "...K..."]
ARROW_UP = ARROW_DOWN[::-1]
ARROW_RIGHT = ["K...", "KK..", "KKK.", "KKKK", "KKK.", "KK..", "K..."]
ARROW_LEFT = [row[::-1] for row in ARROW_RIGHT]

GLYPH_CLOSE = ["KK....KK", ".KK..KK.", "..KKKK..", "...KK...", "..KKKK..", ".KK..KK.", "KK....KK"]
GLYPH_MIN = ["........", "........", "........", "........", "........", "KKKKKK..", "KKKKKK.."]
GLYPH_MAX = ["KKKKKKKKK", "KKKKKKKKK", "K.......K", "K.......K", "K.......K",
             "K.......K", "K.......K", "K.......K", "KKKKKKKKK"]
GLYPH_RESTORE = ["..KKKKKK.", "..KKKKKK.", "..K....K.", "KKKKKK.K.", "KKKKKK.K.",
                 "K....KKK.", "K....K...", "K....K...", "KKKKKK..."]


def _etched(p: QPainter, r: QRect, t: int) -> None:
    for color, off in ((SHADOW, 0), (WHITE, t)):
        rr = QRect(r.left() + off, r.top() + off, r.width() - t, r.height() - t)
        p.fillRect(QRect(rr.left(), rr.top(), rr.width(), t), color)
        p.fillRect(QRect(rr.left(), rr.top(), t, rr.height()), color)
        p.fillRect(QRect(rr.left(), rr.bottom() - t + 1, rr.width(), t), color)
        p.fillRect(QRect(rr.right() - t + 1, rr.top(), t, rr.height()), color)


def _enabled(opt) -> bool:
    return bool(opt.state & STATE.State_Enabled)


# -- The style ----------------------------------------------------------------------


class RetroStyle(QProxyStyle):
    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))
        self._dither: dict[int, QBrush] = {}

    # metrics ---------------------------------------------------------------------

    def pixelMetric(self, m, opt=None, w=None):  # noqa: N802
        PM = S.PixelMetric
        fixed = {
            PM.PM_ScrollBarExtent: 17,
            PM.PM_ScrollBarSliderMin: 14,
            PM.PM_ButtonShiftHorizontal: 1,
            PM.PM_ButtonShiftVertical: 1,
            PM.PM_DefaultFrameWidth: 2,
            PM.PM_TabBarBaseOverlap: 2,
            PM.PM_TabBarTabShiftVertical: 2,
            PM.PM_TabBarTabShiftHorizontal: 0,
            PM.PM_MenuBarPanelWidth: 0,
            PM.PM_MenuPanelWidth: 3,
            PM.PM_ToolTipLabelFrameWidth: 3,
            PM.PM_TabBarTabOverlap: 0,
        }
        if m in fixed:
            return fixed[m]
        return super().pixelMetric(m, opt, w)

    def styleHint(self, h, opt=None, w=None, ret=None):  # noqa: N802
        SH = S.StyleHint
        if h in (SH.SH_EtchDisabledText, SH.SH_UnderlineShortcut):
            return 1
        if h in (SH.SH_ComboBox_Popup, SH.SH_DitherDisabledText, SH.SH_ScrollBar_Transient,
                 SH.SH_Menu_FadeOutOnHide, SH.SH_Widget_Animation_Duration):
            return 0
        return super().styleHint(h, opt, w, ret)

    def sizeFromContents(self, ct, opt, size, w=None):  # noqa: N802
        s = super().sizeFromContents(ct, opt, size, w)
        CT = S.ContentsType
        if ct == CT.CT_PushButton:
            return QSize(max(s.width() + 8, 84), max(s.height(), 27))
        if ct in (CT.CT_LineEdit, CT.CT_ComboBox):
            return QSize(s.width(), max(s.height(), 27))
        if ct == CT.CT_TabBarTab:
            return QSize(s.width() + 14, s.height() + 4)
        return s

    def subControlRect(self, cc, opt, sc, w=None):  # noqa: N802
        if cc == S.ComplexControl.CC_ComboBox:
            r, b, aw = opt.rect, 2, 17
            if sc == S.SubControl.SC_ComboBoxArrow:
                return QRect(r.right() - b - aw + 1, r.top() + b, aw, r.height() - 2 * b)
            if sc == S.SubControl.SC_ComboBoxEditField:
                return QRect(r.left() + b + 2, r.top() + b, r.width() - 2 * b - aw - 4, r.height() - 2 * b)
            if sc == S.SubControl.SC_ComboBoxFrame:
                return QRect(r)
        return super().subControlRect(cc, opt, sc, w)

    def subElementRect(self, se, opt, w=None):  # noqa: N802
        r = super().subElementRect(se, opt, w)
        if se == S.SubElement.SE_TabWidgetTabBar:
            r.translate(2, 0)  # room for the selected tab to bulge left
        return r

    # primitives ------------------------------------------------------------------

    def drawPrimitive(self, pe, opt, p, w=None):  # noqa: N802
        PE = S.PrimitiveElement
        if pe in (PE.PE_PanelButtonCommand, PE.PE_PanelButtonBevel, PE.PE_PanelButtonTool):
            return self._button(opt, p, w)
        if pe in (PE.PE_FrameDefaultButton, PE.PE_FrameTabBarBase, PE.PE_FrameButtonBevel):
            return
        if pe == PE.PE_PanelLineEdit:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), SUNKEN, px.t, WHITE if _enabled(opt) else FACE)
            return
        if pe == PE.PE_FrameLineEdit:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), SUNKEN, px.t)
            return
        if pe == PE.PE_Frame:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), SUNKEN if opt.state & STATE.State_Sunken else RAISED, px.t)
            return
        if pe == PE.PE_FrameTabWidget:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), RAISED, px.t, FACE)
            return
        if pe == PE.PE_FrameGroupBox:
            with device_space(p) as px:
                _etched(p, px.rect(opt.rect), px.t)
            return
        if pe == PE.PE_FrameStatusBarItem:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), THIN_SUNKEN, px.t)
            return
        if pe in (PE.PE_PanelMenu, PE.PE_FrameMenu):
            with device_space(p) as px:
                r = px.rect(opt.rect)
                if isinstance(w, QMenu):
                    bevel(p, r, WINDOW, px.t, FACE if pe == PE.PE_PanelMenu else None)
                else:  # combo box drop-down list
                    bevel(p, r, ((BLACK, BLACK),), px.t, WHITE if pe == PE.PE_PanelMenu else None)
            return
        if pe == PE.PE_PanelMenuBar:
            p.fillRect(opt.rect, FACE)
            return
        if pe == PE.PE_PanelTipLabel:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), ((BLACK, BLACK),), px.t, TIP)
            return
        if pe == PE.PE_FrameFocusRect:
            with device_space(p) as px:
                self._focus_rect(p, px.rect(opt.rect), px.t)
            return
        arrows = {PE.PE_IndicatorArrowDown: ARROW_DOWN, PE.PE_IndicatorArrowUp: ARROW_UP,
                  PE.PE_IndicatorArrowLeft: ARROW_LEFT, PE.PE_IndicatorArrowRight: ARROW_RIGHT}
        if pe in arrows:
            with device_space(p) as px:
                glyph(p, px.rect(opt.rect), arrows[pe], BLACK if _enabled(opt) else SHADOW, px.t)
            return
        super().drawPrimitive(pe, opt, p, w)

    @staticmethod
    def _focus_rect(p: QPainter, r: QRect, t: int) -> None:
        pen = QPen(BLACK, t, Qt.CustomDashLine)
        pen.setDashPattern([1, 1])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(t // 2, t // 2, -t + t // 2, -t + t // 2))

    def _button(self, opt, p: QPainter, w) -> None:
        st = opt.state
        down = bool(st & (STATE.State_Sunken | STATE.State_On))
        default = w is not None and w.objectName() == "primary"
        with device_space(p) as px:
            t, r = px.t, px.rect(opt.rect)
            if default:  # the default button gets an extra black outline
                r = bevel(p, r, ((BLACK, BLACK),), t)
            if down and default:
                inner = bevel(p, r, ((SHADOW, SHADOW),), t, FACE)
            else:
                inner = bevel(p, r, PRESSED if down else RAISED, t, FACE)
            if st & STATE.State_HasFocus and w is not None and w.focusPolicy() != Qt.NoFocus:
                self._focus_rect(p, inner.adjusted(2 * t, 2 * t, -2 * t, -2 * t), t)

    # controls --------------------------------------------------------------------

    def drawControl(self, ce, opt, p, w=None):  # noqa: N802
        CE = S.ControlElement
        if ce == CE.CE_ProgressBarGroove:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), THIN_SUNKEN, px.t, FACE)
            return
        if ce == CE.CE_ProgressBarContents:
            return self._progress(opt, p, w)
        if ce == CE.CE_ProgressBarLabel:
            return
        if ce == CE.CE_TabBarTabShape:
            return self._tab(opt, p)
        if ce == CE.CE_MenuBarEmptyArea:
            p.fillRect(opt.rect, FACE)
            return
        if ce == CE.CE_MenuBarItem:
            selected = bool(opt.state & STATE.State_Selected) and _enabled(opt)
            p.fillRect(opt.rect, NAVY if selected else FACE)
            p.setPen(WHITE if selected else (BLACK if _enabled(opt) else SHADOW))
            p.drawText(opt.rect, Qt.AlignCenter | Qt.TextShowMnemonic, opt.text)
            return
        if ce == CE.CE_MenuItem and opt.menuItemType == opt.MenuItemType.Separator:
            with device_space(p) as px:
                r = px.rect(opt.rect)
                y = r.top() + (r.height() - 2 * px.t) // 2
                p.fillRect(QRect(r.left() + px.t, y, r.width() - 2 * px.t, px.t), SHADOW)
                p.fillRect(QRect(r.left() + px.t, y + px.t, r.width() - 2 * px.t, px.t), WHITE)
            return
        if ce == CE.CE_ComboBoxLabel:
            return self._combo_label(opt, p, w)
        if ce == CE.CE_SizeGrip:
            return self._size_grip(opt, p)
        super().drawControl(ce, opt, p, w)

    def _progress(self, opt, p: QPainter, w) -> None:
        with device_space(p) as px:
            t = px.t
            r = px.rect(opt.rect).adjusted(2 * t, 2 * t, -2 * t, -2 * t)
            if r.width() <= 0 or r.height() <= 0:
                return
            bw = max(2 * t, round(r.height() * 0.6))
            step = bw + max(t, round(bw / 4))
            p.setClipRect(r)
            if opt.minimum == opt.maximum:  # busy: a run of blocks sweeping across
                group = 4 * step
                travel = r.width() + group
                off = int(time.monotonic() * r.width() * 0.6) % travel - group
                for i in range(4):
                    p.fillRect(QRect(r.left() + off + i * step, r.top(), bw, r.height()), NAVY)
                return
            frac = (opt.progress - opt.minimum) / max(1, opt.maximum - opt.minimum)
            end = r.left() + round(r.width() * frac)
            x = r.left()
            while x < end:
                p.fillRect(QRect(x, r.top(), bw, r.height()), NAVY)
                x += step

    def _tab(self, opt, p: QPainter) -> None:
        with device_space(p) as px:
            t = px.t
            r = px.rect(opt.rect)
            if opt.state & STATE.State_Selected:
                r = r.adjusted(-2 * t, 0, 2 * t, 0)       # bulges out and covers the page edge
            else:
                r = r.adjusted(0, 2 * t, 0, -2 * t)       # sits lower, page edge shows below
            x0, y0, x1, h = r.left(), r.top(), r.right(), r.height()
            p.fillRect(QRect(x0 + t, y0 + t, r.width() - 2 * t, h - t), FACE)
            p.fillRect(QRect(x0, y0 + 2 * t, t, h - 2 * t), WHITE)
            p.fillRect(QRect(x0 + t, y0 + t, t, t), WHITE)
            p.fillRect(QRect(x0 + 2 * t, y0, r.width() - 4 * t, t), WHITE)
            p.fillRect(QRect(x0 + t, y0 + 2 * t, t, h - 2 * t), LIGHT)
            p.fillRect(QRect(x0 + 2 * t, y0 + t, r.width() - 4 * t, t), LIGHT)
            p.fillRect(QRect(x1 - t + 1, y0 + 2 * t, t, h - 2 * t), BLACK)
            p.fillRect(QRect(x1 - 2 * t + 1, y0 + t, t, t), BLACK)
            p.fillRect(QRect(x1 - 2 * t + 1, y0 + 2 * t, t, h - 2 * t), SHADOW)

    def _combo_label(self, opt, p: QPainter, w) -> None:
        r = self.subControlRect(S.ComplexControl.CC_ComboBox, opt, S.SubControl.SC_ComboBoxEditField, w)
        focus = bool(opt.state & STATE.State_HasFocus) and not opt.editable
        if focus:  # classic: the selected text is highlighted while the box has focus
            with device_space(p) as px:
                hr = px.rect(r).adjusted(0, px.t, 0, -px.t)
                p.fillRect(hr, NAVY)
                self._focus_rect(p, hr, px.t)
        p.setPen(WHITE if focus else (BLACK if _enabled(opt) else SHADOW))
        text = p.fontMetrics().elidedText(opt.currentText, Qt.ElideRight, r.width() - 6)
        p.drawText(r.adjusted(3, 0, -3, 0), Qt.AlignLeft | Qt.AlignVCenter, text)

    def _size_grip(self, opt, p: QPainter) -> None:
        with device_space(p) as px:
            t, r = px.t, px.rect(opt.rect)
            right, bottom = r.right() + 1, r.bottom() + 1
            for group in range(3):
                for k, color in enumerate((WHITE, SHADOW, SHADOW)):
                    n = 2 + group * 4 + k
                    for j in range(n + 1):
                        p.fillRect(QRect(right - (n - j + 1) * t, bottom - (j + 1) * t, t, t), color)

    # complex controls ---------------------------------------------------------------

    def drawComplexControl(self, cc, opt, p, w=None):  # noqa: N802
        CC, SC = S.ComplexControl, S.SubControl
        if cc == CC.CC_ComboBox:
            with device_space(p) as px:
                bevel(p, px.rect(opt.rect), SUNKEN, px.t, WHITE if _enabled(opt) else FACE)
                ar = px.rect(self.subControlRect(cc, opt, SC.SC_ComboBoxArrow, w))
                down = bool(opt.state & STATE.State_Sunken)
                bevel(p, ar, ((SHADOW, SHADOW),) if down else RAISED, px.t, FACE)
                off = px.t if down else 0
                glyph(p, ar.translated(off, off), ARROW_DOWN, BLACK if _enabled(opt) else SHADOW, px.t)
            return
        if cc == CC.CC_ScrollBar:
            return self._scrollbar(opt, p, w)
        if cc == CC.CC_GroupBox:
            label = self.subControlRect(cc, opt, SC.SC_GroupBoxLabel, w).translated(8, 0)
            frame = self.subControlRect(cc, opt, SC.SC_GroupBoxFrame, w)
            frame.setTop(label.center().y())  # the title sits on the etched line
            with device_space(p) as px:
                _etched(p, px.rect(frame), px.t)
                p.fillRect(px.rect(label.adjusted(-3, 0, 3, 0)), opt.palette.window())
            self.drawItemText(p, label, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextShowMnemonic,
                              opt.palette, _enabled(opt), opt.text, QPalette.WindowText)
            return
        super().drawComplexControl(cc, opt, p, w)

    def _dither_brush(self, t: int) -> QBrush:
        if t not in self._dither:
            pm = QPixmap(2 * t, 2 * t)
            pm.fill(FACE)
            qp = QPainter(pm)
            qp.fillRect(0, 0, t, t, WHITE)
            qp.fillRect(t, t, t, t, WHITE)
            qp.end()
            self._dither[t] = QBrush(pm)
        return self._dither[t]

    def _scrollbar(self, opt, p: QPainter, w) -> None:
        SC = S.SubControl
        cc = S.ComplexControl.CC_ScrollBar
        horiz = opt.orientation == Qt.Horizontal
        usable = _enabled(opt) and opt.maximum > opt.minimum
        with device_space(p) as px:
            p.fillRect(px.rect(self.subControlRect(cc, opt, SC.SC_ScrollBarGroove, w)), self._dither_brush(px.t))
            for sc, arrow in ((SC.SC_ScrollBarSubLine, ARROW_LEFT if horiz else ARROW_UP),
                              (SC.SC_ScrollBarAddLine, ARROW_RIGHT if horiz else ARROW_DOWN)):
                r = px.rect(self.subControlRect(cc, opt, sc, w))
                down = bool(opt.activeSubControls & sc) and bool(opt.state & STATE.State_Sunken)
                bevel(p, r, ((SHADOW, SHADOW),) if down else RAISED, px.t, FACE)
                off = px.t if down else 0
                glyph(p, r.translated(off, off), arrow, BLACK if usable else SHADOW, px.t)
            if usable:
                bevel(p, px.rect(self.subControlRect(cc, opt, SC.SC_ScrollBarSlider, w)), RAISED, px.t, FACE)


# -- Pixel-art icons ------------------------------------------------------------------

def _art(rows: list[str], colors: dict[str, QColor]) -> QImage:
    img = QImage(len(rows[0]), len(rows), QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c in colors:
                img.setPixelColor(x, y, colors[c])
    return img


def _icon(img: QImage) -> QIcon:
    icon = QIcon()
    for scale in (1, 2, 3, 4, 8, 16):  # nearest-neighbour multiples keep the pixels sharp
        icon.addPixmap(QPixmap.fromImage(img.scaled(img.width() * scale, img.height() * scale,
                                                    Qt.IgnoreAspectRatio, Qt.FastTransformation)))
    return icon


APP_ART = [
    "................",
    ".WWWWWWWWWWWWWK.",
    ".WGGGGGGGGGGGDK.",
    ".WGKKKKKKKKKGDK.",
    ".WGKCCCCCCCKGDK.",
    ".WGKCCWCCCCKGDK.",
    ".WGKCCWWCCCKGDK.",
    ".WGKCCWWWCCKGDK.",
    ".WGKCCWWCCCKGDK.",
    ".WGKCCWCCCCKGDK.",
    ".WGKKKKKKKKKGDK.",
    ".WGGGGGGGGGGGDK.",
    ".WDDDDDDDDDDDDK.",
    ".KKKKKKKKKKKKKK.",
    "....WGGGGGGDK...",
    "...KKKKKKKKKK...",
]

YOUTUBE_ART = [
    "................", "................", "................",
    "..RRRRRRRRRRRR..",
    ".RRRRRRRRRRRRRR.",
    ".RRRRRWRRRRRRRR.",
    ".RRRRRWWRRRRRRR.",
    ".RRRRRWWWRRRRRR.",
    ".RRRRRWWWWRRRRR.",
    ".RRRRRWWWRRRRRR.",
    ".RRRRRWWRRRRRRR.",
    ".RRRRRWRRRRRRRR.",
    ".RRRRRRRRRRRRRR.",
    "..RRRRRRRRRRRR..",
    "................", "................",
]

TIKTOK_NOTE = [
    "................",
    "........KK......",
    "........KKK.....",
    "........KKKK....",
    "........KK.KK...",
    "........KK..K...",
    "........KK......",
    "........KK......",
    "........KK......",
    "....KKKKKK......",
    "...KKKKKKK......",
    "...KKKKKKK......",
    "....KKKKK.......",
    "................", "................", "................",
]

INSTAGRAM_ART = [
    "................",
    "..PPPPPPPPPPPP..",
    ".P............P.",
    ".P.........PP.P.",
    ".P.........PP.P.",
    ".P....PPPP....P.",
    ".P...P....P...P.",
    ".P..P......P..P.",
    ".P..P......P..P.",
    ".P...P....P...P.",
    ".P....PPPP....P.",
    ".P............P.",
    ".P............P.",
    "..PPPPPPPPPPPP..",
    "................", "................",
]


DOWNLOADER_ART = [
    "................",
    ".....KKKKKK.....",
    ".....KGGGGK.....",
    ".....KGGGGK.....",
    ".....KGGGGK.....",
    "..KKKKGGGGKKKK..",
    "...KGGGGGGGGK...",
    "....KGGGGGGK....",
    ".....KGGGGK.....",
    "......KGGK......",
    ".......KK.......",
    "................",
    ".K............K.",
    ".KWWWWWWWWWWWWK.",
    ".KDDDDDDDDDDDDK.",
    ".KKKKKKKKKKKKKK.",
]

CREDITS_ART = [
    "................",
    "..KKKKKKKKKKKK..",
    "..KWWWWWWWWWWKD.",
    "..KWNNWNNWNNWKD.",
    "..KWWWWWWWWWWKD.",
    "..KWNNWNNWNNWKD.",
    "..KWWWWWWWWWWKD.",
    "..KWNNWNNWNNWKD.",
    "..KWWWWWWWWWWKD.",
    "..KWNNWNNWNNWKD.",
    "..KWWWWWWWWWWKD.",
    "..KWNNWNNWNNWKD.",
    "..KWWWWWWWWWWKD.",
    "..KKKKKKKKKKKKD.",
    "...DDDDDDDDDDDD.",
    "................",
]


SCREENSHOTS_ART = [
    "................",
    "................",
    "....KKKK........",
    "...KDDDDK.......",
    ".KKKKKKKKKKKKKK.",
    ".KGGGGGGGGGGGGK.",
    ".KGGGKKKKKGGRGK.",
    ".KGGKWCCCKKGGGK.",
    ".KGKWCCNCCKGGGK.",
    ".KGKCCNNNCKGGGK.",
    ".KGKCCCNCCKGGGK.",
    ".KGGKCCCCKGGGGK.",
    ".KGGGKKKKGGGGGK.",
    ".KDDDDDDDDDDDDK.",
    ".KKKKKKKKKKKKKK.",
    "................",
]


SCRIPT_ART = [
    "................",
    "..KKKKKKKKK.....",
    "..KWWWWWWWKK....",
    "..KWNNNNWWKWK...",
    "..KWWWWWWWKKKK..",
    "..KWNNNNNNNNWK..",
    "..KWWWWWWWWWWK..",
    "..KWYYYYYYYYWK..",
    "..KWWWWWWWWWWK..",
    "..KWNNNNNNWWWK..",
    "..KWWWWWWWWWWK..",
    "..KWYYYYYYWWWK..",
    "..KWWWWWWWWWWK..",
    "..KWNNNNNNNNWK..",
    "..KWWWWWWWWWWK..",
    "..KKKKKKKKKKKK..",
]


def tool_icon(key: str) -> QIcon:
    if key == "script":
        return _icon(_art(SCRIPT_ART, {"K": BLACK, "W": WHITE, "N": NAVY, "Y": QColor("#ffd800")}))
    if key == "downloader":
        return _icon(_art(DOWNLOADER_ART, {"K": BLACK, "G": QColor("#00a000"), "W": WHITE, "D": SHADOW}))
    if key == "screenshots":
        return _icon(_art(SCREENSHOTS_ART, {"K": BLACK, "D": SHADOW, "G": FACE, "W": WHITE,
                                            "C": QColor("#4f6fd0"), "N": NAVY, "R": QColor("#ff4040")}))
    if key == "credits":
        return _icon(_art(CREDITS_ART, {"K": BLACK, "W": WHITE, "N": NAVY, "D": QColor("#404040")}))
    return QIcon()


def make_icon() -> QIcon:
    return _icon(_art(APP_ART, {"K": BLACK, "W": WHITE, "G": FACE, "D": SHADOW, "C": QColor("#008080")}))


FACEBOOK_ART = [
    "................",
    ".BBBBBBBBBBBBBB.",
    ".BBBBBBBBWWWWBB.",
    ".BBBBBBBWWWWWBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBWWWWWWBBB.",
    ".BBBBBWWWWWWBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    ".BBBBBBBWWBBBBB.",
    "................",
]


def platform_icon(key: str) -> QIcon:
    if key == "facebook":
        return _icon(_art(FACEBOOK_ART, {"B": QColor("#1877f2"), "W": WHITE}))
    if key == "youtube":
        return _icon(_art(YOUTUBE_ART, {"R": QColor("#ff0000"), "W": WHITE}))
    if key == "tiktok":
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        for dx, color in ((-1, QColor("#25f4ee")), (1, QColor("#fe2c55")), (0, BLACK)):
            p.drawImage(dx, 0, _art(TIKTOK_NOTE, {"K": color}))
        p.end()
        return _icon(img)
    if key == "instagram":
        img = _art(INSTAGRAM_ART, {"P": BLACK})
        stops = [QColor("#feda75"), QColor("#fa7e1e"), QColor("#d62976"), QColor("#962fbf"), QColor("#4f5bd5")]
        for y in range(16):
            f = y / 15 * (len(stops) - 1)
            a, b, k = stops[int(f)], stops[min(int(f) + 1, len(stops) - 1)], f - int(f)
            c = QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                       round(a.blue() + (b.blue() - a.blue()) * k))
            for x in range(16):
                if img.pixelColor(x, y).alpha():
                    img.setPixelColor(x, y, c)
        return _icon(img)
    return QIcon()


# -- Applying it ----------------------------------------------------------------------

# Small, colour-only rules. Bevels and controls are all drawn by RetroStyle.
STYLESHEET = """
QLabel#error { color: #c00000; }
QLabel#muted { color: #404040; }
QLabel#empty { color: #808080; }
/* MS Sans Serif has no true bold; Tahoma is its bold companion from the same era. */
QLabel#jobTitle { font-family: "Tahoma"; font-weight: bold; }
QLabel#statusDone { color: #008000; font-weight: bold; }
QLabel#statusFailed { color: #c00000; font-weight: bold; }
QLabel#statusActive { color: #000000; }
"""


def ui_font(point_size: float = 10, bold: bool = False) -> QFont:
    families = set(QFontDatabase.families())
    family = next((f for f in ("Microsoft Sans Serif", "Tahoma", "Segoe UI") if f in families), "")
    font = QFont(family)
    # Sizes are Windows points (96 dpi). macOS counts 72 per inch, so the same number comes out
    # a quarter smaller there; scale it up so text matches the Windows layout.
    font.setPointSizeF(point_size * (96 / 72 if sys.platform == "darwin" else 1))
    font.setBold(bold)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


def apply_theme(app: QApplication) -> None:
    app.setStyle(RetroStyle())
    pal = QPalette()
    for role, color in (
        (QPalette.Window, FACE), (QPalette.WindowText, BLACK), (QPalette.Base, WHITE),
        (QPalette.AlternateBase, LIGHT), (QPalette.Text, BLACK), (QPalette.Button, FACE),
        (QPalette.ButtonText, BLACK), (QPalette.Highlight, NAVY), (QPalette.HighlightedText, WHITE),
        (QPalette.ToolTipBase, TIP), (QPalette.ToolTipText, BLACK), (QPalette.PlaceholderText, SHADOW),
        (QPalette.Light, WHITE), (QPalette.Midlight, LIGHT), (QPalette.Mid, SHADOW),
        (QPalette.Dark, SHADOW), (QPalette.Shadow, BLACK), (QPalette.BrightText, WHITE), (QPalette.Link, NAVY),
    ):
        pal.setColor(role, color)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, SHADOW)
    app.setPalette(pal)
    app.setFont(ui_font())
    if sys.platform == "darwin":
        # macOS gives menus, dropdown lists, tooltips and a few others their own (smaller) system
        # sizes, which app.setFont() alone doesn't replace.
        for cls in ("QMenu", "QMenuBar", "QComboMenuItem", "QTipLabel", "QMessageBox", "QAbstractItemView"):
            app.setFont(ui_font(), cls)
    app.setStyleSheet(STYLESHEET)
