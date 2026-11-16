"""The Downloader: one page for YouTube, TikTok, Instagram, Facebook, Reddit and X links.

Each link goes to its own platform's worker (they keep their own naming, conversion and
log), so a YouTube and a TikTok download run side by side. Folder, quality and cookies are
shared by every platform, and the Script tool's video downloads use them too.
"""
from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..config import Settings, log_file
from ..engine import DownloadWorker, app_browser_cookies, app_browser_signed_in
from ..platforms import PLATFORMS, QUALITIES, Platform, format_for, normalize_url, platform_for, quality_from_preset
from ..screenshots import signin
from ..osutil import FILE_MANAGER, open_path
from .widgets import FolderCombo, FolderStore, JobRow, show_in_explorer

# Chrome, Edge, Brave and other Chromium browsers encrypt their cookies on Windows so that
# only the browser itself can read them, so they're not offered. Chrome users can export
# a cookies.txt file instead.
COOKIE_NONE, COOKIE_FIREFOX, COOKIE_FILE = "None", "Firefox", "cookies.txt file"
COOKIE_APP = "Mediatheque Browser"
COOKIE_CHOICES = [COOKIE_NONE, COOKIE_FIREFOX, COOKIE_APP, COOKIE_FILE]
COOKIE_TOOLTIP = (
    "Most links download fine with None.\n"
    "Sign-in cookies are only needed for age-restricted, private or members-only videos,\n"
    "or when a platform starts demanding a login (\"confirm you're not a bot\").\n\n"
    "Firefox: reads your Firefox sign-ins directly. Sign in to YouTube (or the platform) in\n"
    "Firefox first. This is the one that works for YouTube's bot check.\n"
    "Mediatheque Browser: the sign-ins you make in Tools > Sign In to Sites.\n"
    "cookies.txt file: export one with the 'Get cookies.txt LOCALLY' extension.\n\n"
    "If cookies can't be read, the download carries on without them."
)
QUALITY_TOOLTIP = (
    "4K, 1440p and 720p are YouTube sizes. TikTok uses its H.264 version for the H.264 choices,\n"
    "and Instagram, Facebook, Reddit and X always download their best MP4. YouTube above 1080p and some\n"
    "Instagram videos are converted to H.264 automatically so Premiere can open them."
)
# Where Mediatheque Browser's sign-in is checked, per platform.
SIGN_IN = {
    "youtube": (".youtube.com", ("LOGIN_INFO", "SAPISID", "__Secure-3PAPISID"), "https://www.youtube.com/"),
    "instagram": (".instagram.com", ("sessionid",), "https://www.instagram.com/accounts/login/"),
    "tiktok": (".tiktok.com", ("sessionid", "sid_tt"), "https://www.tiktok.com/login"),
    "facebook": (".facebook.com", ("c_user",), "https://www.facebook.com/"),
}


def downloader_state(settings: Settings) -> dict:
    """The Downloader's settings, brought over from the old per-platform tabs the first time."""
    st = settings.tab("downloader")
    if "quality" not in st:
        old = [settings.data.get("tabs", {}).get(p.key, {}) for p in PLATFORMS]  # YouTube's first
        st["quality"] = quality_from_preset(next((o["quality"] for o in old if o.get("quality")), ""))
        cookies = [o.get("cookies") for o in old if o.get("cookies") in COOKIE_CHOICES]
        st["cookies"] = next((c for c in cookies if c != COOKIE_NONE), COOKIE_NONE)
        if file := next((o["cookies_file"] for o in old if o.get("cookies_file")), ""):
            st["cookies_file"] = file
        if folder := next((o["folder"] for o in old if o.get("folder")), ""):
            st.setdefault("folder", folder)
        settings.save()
    return st


def cookie_options(state: dict) -> dict | None:
    """yt-dlp options for the Downloader's Cookies choice."""
    choice = state.get("cookies")
    if choice == COOKIE_APP:
        return app_browser_cookies()
    if choice == COOKIE_FIREFOX:
        return {"cookiesfrombrowser": ("firefox",)}
    if choice == COOKIE_FILE and (path := state.get("cookies_file")):
        return {"cookiefile": path}
    return None


class DownloaderPage(QWidget):
    status = Signal(str)  # a short message for the status bar

    def __init__(self, settings: Settings, folders: FolderStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.state = downloader_state(settings)
        self.rows: dict[tuple[str, int], JobRow] = {}      # (platform key, job id) -> row
        self.job_args: dict[tuple[str, int], tuple] = {}

        self.workers: dict[str, DownloadWorker] = {}
        for p in PLATFORMS:
            w = DownloadWorker(p, self)
            w.job_started.connect(lambda jid, k=p.key: (r := self.rows.get((k, jid))) and r.set_started())
            w.job_title.connect(lambda jid, t, k=p.key: (r := self.rows.get((k, jid))) and r.set_title(t))
            w.job_source.connect(lambda jid, s, k=p.key: (r := self.rows.get((k, jid))) and setattr(r, "source", s))
            w.job_progress.connect(lambda jid, pct, s, d, k=p.key:
                                   (r := self.rows.get((k, jid))) and r.set_progress(pct, s, d))
            w.job_finished.connect(lambda jid, o, m, f, k=p.key: self._on_finished(k, jid, o, m, f))
            self.workers[p.key] = w

        # -- Link -------------------------------------------------------------------
        self.link = QLineEdit()
        self.link.setPlaceholderText("Paste YouTube, TikTok, Instagram, Facebook, Reddit or X links (several at once is fine)")
        self.link.returnPressed.connect(self.submit)
        self.download_btn = QPushButton("&Download")
        self.download_btn.setObjectName("primary")  # drawn as the default button
        self.download_btn.clicked.connect(self.submit)

        # -- Folder -----------------------------------------------------------------
        self.folder = FolderCombo(folders, self.state.get("folder"))
        self.folder.folder_changed.connect(lambda f: self._save("folder", f))
        self._save("folder", self.folder.current_folder())
        open_btn = QPushButton("&Open")
        open_btn.setToolTip(f"Open this folder in {FILE_MANAGER}")
        open_btn.clicked.connect(lambda: show_in_explorer(self.folder.current_folder()))

        # -- Quality + cookies ------------------------------------------------------
        self.quality = QComboBox()
        self.quality.addItems(QUALITIES)
        self.quality.setToolTip(QUALITY_TOOLTIP)
        self.quality.setCurrentText(self.state.get("quality") or QUALITIES[0])
        self.quality.currentTextChanged.connect(lambda t: self._save("quality", t))

        self.cookies = QComboBox()
        self.cookies.addItems(COOKIE_CHOICES)
        if (c := self.state.get("cookies")) in COOKIE_CHOICES:
            self.cookies.setCurrentText(c)
        self.cookies.activated.connect(self._on_cookies_chosen)
        self._update_cookies_tooltip()

        cookies_label = self._field_label("Coo&kies:", self.cookies)
        cookies_label.setToolTip(COOKIE_TOOLTIP)
        options = QHBoxLayout()
        options.setSpacing(8)
        options.addWidget(self.quality, 3)
        options.addSpacing(12)
        options.addWidget(cookies_label)
        options.addWidget(self.cookies, 1)

        self.message = QLabel()
        self.message.setObjectName("error")
        self.message.setWordWrap(True)
        self.message.hide()
        self._message_timer = QTimer(self, singleShot=True, interval=8000, timeout=self.message.hide)

        form = QGridLayout()
        form.setContentsMargins(12, 14, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(self._field_label("&Link:", self.link), 0, 0)
        form.addWidget(self.link, 0, 1)
        form.addWidget(self.download_btn, 0, 2)
        form.addWidget(self.message, 1, 1, 1, 2)
        form.addWidget(self._field_label("&Save to:", self.folder), 2, 0)
        form.addWidget(self.folder, 2, 1)
        form.addWidget(open_btn, 2, 2)
        form.addWidget(self._field_label("&Quality:", self.quality), 3, 0)
        form.addLayout(options, 3, 1)
        form.setColumnStretch(1, 1)
        download_box = QGroupBox("Download Videos")
        download_box.setLayout(form)

        # -- Downloads list ---------------------------------------------------------
        self.list_host = QWidget()
        self.list_host.setBackgroundRole(QPalette.Base)
        self.list_host.setAutoFillBackground(True)
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.empty = QLabel("Paste a YouTube, TikTok, Instagram, Facebook, Reddit or X link above and press Enter.\n"
                            "You can paste several links at once, from any mix of platforms.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()  # StyledPanel + Sunken: drawn as a classic white list box
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_host)

        names_btn = QPushButton("Copy Finished &Names")
        names_btn.setToolTip("Copy the names of every finished download, one per line (TikTok, Instagram and X "
                             "as @handles). Each download's own Copy Source button copies its credit.")
        names_btn.clicked.connect(self.copy_finished_names)
        clear_btn = QPushButton("Clea&r Finished")
        clear_btn.clicked.connect(self.clear_finished)
        log_btn = QPushButton("View Lo&g")
        log_btn.setToolTip("Open a platform's detailed download log. Useful when something breaks.")
        log_menu = QMenu(log_btn)
        for p in PLATFORMS:
            log_menu.addAction(f"{p.name} Log", lambda k=p.key: self.open_log(k))
        log_btn.setMenu(log_menu)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (names_btn, clear_btn, log_btn):
            buttons.addWidget(b)

        list_col = QVBoxLayout()
        list_col.setContentsMargins(12, 14, 12, 12)
        list_col.setSpacing(10)
        list_col.addWidget(scroll, 1)
        list_col.addLayout(buttons)
        downloads_box = QGroupBox("Downloads")
        downloads_box.setLayout(list_col)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(download_box)
        root.addWidget(downloads_box, 1)

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _field_label(text: str, buddy: QWidget) -> QLabel:
        label = QLabel(text)
        label.setBuddy(buddy)  # Alt + underlined letter jumps to the field
        return label

    def _save(self, key: str, value: str) -> None:
        self.state[key] = value
        self.settings.save()

    def _on_cookies_chosen(self, _index: int) -> None:
        choice = self.cookies.currentText()
        if choice == COOKIE_FILE:
            start = self.state.get("cookies_file") or os.path.expanduser("~/Downloads")
            path, _ = QFileDialog.getOpenFileName(self, "Choose an exported cookies.txt", start,
                                                  "Cookie files (*.txt);;All files (*)")
            if path:
                self._save("cookies_file", os.path.normpath(path))
            elif not self.state.get("cookies_file"):
                choice = COOKIE_NONE
                self.cookies.setCurrentText(choice)
        self._save("cookies", choice)
        self._update_cookies_tooltip()
        if choice == COOKIE_APP:
            domain, names, url = SIGN_IN["youtube"]
            if app_browser_signed_in(domain, names) is False:
                if QMessageBox.question(
                        self, "Mediatheque Browser",
                        "Mediatheque's browser isn't signed in to YouTube yet.\n\n"
                        "Open it now to sign in? Close the window when you're done, then download.") == QMessageBox.Yes:
                    if err := signin.open_window(url):
                        QMessageBox.warning(self, "Sign In to Sites", err)

    def _update_cookies_tooltip(self) -> None:
        tip = COOKIE_TOOLTIP
        if self.cookies.currentText() == COOKIE_FILE and self.state.get("cookies_file"):
            tip = f"Using: {self.state['cookies_file']}\nPick this option again to choose a different file.\n\n" + tip
        self.cookies.setToolTip(tip)

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.show()
        self._message_timer.start()

    def active_count(self) -> int:
        return sum(not r.finished for r in self.rows.values())

    def shutdown(self) -> None:
        for w in self.workers.values():
            w.cancel_all()

    def open_log(self, key: str = "youtube") -> None:
        path = log_file(key)
        if path.exists():
            open_path(str(path))
        else:
            self.show_message("No log for that platform yet. It's created on its first download.")

    # -- submitting ------------------------------------------------------------------

    def submit(self) -> None:
        text = self.link.text().strip()
        if not text:
            self.link.setFocus()
            self.show_message("Paste a YouTube, TikTok, Instagram, Facebook, Reddit or X link first.")
            return
        urls = [normalize_url(u) for u in text.split()]
        videos = [(u, p) for u in urls if (p := platform_for(u))]
        others = [u for u in urls if not platform_for(u)]
        if not videos:
            self.show_message("That isn't a YouTube, TikTok, Instagram, Facebook, Reddit or X video link. "
                              "Article links go to Screenshots.")
            return
        if not self._ready_folder():
            return
        for url, p in videos:
            self._enqueue(p, url)
        if others:
            self.link.setText(" ".join(others))
            n = len(others)
            self.show_message(f"Downloading {len(videos)}. Left {n} link{'s' if n != 1 else ''} in the box: "
                              f"{'they aren' if n != 1 else 'it isn'}'t a video link (articles go to Screenshots).")
        else:
            self.link.clear()
            self.message.hide()

    def queue_urls(self, urls: list[str]) -> dict[str, int]:
        """Queue links handed over from another tool. Returns how many per platform."""
        counts: dict[str, int] = {}
        if not self._ready_folder():
            return counts
        for url in urls:
            if p := platform_for(url):
                self._enqueue(p, url)
                counts[p.name] = counts.get(p.name, 0) + 1
        return counts

    def _ready_folder(self) -> bool:
        try:
            os.makedirs(self.folder.current_folder(), exist_ok=True)
            return True
        except OSError as e:
            self.show_message(f"Can't use that folder: {e.strerror or e}")
            return False

    def _enqueue(self, platform: Platform, url: str, folder: str = "", quality: str = "",
                 cookies: dict | None | bool = False) -> None:
        folder = folder or self.folder.current_folder()
        quality = quality or self.quality.currentText()
        if cookies is False:
            cookies = cookie_options(self.state)
        preset = format_for(platform, quality)
        job = self.workers[platform.key].submit(url, folder, preset, cookies)
        key = (platform.key, job.id)
        row = JobRow(job.id, url, f"{platform.name} · {preset}")
        row.cancel_clicked.connect(lambda _jid, k=key: self._cancel(k))
        row.retry_clicked.connect(lambda _jid, k=key: self._retry(k))
        row.platform = platform
        row.offers_source = True
        row.source_clicked.connect(lambda _jid, k=key: self.copy_source(k))
        self.rows[key] = row
        self.job_args[key] = (platform, url, folder, quality)
        self.empty.hide()
        self.list_layout.insertWidget(0, row)  # newest on top

    def _cancel(self, key: tuple[str, int]) -> None:
        self.workers[key[0]].cancel(key[1])
        row = self.rows.get(key)
        if row and row.state == "queued":
            row.set_finished("cancelled", "Cancelled", [])

    def _retry(self, key: tuple[str, int]) -> None:
        args = self.job_args.pop(key, None)
        self._remove_row(key)
        if args:
            self._enqueue(*args)  # with the current Cookies choice, in case that was the fix

    def _remove_row(self, key: tuple[str, int]) -> None:
        if row := self.rows.pop(key, None):
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self.empty.setVisible(not self.rows)

    def _on_finished(self, pkey: str, job_id: int, outcome: str, message: str, files: list) -> None:
        if row := self.rows.get((pkey, job_id)):
            if row.state == "cancelled" and outcome == "cancelled":
                return
            row.set_finished(outcome, message, files)

    # -- copying -----------------------------------------------------------------------

    @staticmethod
    def _handle(path: str) -> str:
        stem = os.path.splitext(os.path.basename(path))[0]
        return "@" + re.sub(r" \(\d+\)$", "", stem).lstrip("@")  # "@user (2)" -> "@user"

    def finished_names(self) -> list[str]:
        """Names of every finished download, top to bottom as listed, each once. TikTok,
        Instagram and X files are named after the creator, so those come out as @handles."""
        names: list[str] = []
        for i in range(self.list_layout.count()):
            row = self.list_layout.itemAt(i).widget()
            if not isinstance(row, JobRow) or row.state != "done":
                continue
            handles = getattr(row, "platform", None) is not None and bool(row.platform.rename)
            for path in row.files:
                name = self._handle(path) if handles else os.path.splitext(os.path.basename(path))[0]
                if name not in names:
                    names.append(name)
        return names

    def copy_finished_names(self) -> None:
        names = self.finished_names()
        if not names:
            self.status.emit("No finished downloads to copy names from.")
            return
        QGuiApplication.clipboard().setText("\n".join(names))
        self.status.emit(f"Copied {len(names)} name{'s' if len(names) != 1 else ''} to the clipboard.")

    def copy_source(self, key: tuple[str, int]) -> None:
        """One download's on-screen credit: "Title | Channel" (YouTube), "u/name · r/sub"
        (Reddit), the poster (Facebook), or the @handle (TikTok, Instagram, X)."""
        row = self.rows.get(key)
        if not row:
            return
        credit = row.source or (self._handle(row.files[0]) if row.platform.rename and row.files else "")
        if not credit:
            self.status.emit("No source credit for that download.")
            return
        QGuiApplication.clipboard().setText(credit)
        self.status.emit(f"Copied source: {credit}")

    def clear_finished(self) -> None:
        for key in [k for k, r in self.rows.items() if r.finished]:
            self.job_args.pop(key, None)
            self._remove_row(key)
