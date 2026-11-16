"""Link previews for the Attach / Skip prompt: a thumbnail and title for videos, a small
snapshot of the page for articles. Fetched on a background thread and cached."""
from __future__ import annotations

import logging
import queue
import threading
import urllib.request

from PySide6.QtCore import QObject, Signal

from ..config import data_dir
from ..platforms import video_platform_in_script


class _Quiet:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class PreviewService(QObject):
    ready = Signal(str, object, str)  # url, image bytes (or None), title

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.log = logging.getLogger("mediatheque.screenshots")
        self._cache: dict[str, tuple[bytes | None, str]] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._queued: set[str] = set()
        self._thread = threading.Thread(target=self._run, name="previews", daemon=True)
        self._thread.start()

    def cached(self, url: str) -> tuple[bytes | None, str] | None:
        return self._cache.get(url)

    def request(self, url: str, urgent: bool = False) -> None:
        if url in self._cache:
            data, title = self._cache[url]
            self.ready.emit(url, data, title)
            return
        if url in self._queued and not urgent:
            return
        self._queued.add(url)
        self._queue.put(url)

    def shutdown(self) -> None:
        self._queue.put(None)

    # -- background thread ---------------------------------------------------------

    def _run(self) -> None:
        pw = ctx = None
        try:
            while True:
                url = self._queue.get()
                if url is None:
                    return
                if url in self._cache:
                    continue
                try:
                    if video_platform_in_script(url):
                        result = self._video(url)
                    else:
                        if ctx is None:
                            pw, ctx = self._browser()
                        result = self._article(ctx, url)
                except Exception as e:
                    self.log.info("Preview failed for %s: %s", url, e)
                    result = (None, "")
                self._cache[url] = result
                self._queued.discard(url)
                self.ready.emit(url, *result)
        finally:
            for c in (ctx, pw):
                try:
                    c and (c.stop() if c is pw else c.close())
                except Exception:
                    pass

    @staticmethod
    def _video(url: str) -> tuple[bytes | None, str]:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True,
                               "logger": _Quiet(), "socket_timeout": 15}) as ydl:
            info = ydl.extract_info(url, download=False, process=False) or {}
        title = info.get("title") or info.get("description") or ""
        uploader = info.get("uploader") or info.get("channel") or ""
        if uploader:
            title = f"{uploader}: {title}" if title else uploader
        thumb = info.get("thumbnail") or next(
            (t.get("url") for t in reversed(info.get("thumbnails") or []) if t.get("url")), None)
        data = None
        if thumb:
            req = urllib.request.Request(thumb, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
        return data, title[:200]

    @staticmethod
    def _browser():
        from playwright.sync_api import sync_playwright
        from ..screenshots.signin import browser_channel
        from ..screenshots.naming import own_browser
        # Separate from the capture window's profile (and, on a Mac, from Google Chrome's).
        profile = data_dir() / ("preview-profile-own" if own_browser() else "preview-profile")
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            str(profile), channel=browser_channel(), headless=True, viewport={"width": 1280, "height": 800},
            color_scheme="light", args=["--disable-blink-features=AutomationControlled"])
        return pw, ctx

    @staticmethod
    def _article(ctx, url: str) -> tuple[bytes | None, str]:
        from ..screenshots.session import CONSENT_JS
        page = ctx.new_page()
        try:
            page.goto(url.split("#")[0], wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(1500)
            page.evaluate(CONSENT_JS)
            title = page.title()
            data = page.screenshot(type="jpeg", quality=70)
            return data, title[:200]
        finally:
            page.close()
