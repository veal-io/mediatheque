"""Article Screenshots page: queue article links (one-off or a whole script) and step
through them in the capture window, snapping each shot by hand."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QMessageBox,
    QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..osutil import FILE_MANAGER, IS_MAC
from ..config import Settings, log_file
from ..screenshots.links import Capture, links_from_file, links_from_text, plan
from ..screenshots import signin
from ..screenshots.session import SessionWorker
from .record_bar import RecordBar
from ..osutil import open_path
from .widgets import FolderCombo, FolderStore, JobRow, show_in_explorer

SCRIPT_FILTER = "Scripts (*.pdf *.docx *.txt *.html *.htm);;All files (*)"
SCRIPT_TYPES = (".pdf", ".docx", ".txt", ".html", ".htm")
HOW_TO = ("A capture window opens and shows each article in turn. Aim the box and click to snap; "
          "Y keeps it and moves on, A keeps it and takes another, N retakes. "
          f"Wheel scrolls, {'Option+Wheel / Cmd+Wheel' if IS_MAC else 'Alt+Wheel / Ctrl+Wheel'} set the box's height / width, "
          "Shift fills the screen, B lets you use the page "
          "(sign in, close popups), C hides sticky headers, V records the screen, R resets the page, S skips, Esc finishes.")


def _short(url: str) -> str:
    u = urlparse(url)
    text = (u.hostname or "").removeprefix("www.") + u.path
    return text if len(text) <= 90 else text[:87] + "…"


class ShotRow(JobRow):
    """One article stop in the list."""

    def __init__(self, job_id: int, capture: Capture, folder: str):
        what = "Headline"
        if capture.quote:
            q = capture.quote.preview
            what = f"Quote: {q}" if q[:1] in "“\"'‘" else f"Quote: “{q}”"
        super().__init__(job_id, capture.url, what)
        self.capture = capture
        self.folder = folder
        self.title.setText(_short(capture.url))

    def set_showing(self) -> None:
        self.set_started()
        self.set_progress(-1, "In the capture window", self._quality)
        self.button.setEnabled(False)  # it's being handled in the window now

    def add_shot(self, path: str) -> None:
        self.files.append(path)
        n = len(self.files)
        self.title.setText(os.path.splitext(os.path.basename(path))[0])
        self.set_progress(-1, f"In the capture window · {n} saved", self._quality)

    def set_outcome(self, outcome: str) -> None:
        files = list(self.files)
        if outcome == "done":
            self.set_finished("done", "", files)
            if len(files) > 1:
                self.detail.setText(f"{len(files)} shots · {os.path.basename(files[-1])}")
        elif outcome.startswith("failed:"):
            self.set_finished("failed", outcome[7:], [])
        else:
            self.set_finished("cancelled", "", [])
            label = {"skipped": "Skipped", "stopped": "Not captured", "cancelled": "Cancelled"}.get(outcome, outcome)
            self.status.setText(label)


class ScreenshotsPage(QWidget):
    status = Signal(str)
    send_to_downloader = Signal(list)

    def __init__(self, settings: Settings, folders: FolderStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.state = settings.tab("screenshots")
        self.rows: dict[int, ShotRow] = {}
        self.videos: list[str] = []
        self._involved = False  # did the current capture session include this page's articles?
        self.setAcceptDrops(True)

        self.worker = SessionWorker(self)
        self.worker.stop_started.connect(self._on_started)
        self.worker.shot_saved.connect(self._on_saved)
        self.worker.stop_finished.connect(self._on_finished)
        self.worker.session_ended.connect(self._on_session_ended)
        self.worker.status.connect(self.status)
        # The recorder's control bar: shared by this page and the Script tool's captures.
        self.record_bar = RecordBar()
        self.record_bar.choice.connect(self.worker.record_choice)
        self.worker.record_state.connect(self.record_bar.set_state)

        # -- Capture form ---------------------------------------------------------
        self.link = QLineEdit()
        self.link.setPlaceholderText("Paste an article link (or several) for a late addition")
        self.link.returnPressed.connect(self.submit_links)
        add_btn = QPushButton("&Capture")
        add_btn.setObjectName("primary")  # drawn as the default button
        add_btn.clicked.connect(self.submit_links)

        self.script = QLineEdit()
        self.script.setReadOnly(True)
        self.script.setPlaceholderText("Import a script (PDF or Word) to go through all of its article links")
        import_btn = QPushButton("&Import Script…")
        import_btn.clicked.connect(self.browse_script)

        self.folder = FolderCombo(folders, self.state.get("folder"))
        self.folder.folder_changed.connect(lambda f: self._save("folder", f))
        self._save("folder", self.folder.current_folder())
        open_btn = QPushButton("&Open")
        open_btn.setToolTip(f"Open this folder in {FILE_MANAGER}")
        open_btn.clicked.connect(lambda: show_in_explorer(self.folder.current_folder()))

        how = QLabel(HOW_TO)
        how.setObjectName("muted")
        how.setWordWrap(True)

        self.message = QLabel()
        self.message.setObjectName("error")
        self.message.setWordWrap(True)
        self.message.hide()
        self._message_timer = QTimer(self, singleShot=True, interval=8000, timeout=self.message.hide)

        form = QGridLayout()
        form.setContentsMargins(12, 14, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(self._label("&Link:", self.link), 0, 0)
        form.addWidget(self.link, 0, 1)
        form.addWidget(add_btn, 0, 2)
        form.addWidget(self.message, 1, 1, 1, 2)
        form.addWidget(self._label("Scri&pt:", import_btn), 2, 0)
        form.addWidget(self.script, 2, 1)
        form.addWidget(import_btn, 2, 2)
        form.addWidget(self._label("&Save to:", self.folder), 3, 0)
        form.addWidget(self.folder, 3, 1)
        form.addWidget(open_btn, 3, 2)
        form.addWidget(how, 4, 1, 1, 2)
        form.setColumnStretch(1, 1)
        capture_box = QGroupBox("Capture Articles")
        capture_box.setLayout(form)

        # -- List ------------------------------------------------------------------
        self.list_host = QWidget()
        self.list_host.setBackgroundRole(QPalette.Base)
        self.list_host.setAutoFillBackground(True)
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.empty = QLabel("Import a script to step through every article it links to, or paste a link above.\n"
                            "Links that point at a quote open at that passage and flash it.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_host)

        self.videos_btn = QPushButton()
        self.videos_btn.setToolTip("Queue the script's TikTok, Instagram and YouTube links in the Downloader.")
        self.videos_btn.clicked.connect(self._send_videos)
        self.videos_btn.hide()
        self.resume_btn = QPushButton("Res&ume")
        self.resume_btn.setToolTip("Go back through the articles that were skipped or not reached.")
        self.resume_btn.clicked.connect(self.resume)
        self.resume_btn.hide()
        clear_btn = QPushButton("Clea&r Finished")
        clear_btn.clicked.connect(self.clear_finished)
        log_btn = QPushButton("View Lo&g")
        log_btn.clicked.connect(self.open_log)
        self.signin_btn = QPushButton()
        self.signin_btn.clicked.connect(self.sign_in)
        self._signin_timer = QTimer(self, interval=700, timeout=self._update_signin)
        self._signin_timer.start()
        self._update_signin()
        buttons = QHBoxLayout()
        buttons.addWidget(self.signin_btn)
        buttons.addWidget(self.videos_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.resume_btn)
        buttons.addWidget(clear_btn)
        buttons.addWidget(log_btn)

        list_col = QVBoxLayout()
        list_col.setContentsMargins(12, 14, 12, 12)
        list_col.setSpacing(10)
        list_col.addWidget(scroll, 1)
        list_col.addLayout(buttons)
        list_box = QGroupBox("Articles")
        list_box.setLayout(list_col)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(capture_box)
        root.addWidget(list_box, 1)

    # -- Sign In to Sites -------------------------------------------------------------

    def sign_in(self) -> None:
        """Open the capture browser as a normal Edge window to sign in to sites (or close it)."""
        if signin.is_open():
            signin.close_window()
            self._update_signin()
            return
        if self.worker.active:
            self.status.emit("Finish the capture session first (Esc in the capture window), then sign in.")
            return
        error = signin.open_window()
        if error:
            QMessageBox.warning(self, "Sign In to Sites", error)
            return
        self.status.emit("Sign in to the sites you need in the Edge window, then close it. "
                         "The Screenshotter will be signed in too.")
        self._update_signin()

    def _update_signin(self) -> None:
        if signin.is_open():
            self.signin_btn.setText("&Done Signing In")
            self.signin_btn.setToolTip("Close the Sign In window. Captures start once it's closed.")
            self.signin_btn.setEnabled(True)
        else:
            self.signin_btn.setText("Sig&n In to Sites…")
            self.signin_btn.setToolTip("Open the Screenshotter's browser as a normal Edge window to sign in to "
                                       "Fortune, the NYT and other paywalled sites. Captures stay signed in.")
            self.signin_btn.setEnabled(not self.worker.active)

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    def _label(text: str, buddy: QWidget) -> QLabel:
        label = QLabel(text)
        label.setBuddy(buddy)
        return label

    def _save(self, key: str, value) -> None:
        self.state[key] = value
        self.settings.save()

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.show()
        self._message_timer.start()

    def active_count(self) -> int:
        return sum(not r.finished for r in self.rows.values())

    def shutdown(self) -> None:
        self.record_bar.hide()
        self.worker.shutdown()

    def open_log(self) -> None:
        path = log_file("screenshots")
        if path.exists():
            open_path(str(path))
        else:
            self.show_message("No log yet. It's created on the first capture.")

    # -- adding articles -------------------------------------------------------------

    def browse_script(self) -> None:
        start = self.state.get("script_dir") or os.path.expanduser("~/Documents")
        path, _ = QFileDialog.getOpenFileName(self, "Import a script", start, SCRIPT_FILTER)
        if path:
            self.import_script(path)

    def import_script(self, path: str) -> None:
        try:
            links = links_from_file(path)
        except Exception as e:
            self.show_message(f"Couldn't read links from that file: {e}")
            return
        self._save("script_dir", os.path.dirname(path))
        self.script.setText(os.path.normpath(path))
        if not links:
            self.show_message("No links found in that file. Are the links embedded (clickable)?")
            return
        added, videos = self._add(links)
        parts = [f"{added} article stop{'s' if added != 1 else ''}"]
        if videos:
            parts.append(f"{videos} video link{'s' if videos != 1 else ''} set aside for the Downloader")
        self.status.emit(f"{os.path.basename(path)}: " + ", ".join(parts) + ".")

    def submit_links(self) -> None:
        links = links_from_text(self.link.text())
        if not links:
            self.link.setFocus()
            self.show_message("Paste an article link first.")
            return
        added, videos = self._add(links)
        self.link.clear()
        self.message.hide()
        if videos and not added:
            self.status.emit("That's a video link: use the button below to send it to the Downloader.")

    def _add(self, links: list[str]) -> tuple[int, int]:
        captures, videos = plan(links)
        folder = self.folder.current_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.show_message(f"Can't use that folder: {e.strerror or e}")
            return 0, 0
        for cap in captures:
            self._enqueue(cap, folder)
        new_videos = [v for v in videos if v not in self.videos]
        self.videos += new_videos
        self._update_videos_btn()
        return len(captures), len(new_videos)

    def _enqueue(self, cap: Capture, folder: str, at: int | None = None) -> ShotRow:
        stop = self.worker.add(cap.url, cap.quote, folder)
        row = ShotRow(stop.id, cap, folder)
        row.cancel_clicked.connect(self._cancel)
        row.retry_clicked.connect(self._retry)
        self.rows[stop.id] = row
        self.empty.hide()
        self.list_layout.insertWidget(self.list_layout.count() - 2 if at is None else at, row)
        return row

    def _cancel(self, stop_id: int) -> None:
        self.worker.cancel(stop_id)
        row = self.rows.get(stop_id)
        if row and row.state == "queued":
            row.set_outcome("cancelled")

    def _retry(self, stop_id: int) -> None:
        row = self.rows.pop(stop_id, None)
        if not row:
            return
        index = self.list_layout.indexOf(row)
        self.list_layout.removeWidget(row)
        row.deleteLater()
        self._enqueue(row.capture, row.folder, at=index)

    def resume(self) -> None:
        """Re-queue every article that was skipped or not reached, keeping list order."""
        for stop_id in [j for j, r in self.rows.items() if r.state == "cancelled"]:
            self._retry(stop_id)
        self.resume_btn.hide()

    def clear_finished(self) -> None:
        for stop_id in [j for j, r in self.rows.items() if r.finished]:
            row = self.rows.pop(stop_id)
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self.empty.setVisible(not self.rows)
        self.resume_btn.hide()

    def _update_videos_btn(self) -> None:
        n = len(self.videos)
        self.videos_btn.setText(f"Send {n} Video Lin&k{'s' if n != 1 else ''} to Downloader")
        self.videos_btn.setVisible(n > 0)

    def _send_videos(self) -> None:
        self.send_to_downloader.emit(list(self.videos))
        self.videos = []
        self._update_videos_btn()

    # -- session signals -------------------------------------------------------------

    def _on_started(self, stop_id: int) -> None:
        if row := self.rows.get(stop_id):
            self._involved = True
            row.set_showing()

    def _on_saved(self, stop_id: int, path: str) -> None:
        if row := self.rows.get(stop_id):
            row.add_shot(path)

    def _on_finished(self, stop_id: int, outcome: str) -> None:
        row = self.rows.get(stop_id)
        if row and not (row.state == "cancelled" and outcome == "cancelled"):
            row.set_outcome(outcome)

    def _on_session_ended(self, saved: int) -> None:
        if not self._involved:
            return  # a session run by another tool (the Script page)
        self._involved = False
        left = sum(r.state == "cancelled" for r in self.rows.values())
        self.resume_btn.setVisible(left > 0)
        msg = f"Capture window closed: {saved} screenshot{'s' if saved != 1 else ''} saved."
        if left:
            msg += f" {left} article{'s' if left != 1 else ''} skipped or not reached (Resume to go back)."
        self.status.emit(msg)

    # Drag a script (or a text file of links) onto the page.
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(u.toLocalFile().lower().endswith(SCRIPT_TYPES) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(SCRIPT_TYPES):
                self.import_script(u.toLocalFile())
