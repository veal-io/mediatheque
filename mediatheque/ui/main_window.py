from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMenuBar, QMessageBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..config import Settings, log_dir
from ..engine import YTDLP_VERSION
from .chrome import TitleBar
from .credits_page import CreditsPage
from .screenshots_page import ScreenshotsPage
from .script_page import ScriptPage
from .downloader_page import DownloaderPage
from .sidebar import Sidebar
from .theme import WINDOW, bevel, device_space, make_icon, tool_icon
from ..osutil import open_path
from .widgets import FolderStore, show_in_explorer

CREATE_NO_WINDOW = 0x08000000
BORDER = 4  # classic window border, logical pixels
READY = "Ready"
TOOLS = [("script", "Script"), ("downloader", "Downloader"), ("screenshots", "Screenshots"),
         ("credits", "Credits")]  # sidebar order; Ctrl+1… follow it
_OLD_ORDER = ["downloader", "screenshots", "credits", "script"]  # how "tool" was saved before
TOOL_TITLES = {"downloader": "Downloader", "screenshots": "Article Screenshots", "credits": "Credit Generator",
               "script": "Script"}


class _Updater(QObject):
    """Updates yt-dlp in the background. Platforms change often; yt-dlp keeps up."""
    done = Signal(bool, str)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        python = sys.executable
        if python.lower().endswith("pythonw.exe"):
            python = python[:-len("pythonw.exe")] + "python.exe"
        cmd = [python, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
               "yt-dlp[default]", "yt-dlp-ejs"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                               creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            ver = subprocess.run([python, "-c", "import yt_dlp.version as v; print(v.__version__)"],
                                 capture_output=True, text=True, timeout=60,
                                 creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            if r.returncode == 0:
                self.done.emit(True, ver.stdout.strip())
            else:
                self.done.emit(False, (r.stderr or r.stdout).strip()[-1500:])
        except Exception as e:
            self.done.emit(False, str(e))


def _status_panel(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setContentsMargins(4, 1, 4, 1)
    return label


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        # We draw our own Windows 98 frame and title bar.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle("Mediatheque")
        self.setWindowIcon(make_icon())
        self.setMouseTracking(True)
        self.resize(1180, 820)
        self.setMinimumSize(760, 560)
        self._styled_native = False

        # -- title bar + menus -------------------------------------------------------
        self.title_bar = TitleBar(self)
        self.menu = QMenuBar()
        top = QWidget()
        col = QVBoxLayout(top)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(self.title_bar)
        col.addWidget(self.menu)
        # Keep the menus in the window on a Mac too, under the title bar. Set once it has a parent:
        # Qt hid it when it was made as a Mac menu bar and only shows it again if it has one.
        self.menu.setNativeMenuBar(False)
        self.setMenuWidget(top)

        # -- the Downloader: one page for every platform ------------------------------
        self.folders = FolderStore(settings)
        self.downloader = DownloaderPage(settings, self.folders)
        self.downloader.status.connect(self.show_status)

        # -- tools: sidebar on the left, the chosen tool fills the rest -------------
        self.credits = CreditsPage(settings)
        self.credits.status.connect(self.show_status)
        self.screenshots = ScreenshotsPage(settings, self.folders)
        self.screenshots.status.connect(self.show_status)
        self.screenshots.send_to_downloader.connect(self._queue_videos)
        # The Script page shares the capture window with the Screenshots page (one browser profile).
        self.script = ScriptPage(settings, self.folders, self.screenshots.worker)
        self.script.status.connect(self.show_status)
        self.pages = {"downloader": self.downloader, "screenshots": self.screenshots, "credits": self.credits,
                      "script": self.script}
        self.stack = QStackedWidget()
        for key, _label in TOOLS:
            self.stack.addWidget(self.pages[key])
        self.sidebar = Sidebar()
        for key, label in TOOLS:
            self.sidebar.add_tool(tool_icon(key), label)
        self.sidebar.current_changed.connect(self.show_tool)

        central = QWidget()
        box = QHBoxLayout(central)
        box.setContentsMargins(8, 8, 8, 6)
        box.setSpacing(8)
        box.addWidget(self.sidebar)
        box.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._build_menus()

        # -- status bar --------------------------------------------------------------
        sb = self.statusBar()
        self.status_msg = _status_panel(READY)
        self.engine_label = _status_panel(f"yt-dlp {YTDLP_VERSION}")
        sb.addPermanentWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.engine_label)
        sb.setSizeGripEnabled(True)
        self._status_timer = QTimer(self, singleShot=True, timeout=lambda: self.status_msg.setText(READY))

        # The resize cursor is set on the window itself while over the border. Children
        # inherit a parent's cursor, so give everything inside the border its own arrow;
        # otherwise the resize cursor sticks once the mouse moves inward.
        for inner in (top, central, sb):
            inner.setCursor(Qt.ArrowCursor)

        self._updater = _Updater(self)
        self._updater.done.connect(self._update_done)

        win = settings.data.get("window", {})
        if geo := win.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        key = win.get("tool_key")
        if key is None and isinstance(win.get("tool"), int) and win["tool"] < len(_OLD_ORDER):
            key = _OLD_ORDER[win["tool"]]
        keys = [k for k, _ in TOOLS]
        self.show_tool(keys.index(key) if key in keys else 0)
        self._apply_frame()

    # -- menus ---------------------------------------------------------------------

    def _build_menus(self) -> None:
        def action(menu, text, slot, shortcut=None):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            menu.addAction(act)
            return act

        file_menu = self.menu.addMenu("&File")
        action(file_menu, "Open Script &Project…",
               lambda: (self.show_tool(self._tool_index("script")), self.script.browse()), "Ctrl+P")
        action(file_menu, "Import &Script for Screenshots…",
               lambda: (self.show_tool(self._tool_index("screenshots")), self.screenshots.browse_script()), "Ctrl+Shift+I")
        action(file_menu, "&Import Patron CSV…",
               lambda: (self.show_tool(self._tool_index("credits")), self.credits.browse()), "Ctrl+I")
        action(file_menu, "&Open Download Folder", lambda: show_in_explorer(self.downloader.folder.current_folder()),
               "Ctrl+O")
        action(file_menu, "Open &Logs Folder", lambda: open_path(str(log_dir())))
        file_menu.addSeparator()
        # On a Mac, Cmd+Q comes from the system's own app menu, which closes this window the same way.
        action(file_menu, "E&xit", self.close, None if sys.platform == "darwin" else "Alt+F4")

        view = self.menu.addMenu("&View")
        for i, (key, _label) in enumerate(TOOLS):
            action(view, f"&{TOOL_TITLES[key]}", lambda _=False, i=i: self.show_tool(i), f"Ctrl+{i + 1}")

        tools = self.menu.addMenu("&Tools")
        self.update_action = action(tools, "&Update Engine…", self._update_engine)
        action(tools, "Open Download &Logs", lambda: open_path(str(log_dir())))
        action(tools, "&Clear Finished Downloads", lambda: self.downloader.clear_finished())
        action(tools, "&Pack for Another Computer…", self._pack_for_transfer)
        tools.addSeparator()
        action(tools, "Sig&n In to Sites…", lambda: (self.show_tool(self._tool_index("screenshots")),
                                                    self.screenshots.sign_in()))
        action(tools, "Claude &API Key…", self._api_key)

        help_menu = self.menu.addMenu("&Help")
        action(help_menu, "&About Mediatheque", self._about)

    def show_status(self, text: str, ms: int = 8000) -> None:
        self.status_msg.setText(text)
        self._status_timer.start(ms)

    def _pack_for_transfer(self) -> None:
        """Tools > Pack for Another Computer: the app and every script project in one zip."""
        from PySide6.QtWidgets import QFileDialog
        from ..transfer import pack
        name = "Mediatheque for Mac.zip" if sys.platform == "win32" else "Mediatheque transfer.zip"
        start = os.path.join(os.path.expanduser("~"), "Desktop", name)
        path, _ = QFileDialog.getSaveFileName(self, "Pack Mediatheque for another computer", start,
                                              "Zip files (*.zip)")
        if not path:
            return
        self.script.save_all()
        try:
            n = pack(Path(path))
        except OSError as e:
            QMessageBox.warning(self, "Pack for Another Computer", f"Couldn't write the zip:\n\n{e}")
            return
        show_in_explorer(path)
        QMessageBox.information(
            self, "Pack for Another Computer",
            f"Packed Mediatheque and {n} script project{'s' if n != 1 else ''} into\n{path}\n\n"
            "Copy it to the other computer along with each video's project folder (BRoll, Screen Captures, "
            "Project Files…). MAC.md inside the zip says what to do next.")

    def _api_key(self) -> None:
        from .suggest_ui import ApiKeyDialog
        dlg = ApiKeyDialog(self)
        if dlg.exec() == ApiKeyDialog.Accepted:
            self.status_msg.setText("Removed the saved Claude API key." if dlg.removed else "Claude API key saved.")

    def _about(self) -> None:
        QMessageBox.about(
            self, "About Mediatheque",
            "<b>Mediatheque</b><br>Video essay multi-tool: YouTube, TikTok, Instagram and Facebook downloader,"
            " article screenshots, script projects, and the patron Credit Generator.<br><br>"
            f"Engine: yt-dlp {YTDLP_VERSION}")

    def show_tool(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.sidebar.set_current(index)
        self.setWindowTitle(f"Mediatheque - {TOOL_TITLES[TOOLS[index][0]]}")
        self.title_bar.update()
        key = TOOLS[index][0]
        if key == "downloader":
            self.downloader.link.setFocus()
        elif key == "screenshots":
            self.screenshots.link.setFocus()
        elif key == "script":
            self.script.view.setFocus()
        else:
            self.credits.import_btn.setFocus()

    @staticmethod
    def _tool_index(key: str) -> int:
        return next(i for i, (k, _label) in enumerate(TOOLS) if k == key)

    def _queue_videos(self, urls: list[str]) -> None:
        """Video links found in a script go to the Downloader."""
        counts = self.downloader.queue_urls(urls)
        self.show_status("Queued in the Downloader: " + ", ".join(f"{n} {name}" for name, n in counts.items()) + ".")

    # -- frameless window plumbing ------------------------------------------------

    def _apply_frame(self) -> None:
        m = 0 if self.isMaximized() or self.isFullScreen() else BORDER
        self.setContentsMargins(m, m, m, m)
        self.statusBar().setSizeGripEnabled(m > 0)
        self.title_bar.sync_state()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if sys.platform == "win32" and not self._styled_native:
            self._styled_native = True
            # Frameless windows lose the minimize box, so clicking the taskbar button
            # wouldn't minimize. Give the native window those styles back (no caption).
            import ctypes
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            hwnd, GWL_STYLE = int(self.winId()), -16
            style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | 0x00020000 | 0x00010000 | 0x00080000)

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self._apply_frame()
        elif e.type() == QEvent.ActivationChange:
            self.title_bar.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self.contentsMargins().left():
            p = QPainter(self)
            with device_space(p) as px:
                bevel(p, px.rect(self.rect()), WINDOW, px.t)

    def _edges_at(self, pos) -> Qt.Edges:
        if self.isMaximized():
            return Qt.Edges()
        grip = BORDER + 2
        edges = Qt.Edges()
        if pos.x() < grip:
            edges |= Qt.LeftEdge
        if pos.x() >= self.width() - grip:
            edges |= Qt.RightEdge
        if pos.y() < grip:
            edges |= Qt.TopEdge
        if pos.y() >= self.height() - grip:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        horiz = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        vert = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if horiz and vert:
            diag_main = edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge)
            self.setCursor(Qt.SizeFDiagCursor if diag_main else Qt.SizeBDiagCursor)
        elif horiz:
            self.setCursor(Qt.SizeHorCursor)
        elif vert:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        if e.button() == Qt.LeftButton and edges and self.windowHandle():
            self.windowHandle().startSystemResize(edges)
            return
        super().mousePressEvent(e)

    # -- engine update -------------------------------------------------------------

    def _update_engine(self) -> None:
        if self.downloader.active_count():
            QMessageBox.information(self, "Mediatheque", "Wait for current downloads to finish, then update.")
            return
        self.update_action.setEnabled(False)
        self.status_msg.setText("Updating engine…")
        self._status_timer.stop()
        self._updater.start()

    def _update_done(self, ok: bool, info: str) -> None:
        self.update_action.setEnabled(True)
        self.status_msg.setText(READY)
        if not ok:
            QMessageBox.warning(self, "Update failed", f"Couldn't update yt-dlp:\n\n{info}")
            return
        if info == YTDLP_VERSION:
            QMessageBox.information(self, "Mediatheque", f"Already up to date (yt-dlp {info}).")
            return
        self.engine_label.setText(f"yt-dlp {info} (restart to use)")
        if QMessageBox.question(self, "Engine updated",
                                f"Updated yt-dlp {YTDLP_VERSION} → {info}.\nRestart Mediatheque now?") \
                == QMessageBox.Yes:
            self._restart()

    def _restart(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        subprocess.Popen([sys.executable, "-m", "mediatheque"], cwd=root)
        self.close()

    # -- closing -------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        active = (self.downloader.active_count() + self.screenshots.active_count()
                  + self.script.active_count())
        if active and QMessageBox.question(
                self, "Mediatheque",
                f"{active} download(s) still in progress. Quit and cancel them?") != QMessageBox.Yes:
            event.ignore()
            return
        self.downloader.shutdown()
        self.script.save_all()  # every open script project, on disk before quitting
        self.script.shutdown()
        self.screenshots.shutdown()  # closes the capture browser
        self.settings.data["window"] = {
            "geometry": bytes(self.saveGeometry().toBase64()).decode(),
            "tool_key": TOOLS[self.stack.currentIndex()][0],
        }
        self.settings.save()
        event.accept()
