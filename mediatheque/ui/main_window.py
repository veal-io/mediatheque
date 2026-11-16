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

from .. import edition
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
        if not edition.EDITOR:
            action(tools, "Pack for Another &Editor…", self._pack_for_editor)
            action(tools, "Publish U&pdate…", self._publish_update)
        tools.addSeparator()
        action(tools, "Sig&n In to Sites…", lambda: (self.show_tool(self._tool_index("screenshots")),
                                                    self.screenshots.sign_in()))
        if edition.SUGGEST:
            action(tools, "Claude &API Key…", self._api_key)

        help_menu = self.menu.addMenu("&Help")
        action(help_menu, "Script &Tutorial…", self.start_tutorial)
        self.delete_practice_action = action(help_menu, "&Delete Practice Project", self._delete_practice)
        help_menu.aboutToShow.connect(self._help_menu_opening)
        help_menu.addSeparator()
        action(help_menu, "&Report a Problem…", self._report_problem)
        action(help_menu, "Check &Setup…", self._check_setup)
        from ..updates import can_update
        if can_update():  # an editor's copy: it updates itself from GitHub
            help_menu.addSeparator()
            action(help_menu, "Check for &Updates", lambda: self.check_for_updates(quiet=False))
            self.undo_update_action = action(help_menu, "U&ndo Last Update", self._undo_update)
        help_menu.addSeparator()
        action(help_menu, "&About Mediatheque", self._about)

    # -- the Script Tutorial ---------------------------------------------------------------

    def start_tutorial(self) -> None:
        from .tutorial import Tour
        if getattr(self, "_tour", None) is not None:
            return  # already running
        self._tour = Tour(self)
        self._tour.ended.connect(lambda: setattr(self, "_tour", None))
        self._tour.start()

    def offer_tutorial(self) -> None:
        """On the first launch, offer the tour once ("Not now" asks again next time)."""
        from ..script.project import list_projects
        st = self.settings.data.setdefault("tutorial", {})
        if st.get("offer") in ("never", "taken") or not (edition.EDITOR or not list_projects()):
            return
        box = QMessageBox(QMessageBox.Question, "Welcome to Mediatheque",
                          "New to Mediatheque? Take the 5-minute Script tour.\n\n"
                          "It uses a practice script, so nothing of yours is touched. "
                          "Help > Script Tutorial… runs it any time.", parent=self)
        start = box.addButton("Start", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        never = box.addButton("Don't ask again", QMessageBox.DestructiveRole)
        box.setDefaultButton(start)
        box.exec()
        st["offer"] = "taken" if box.clickedButton() is start else "never" if box.clickedButton() is never else "later"
        self.settings.save()
        if box.clickedButton() is start:
            self.start_tutorial()

    def _help_menu_opening(self) -> None:
        from .tutorial import practice_exists
        self.delete_practice_action.setEnabled(practice_exists() and getattr(self, "_tour", None) is None)
        if hasattr(self, "undo_update_action"):
            from ..updates import backup_version
            back = backup_version()
            self.undo_update_action.setEnabled(bool(back))
            self.undo_update_action.setText(f"U&ndo Last Update (back to {back})" if back else "U&ndo Last Update")

    # -- updates (an editor's copy) -----------------------------------------------------------

    def _busy(self) -> bool:
        return bool(self.downloader.active_count() + self.screenshots.active_count() + self.script.active_count())

    def startup_checks(self) -> None:
        """After the window is up: the first-launch tour offer, then (an editor's copy, once a day) updates."""
        from ..updates import can_update, due_for_check
        self.offer_tutorial()
        if can_update() and due_for_check(self.settings):
            QTimer.singleShot(3000, lambda: self.check_for_updates(quiet=True))

    def check_for_updates(self, quiet: bool = True) -> None:
        """Ask GitHub for a newer version. quiet: the daily check at launch, which says nothing unless
        there's an update."""
        from .. import __version__
        from ..updates import mark_checked, update_repo
        from .updates_ui import Checker
        if getattr(self, "_checker", None) is not None:
            return
        self._checker = Checker(self)

        def found(rel) -> None:
            self._checker = None
            mark_checked(self.settings)
            if rel is not None:
                self._offer_update(rel)
            elif not quiet:
                QMessageBox.information(self, "Check for Updates", f"You have the latest version ({__version__}).")

        def failed(message: str) -> None:
            self._checker = None
            if not quiet:
                QMessageBox.warning(self, "Check for Updates", message)

        self._checker.found.connect(found)
        self._checker.failed.connect(failed)
        self._checker.start(update_repo(self.settings))
        if not quiet:
            self.show_status("Checking for updates…")

    def _offer_update(self, rel) -> None:
        from .updates_ui import UpdateDialog, offer_restart
        dlg = UpdateDialog(rel, self._busy, self)
        if dlg.exec() == UpdateDialog.Accepted and dlg.installed:
            self.engine_label.setText(f"Mediatheque {dlg.installed} (restart to use)")
            offer_restart(self, f"Updated to version {dlg.installed}.")

    def _undo_update(self) -> None:
        from .. import __version__
        from ..updates import UpdateError, backup_version, undo
        from .updates_ui import offer_restart
        back = backup_version()
        if not back or QMessageBox.question(
                self, "Undo Last Update", f"Go back from version {__version__} to version {back}?\n\nYour projects "
                "and settings aren't touched.") != QMessageBox.Yes:
            return
        if self._busy():
            QMessageBox.information(self, "Undo Last Update", "Let the downloads and captures finish first.")
            return
        try:
            version = undo()
        except UpdateError as e:
            QMessageBox.warning(self, "Undo Last Update", str(e))
            return
        offer_restart(self, f"Went back to version {version}.")

    def _publish_update(self) -> None:
        from .publish_ui import PublishDialog
        self.script.save_all()
        PublishDialog(self.settings, self).exec()

    def _delete_practice(self) -> None:
        from .tutorial import delete_practice
        if QMessageBox.question(self, "Delete Practice Project",
                                "Delete the Script Tutorial's practice project and anything it downloaded? "
                                "Your own projects aren't touched.") != QMessageBox.Yes:
            return
        if delete_practice(self.script, self):
            self.show_status("Deleted the practice project.")

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

    def _pack_for_editor(self) -> None:
        """Tools > Pack for Another Editor: a clean copy with Suggest off and none of your data."""
        from PySide6.QtWidgets import QFileDialog
        from ..transfer import editor_zip_name, pack_editor
        from ..updates import update_repo
        start = os.path.join(os.path.expanduser("~"), "Desktop", editor_zip_name())
        path, _ = QFileDialog.getSaveFileName(self, "Pack Mediatheque for another editor", start, "Zip files (*.zip)")
        if not path:
            return
        repo = update_repo(self.settings)
        try:
            names = pack_editor(Path(path), repo)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Pack for Another Editor", f"Couldn't make the package:\n\n{e}")
            return
        show_in_explorer(path)
        updates = (f"It updates itself from github.com/{repo} when you publish an update (Tools > Publish Update…)."
                   if repo else "Updates aren't set up yet, so this copy won't update itself. Tools > Publish "
                   "Update… sets them up; pack again after that.")
        QMessageBox.information(
            self, "Pack for Another Editor",
            f"Packed {len(names)} files into\n{path}\n\nIt has Suggest switched off and none of your projects, "
            f"settings or patron lists. {updates}\n\nSend the editor the zip and say: unzip it into your home "
            "folder, then double-click Mediatheque.command. EDITOR_SETUP.md inside has the rest.")

    def _api_key(self) -> None:
        from .suggest_ui import ApiKeyDialog
        dlg = ApiKeyDialog(self)
        if dlg.exec() == ApiKeyDialog.Accepted:
            self.status_msg.setText("Removed the saved Claude API key." if dlg.removed else "Claude API key saved.")

    def _about(self) -> None:
        from .. import __version__
        QMessageBox.about(
            self, "About Mediatheque",
            f"<b>Mediatheque {__version__}</b>" + (" (editor edition)" if edition.EDITOR else "")
            + "<br>Video essay multi-tool: YouTube, TikTok, Instagram and Facebook downloader,"
            " article screenshots, script projects, and the patron Credit Generator.<br><br>"
            f"Engine: yt-dlp {YTDLP_VERSION}")

    def _report_problem(self) -> None:
        from .help_dialogs import ReportDialog
        ReportDialog(self).exec()

    def _check_setup(self) -> None:
        from .help_dialogs import CheckSetupDialog
        CheckSetupDialog(self).exec()

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
