"""Download engine: one queue + one background thread per platform tab.

Tabs never share a worker, so a stuck or failing download on one platform
can't block the others.
"""
from __future__ import annotations

import glob
import itertools
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

import yt_dlp
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError, PostProcessingError, sanitize_filename
from yt_dlp.version import __version__ as YTDLP_VERSION

from . import convert
from .platforms import Platform


def ffmpeg_path() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def deno_path() -> str | None:
    # YouTube needs a JavaScript runtime to unlock its full format list.
    try:
        import deno
        return deno.find_deno_bin()
    except Exception:
        return None


@dataclass
class Job:
    id: int
    url: str
    folder: str
    format_label: str
    cookies: dict | None  # yt-dlp cookie options ({"cookiefile": …} or {"cookiesfrombrowser": …})
    cancel_event: threading.Event = field(default_factory=threading.Event)
    files: list[str] = field(default_factory=list)
    tmpfiles: set[str] = field(default_factory=set)
    prefix: str = ""  # added to the final file name, e.g. "7_" for script highlight 7
    notes: list[str] = field(default_factory=list)  # shown on the finished row


class _YdlLogger:
    def __init__(self, log: logging.Logger):
        self.log = log

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            self.log.debug(msg)
        else:
            self.log.info(msg)

    def info(self, msg: str) -> None:
        self.log.info(msg)

    def warning(self, msg: str) -> None:
        self.log.warning(msg)

    def error(self, msg: str) -> None:
        self.log.error(msg)


_PP_LABELS = {
    "Merger": "Merging video + audio…",
    "ExtractAudio": "Converting audio…",
    "MoveFiles": "Finishing…",
}


def _fmt_bytes(n: float | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def _fmt_eta(s: float | None) -> str:
    if s is None:
        return ""
    s = int(s)
    return f"{s // 60}:{s % 60:02d} left" if s < 3600 else f"{s // 3600}h {s % 3600 // 60}m left"


def _stream_kind(info: dict) -> str:
    v, a = info.get("vcodec"), info.get("acodec")
    if v and v != "none" and a == "none":
        return "Video"
    if v == "none" and a and a != "none":
        return "Audio"
    return ""


class _PremierePP(PostProcessor):
    """Re-encodes VP9/AV1 video to H.264 so Premiere Pro can import it."""

    def __init__(self, ydl: yt_dlp.YoutubeDL, ffmpeg: str, job: Job, worker: DownloadWorker):
        super().__init__(ydl)
        self.ffmpeg, self.job, self.worker = ffmpeg, job, worker

    def run(self, info: dict):
        path = info.get("filepath")
        if not path or not os.path.exists(path):
            return [], info
        job, worker = self.job, self.worker
        try:
            new = convert.to_h264(
                self.ffmpeg, path, worker.log,
                on_progress=lambda pct, status: worker.job_progress.emit(job.id, pct, status, ""),
                cancelled=job.cancel_event.is_set)
        except convert.ConversionCancelled:
            raise DownloadCancelled("Cancelled by user")
        except RuntimeError as e:
            raise PostProcessingError(str(e))
        if new:
            info["filepath"], info["ext"] = new, "mp4"
            job.notes.append("converted to H.264 for Premiere")
        return [], info


class _RenamePP(PostProcessor):
    """Gives the finished file the platform's final name, numbering duplicates."""

    def __init__(self, ydl: yt_dlp.YoutubeDL, template: str | None, prefix: str = ""):
        super().__init__(ydl)
        self.template = template
        self.prefix = prefix

    def run(self, info: dict):
        old = info.get("filepath")
        if not old or not os.path.exists(old):
            return [], info
        if self.template:
            stem = sanitize_filename(self._downloader.evaluate_outtmpl(self.template, info)).strip()
            if stem.strip("@") in ("", "NA"):  # yt-dlp fills missing fields with "NA"
                self.report_warning(f"Couldn't work out a name from {self.template!r}; keeping its name")
                stem = os.path.splitext(os.path.basename(old))[0]
        else:
            stem = os.path.splitext(os.path.basename(old))[0]
        stem = self.prefix + stem
        folder, ext = os.path.split(old)[0], os.path.splitext(old)[1]
        if os.path.normcase(os.path.join(folder, stem + ext)) == os.path.normcase(old):
            return [], info
        new, n = os.path.join(folder, stem + ext), 2
        while os.path.exists(new):
            new, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
        os.rename(old, new)
        self.to_screen(f"Renamed to {os.path.basename(new)}")
        info["filepath"] = new
        return [], info


def app_browser_cookies() -> dict:
    """yt-dlp options to use the sign-ins in Mediatheque's own browser (Sign In to Sites,
    shared with the Screenshotter). Edge can't be read while that browser is open; the
    download then carries on without cookies, as for any unreadable cookies."""
    from .screenshots.naming import profile_dir
    from .screenshots.signin import browser_channel
    browser = "edge" if browser_channel() == "msedge" else "chrome"  # a Mac uses Chrome or its own Chromium
    return {"cookiesfrombrowser": (browser, os.path.join(profile_dir(), "Default"))}


def app_browser_signed_in(domain: str, names: tuple[str, ...]) -> bool | None:
    """Is Mediatheque's browser signed in to a site (by its sign-in cookie names)?
    None if it can't be told right now (the browser is open)."""
    from yt_dlp.cookies import extract_cookies_from_browser
    try:
        jar = extract_cookies_from_browser(*app_browser_cookies()["cookiesfrombrowser"])
    except Exception:
        return None
    return any(c.domain.endswith(domain) and c.name in names for c in jar)


# What yt-dlp says about a post with nothing to download: text, a picture, a link elsewhere.
_NO_VIDEO = ("no video could be found", "no media found", "is not a video", "unsupported url")
_NOT_VIDEO_EXTS = {"jpg", "jpeg", "png", "webp", "mhtml", "html"}


def _has_video(info: dict | None) -> bool:
    if not info:
        return False
    if info.get("entries") is not None:
        return any(_has_video(e) for e in info["entries"])
    formats = info.get("formats") or ([info] if info.get("url") else [])
    return any(f.get("vcodec") != "none" and f.get("ext") not in _NOT_VIDEO_EXTS for f in formats)


def post_kind(url: str, cookies: dict | None = None) -> tuple[str, str]:
    """Does this post hold a video? ("video" | "article" | "unsure", why it's unsure)."""
    from .platforms import platform_for
    try:
        return ("video" if _has_video(fetch_info(url, cookies)) else "article"), ""
    except Exception as e:  # noqa: BLE001 (every failure is a judgement here)
        message = str(e)
        if cookies and _is_cookie_error(message):
            return post_kind(url, None)
        if any(s in message.lower() for s in _NO_VIDEO):
            return "article", ""
        p = platform_for(url)
        return "unsure", p.explain(message) if p else message.splitlines()[0][:160]


_COOKIE_ERRORS = ("cookie database", "cookies database", "decrypt with dpapi", "cookie file",
                  "cookiefile", "netscape format", "could not find firefox")


def _is_cookie_error(message: str) -> bool:
    low = message.lower()
    return any(s in low for s in _COOKIE_ERRORS)


def _collect_files(info: dict | None) -> list[str]:
    if not info:
        return []
    out: list[str] = []
    for entry in info.get("entries") or []:
        out += _collect_files(entry)
    for rd in info.get("requested_downloads") or []:
        if fp := rd.get("filepath") or rd.get("_filename"):
            out.append(fp)
    return out


OEMBED = {  # public endpoints that give a video's title and author: no sign-in, no bot check
    "youtube": "https://www.youtube.com/oembed?format=json&url=",
    "tiktok": "https://www.tiktok.com/oembed?url=",
}


def oembed(platform_key: str, url: str) -> dict | None:
    """{"title", "author_name", …} for a video, or None if the platform has no oEmbed or it failed."""
    import json
    import urllib.parse
    import urllib.request
    base = OEMBED.get(platform_key)
    if not base:
        return None
    req = urllib.request.Request(base + urllib.parse.quote(url, safe=""), headers={"User-Agent": "Mediatheque"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.info("oEmbed lookup failed for %s: %s", url, e)
        return None


class _QuietLog:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def fetch_info(url: str, cookies: dict | None = None) -> dict:
    """A video's details without downloading it (for a source credit after the fact)."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "logger": _QuietLog(), **(cookies or {})}
    if dn := deno_path():
        opts["js_runtimes"] = {"deno": {"path": dn}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        from .platforms import platform_for
        if (p := platform_for(url)) and p.expand:
            url = p.expand(url)
        return ydl.extract_info(url, download=False) or {}


class DownloadWorker(QObject):
    job_started = Signal(int)
    job_title = Signal(int, str)
    job_source = Signal(int, str)  # on-screen source credit, for platforms that define one
    job_progress = Signal(int, float, str, str)   # id, percent (-1 = busy), status, detail
    job_finished = Signal(int, str, str, object)  # id, "done"|"failed"|"cancelled", message, files

    def __init__(self, platform: Platform, parent: QObject | None = None):
        super().__init__(parent)
        self.platform = platform
        self.log = logging.getLogger(f"mediatheque.{platform.key}")
        self.log.setLevel(logging.DEBUG)
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._ids = itertools.count(1)
        self._jobs: dict[int, Job] = {}
        self._thread = threading.Thread(target=self._run, name=f"dl-{platform.key}", daemon=True)
        self._thread.start()

    # -- called from the UI thread -------------------------------------------------

    def submit(self, url: str, folder: str, format_label: str, cookies: dict | None, prefix: str = "") -> Job:
        job = Job(next(self._ids), url, folder, format_label, cookies, prefix=prefix)
        self._jobs[job.id] = job
        self._queue.put(job)
        return job

    def cancel(self, job_id: int) -> None:
        if job := self._jobs.get(job_id):
            job.cancel_event.set()

    def cancel_all(self) -> None:
        for job in self._jobs.values():
            job.cancel_event.set()

    # -- background thread ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job.cancel_event.is_set():
                self.job_finished.emit(job.id, "cancelled", "Cancelled", [])
                continue
            self.job_started.emit(job.id)
            try:
                self._download(job)
            except Exception as e:  # never let the worker thread die
                self.log.exception("Unhandled error")
                self.job_finished.emit(job.id, "failed", f"Unexpected error: {e}", [])
            finally:
                self._jobs.pop(job.id, None)

    def _download(self, job: Job) -> None:
        log = self.log
        log.info("=" * 70)
        log.info("Job %d  url=%s", job.id, job.url)
        log.info("quality=%r  cookies=%r  folder=%s", job.format_label, job.cookies, job.folder)
        log.info("yt-dlp %s  ffmpeg=%s  deno=%s", YTDLP_VERSION, ffmpeg_path(), deno_path())

        self.job_progress.emit(job.id, -1, "Fetching info…", "")
        last_emit = [0.0]
        titled = [False]
        part_files: dict[str, int] = {}

        def progress_hook(d: dict) -> None:
            if job.cancel_event.is_set():
                raise DownloadCancelled("Cancelled by user")
            info = d.get("info_dict") or {}
            if not titled[0] and info.get("title"):
                titled[0] = True
                self.job_title.emit(job.id, info["title"])
            if tmp := d.get("tmpfilename"):
                job.tmpfiles.add(tmp)
            if d["status"] != "downloading":
                return
            now = time.monotonic()
            if now - last_emit[0] < 0.15:
                return
            last_emit[0] = now

            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = min(100.0, done / total * 100)
            elif d.get("fragment_count"):
                pct = (d.get("fragment_index") or 0) / d["fragment_count"] * 100
            else:
                pct = -1

            fname = d.get("filename") or ""
            part_files.setdefault(fname, len(part_files) + 1)
            n_parts = len(info.get("requested_formats") or []) or 0
            kind = _stream_kind(info)
            status = f"Downloading {kind.lower()}".strip() if kind else "Downloading"
            if n_parts > 1:
                status += f" ({part_files[fname]}/{n_parts})"
            status += f" · {pct:.0f}%" if pct >= 0 else "…"

            bits = [f"{_fmt_bytes(done)} of {_fmt_bytes(total)}" if total else _fmt_bytes(done)]
            if speed := d.get("speed"):
                bits.append(f"{_fmt_bytes(speed)}/s")
            if eta := _fmt_eta(d.get("eta")):
                bits.append(eta)
            self.job_progress.emit(job.id, pct, status, "  ·  ".join(bits))

        def pp_hook(d: dict) -> None:
            if d["status"] == "started":
                label = _PP_LABELS.get(d.get("postprocessor", ""), "Processing…")
                self.job_progress.emit(job.id, -1, label, "")

        opts = self.platform.build_options(job.format_label)
        opts.update({
            "paths": {"home": job.folder},
            "logger": _YdlLogger(log),
            "verbose": True,
            "noprogress": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [pp_hook],
            "post_hooks": [job.files.append],
            "windowsfilenames": True,
            "overwrites": False,
            "retries": 5,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
        })
        if ff := ffmpeg_path():
            opts["ffmpeg_location"] = ff
        if dn := deno_path():
            opts["js_runtimes"] = {"deno": {"path": dn}}
        try:
            os.makedirs(job.folder, exist_ok=True)
            try:
                info = self._extract(job, {**opts, **(job.cookies or {})})
            except DownloadError as e:
                # Cookies are optional for most links: if they can't be read, carry on without.
                if not job.cookies or not _is_cookie_error(str(e)):
                    raise
                log.warning("Couldn't load cookies (%s); retrying without them", e)
                self.job_progress.emit(job.id, -1, "Cookies unreadable, retrying without…", "")
                job.notes.append("downloaded without cookies (couldn't read them)")
                info = self._extract(job, opts)
        except DownloadCancelled:
            self._cleanup(job)
            log.info("Cancelled")
            self.job_finished.emit(job.id, "cancelled", "Cancelled", [])
            return
        except DownloadError as e:
            if job.cancel_event.is_set():
                self._cleanup(job)
                self.job_finished.emit(job.id, "cancelled", "Cancelled", [])
                return
            log.error("FAILED: %s", e)
            self.job_finished.emit(job.id, "failed", self.platform.explain(str(e)), [])
            return
        except OSError as e:
            log.exception("FAILED")
            self.job_finished.emit(job.id, "failed", f"Couldn't write to the folder: {e.strerror or e}", [])
            return

        if not titled[0] and info and info.get("title"):
            self.job_title.emit(job.id, info["title"])
        files = list(dict.fromkeys(job.files + _collect_files(info)))
        files = [f for f in files if os.path.exists(f)]
        if not files:
            log.error("FAILED: finished but no file was produced")
            self.job_finished.emit(job.id, "failed", "Nothing was downloaded. The post may have no video.", [])
            return
        if self.platform.source and info:
            self.job_source.emit(job.id, self.platform.source(info))
        log.info("Done: %s", files)
        self.job_finished.emit(job.id, "done", "  ·  ".join(job.notes), files)

    def _extract(self, job: Job, opts: dict) -> dict | None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if ff := ffmpeg_path():
                ydl.add_post_processor(_PremierePP(ydl, ff, job, self), when="after_move")
            if self.platform.rename or job.prefix:
                ydl.add_post_processor(_RenamePP(ydl, self.platform.rename, job.prefix), when="after_move")
            url = self.platform.expand(job.url) if self.platform.expand else job.url
            return ydl.extract_info(url, download=True)

    def _cleanup(self, job: Job) -> None:
        """Remove partial files left behind by a cancelled download."""
        for tmp in job.tmpfiles:
            for path in glob.glob(glob.escape(tmp) + "*"):
                try:
                    os.remove(path)
                except OSError:
                    pass
