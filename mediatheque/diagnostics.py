"""Help > Report a Problem… and Help > Check Setup…

Report a Problem zips Mediatheque's logs with a short note of versions, for sending to
whoever looks after the copy. It holds no projects, cookies, keys or settings.

Check Setup lists what Mediatheque needs on this computer, with a plain-English fix beside
anything missing.
"""
from __future__ import annotations

import datetime as dt
import os
import platform
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__, edition
from .config import log_dir
from .osutil import IS_MAC, IS_WINDOWS

ENGINE_STALE_DAYS = 28  # nudge to Tools > Update Engine after about four weeks


def ytdlp_version() -> str:
    """The yt-dlp that's installed now (after Update Engine, before a restart, this is the new one)."""
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        try:
            from yt_dlp.version import __version__ as v
            return v
        except Exception:
            return "not installed"


def os_version() -> str:
    if IS_MAC:
        return f"macOS {platform.mac_ver()[0] or '?'} ({platform.machine()})"
    if IS_WINDOWS:
        return f"Windows {platform.version()} ({platform.machine()})"
    return platform.platform()


def versions() -> list[tuple[str, str]]:
    try:
        from PySide6 import __version__ as qt
    except Exception:
        qt = "?"
    return [
        ("Mediatheque", __version__ + ("  (editor edition)" if edition.EDITOR else "")),
        ("System", os_version()),
        ("Python", platform.python_version()),
        ("yt-dlp", ytdlp_version()),
        ("PySide6", qt),
    ]


# -- Report a Problem --------------------------------------------------------------------------

def desktop() -> Path:
    d = Path.home() / "Desktop"
    return d if d.is_dir() else Path.home()


def make_report(dest_dir: Path | None = None, what_happened: str = "") -> Path:
    """Zip the logs folder and a note of versions. Returns the zip's path."""
    dest_dir = dest_dir or desktop()
    stamp = time.strftime("%Y-%m-%d %H%M")
    dest = dest_dir / f"Mediatheque problem report {stamp}.zip"
    n = 2
    while dest.exists():
        dest = dest_dir / f"Mediatheque problem report {stamp} ({n}).zip"
        n += 1
    note = ["Mediatheque problem report", f"Made {time.strftime('%Y-%m-%d %H:%M')}", ""]
    note += [f"{k}: {v}" for k, v in versions()]
    if what_happened.strip():
        note += ["", "What happened:", what_happened.strip()]
    note += ["", "The logs folder is included. No projects, cookies, keys or settings are."]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("About this report.txt", "\n".join(note) + "\n")
        for f in sorted(log_dir().iterdir()):
            # Only log files (app.log, youtube.log.1, launch.log…), nothing else that ends up there.
            if f.is_file() and ".log" in f.name:
                z.write(f, f"logs/{f.name}")
    return dest


# -- Check Setup --------------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    status: str    # "ok", "warn" or "fail"
    detail: str    # what was found
    fix: str = ""  # what to do about it, in plain English


def _python() -> Check:
    v = sys.version_info
    if v >= (3, 10):
        return Check("Python", "ok", f"Python {platform.python_version()}")
    return Check("Python", "fail", f"Python {platform.python_version()} is too old",
                 "Install Python 3.10 or newer from python.org, then delete the .venv folder inside the "
                 "Mediatheque folder and start Mediatheque again (it sets itself up anew).")


def _browser() -> Check:
    name = "Edge or Chrome"
    try:
        if IS_MAC:
            from .screenshots.naming import own_browser
            from .screenshots.signin import MAC_BROWSERS
            if own_browser():
                return Check(name, "ok", "Mediatheque's own browser (Google Chrome for Testing) is installed")
            home = os.path.expanduser("~")
            for channel, binary in MAC_BROWSERS:
                if os.path.exists(binary) or os.path.exists(home + binary):
                    return Check(name, "ok", f"Using {'Microsoft Edge' if channel == 'msedge' else 'Google Chrome'}")
            return Check(name, "fail", "No browser found for screenshots and previews",
                         "Install Google Chrome (google.com/chrome) or Microsoft Edge. Starting Mediatheque with "
                         "Mediatheque.command also downloads its own browser.")
        if IS_WINDOWS:
            from .screenshots.signin import find_edge
            if find_edge():
                return Check(name, "ok", "Microsoft Edge found")
            return Check(name, "fail", "Microsoft Edge wasn't found",
                         "Install Microsoft Edge from microsoft.com/edge. Screenshots and previews use it.")
    except Exception as e:  # noqa: BLE001
        return Check(name, "warn", f"Couldn't check ({e})")
    return Check(name, "warn", "Not checked on this system")


def _firefox() -> Check:
    if IS_MAC:
        found = any(os.path.exists(p) for p in ("/Applications/Firefox.app",
                                                os.path.expanduser("~/Applications/Firefox.app")))
    elif IS_WINDOWS:
        roots = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        found = any(os.path.exists(os.path.join(r, "Mozilla Firefox", "firefox.exe")) for r in roots if r)
    else:
        found = bool(shutil.which("firefox"))
    if found:
        return Check("Firefox", "ok", "Installed. Sign in to YouTube in it, and set the Downloader's Cookies to "
                                      "Firefox when YouTube asks you to confirm you're not a bot.")
    return Check("Firefox", "warn", "Not installed",
                 "Install Firefox (firefox.com) and sign in to YouTube in it. When YouTube asks you to confirm "
                 "you're not a bot, set the Downloader's Cookies to Firefox and Retry.")


def _ffmpeg() -> Check:
    try:
        from .convert import working_encoders
        from .engine import ffmpeg_path
        ffmpeg = ffmpeg_path()
        if not ffmpeg or not os.path.exists(ffmpeg):
            return Check("ffmpeg", "fail", "Not found",
                         "Quit Mediatheque, delete the .venv folder inside the Mediatheque folder, and start it again "
                         "so it reinstalls its packages.")
        enc = working_encoders(ffmpeg)
        if not enc:
            return Check("ffmpeg", "fail", "Found, but no H.264 encoder works",
                         "Downloads still work, but files that need converting for Premiere (VP9, AV1) won't be. "
                         "Use Help > Report a Problem… and send the zip.")
        return Check("ffmpeg", "ok", f"Converts to H.264 with: {enc[0][0]}")
    except Exception as e:  # noqa: BLE001
        return Check("ffmpeg", "warn", f"Couldn't check ({e})")


def _screen_recording() -> Check | None:
    if not IS_MAC:
        return None
    from .osutil import mac_screen_recording_allowed
    if mac_screen_recording_allowed(ask=False):
        return Check("Screen Recording", "ok", "Allowed, so V in the capture window can record the screen")
    return Check("Screen Recording", "warn", "Not allowed yet (only needed for recording the screen)",
                 "Open System Settings > Privacy & Security > Screen & System Audio Recording, turn on Mediatheque "
                 "(or Terminal, if you start it with Mediatheque.command), then quit and reopen Mediatheque.")


def _engine() -> Check:
    v = ytdlp_version()
    try:
        y, m, d = (int(x) for x in v.split(".")[:3])
        age = (dt.date.today() - dt.date(y, m, d)).days
    except ValueError:
        return Check("Download engine", "warn", f"yt-dlp {v}", "Choose Tools > Update Engine….")
    if age > ENGINE_STALE_DAYS:
        return Check("Download engine", "warn", f"yt-dlp {v}, {age} days old",
                     "Choose Tools > Update Engine… to get the latest. Sites change often, and an update "
                     "usually fixes downloads that stopped working.")
    return Check("Download engine", "ok", f"yt-dlp {v} ({age} day{'s' if age != 1 else ''} old)")


def run_checks() -> list[Check]:
    """Takes a few seconds (it tries each video encoder): run it off the main thread."""
    checks = [_python(), _browser(), _firefox(), _ffmpeg(), _screen_recording(), _engine()]
    return [c for c in checks if c is not None]
