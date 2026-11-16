"""Make downloads Premiere-ready.

Premiere Pro can't decode VP9 or AV1, which YouTube (above 1080p) and Instagram
(often at 1080p) serve. Files in those codecs are re-encoded to H.264, using the
GPU when one is available. H.264 and HEVC files are left untouched.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from typing import Callable

NEEDS_CONVERSION = {"vp9", "vp8", "av1"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Tried in order; the first that works on this machine is used.
ENCODERS: list[tuple[str, list[str]]] = [
    ("NVIDIA GPU", ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq",
                    "-rc", "vbr", "-cq", "18", "-b:v", "0", "-profile:v", "high"]),
    ("Intel Quick Sync", ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", "18",
                          "-profile:v", "high"]),
    ("Apple VideoToolbox", ["-c:v", "h264_videotoolbox", "-q:v", "70", "-profile:v", "high"]),
    ("CPU", ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high"]),
]


class ConversionCancelled(Exception):
    pass


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW, **kw)


def probe(ffmpeg: str, path: str) -> tuple[str | None, str | None, float]:
    """Return (video codec, audio codec, duration in seconds) using ffmpeg itself."""
    err = _run([ffmpeg, "-hide_banner", "-i", path], timeout=60).stderr
    v = re.search(r"Stream #\S+.*?: Video: (\w+)", err)
    a = re.search(r"Stream #\S+.*?: Audio: (\w+)", err)
    d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else 0.0
    return (v[1] if v else None), (a[1] if a else None), duration


_working: list[tuple[str, list[str]]] | None = None
_working_lock = threading.Lock()


def working_encoders(ffmpeg: str) -> list[tuple[str, list[str]]]:
    """Encoders that actually run on this machine (checked once per session)."""
    global _working
    with _working_lock:
        if _working is None:
            _working = []
            for name, args in ENCODERS:
                test = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc2=size=1280x720:rate=30", "-t", "0.2", *args,
                        "-pix_fmt", "yuv420p", "-f", "null", "-"]
                try:
                    if _run(test, timeout=30).returncode == 0:
                        _working.append((name, args))
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return _working


def to_h264(ffmpeg: str, src: str, log: logging.Logger,
            on_progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> str | None:
    """Re-encode src to H.264 MP4 if its video codec needs it.

    Returns the new path, or None if nothing needed doing.
    """
    vcodec, acodec, duration = probe(ffmpeg, src)
    log.info("Codec check: video=%s audio=%s duration=%.1fs", vcodec, acodec, duration)
    if vcodec not in NEEDS_CONVERSION:
        return None

    base = os.path.splitext(src)[0]
    final = base + ".mp4"
    tmp = base + ".converting.mp4"
    audio = ["-c:a", "copy"] if acodec == "aac" else ["-c:a", "aac", "-b:a", "256k"]
    last_error = ""

    for name, enc in working_encoders(ffmpeg):
        log.info("Converting %s -> H.264 with %s", vcodec, name)
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", src,
               "-map", "0:v:0", "-map", "0:a?", *enc, "-pix_fmt", "yuv420p", *audio,
               "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        stderr_lines: list[str] = []
        reader = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)
        reader.start()
        label = f"Converting to H.264 for Premiere ({name})"
        on_progress(-1, label)
        for line in proc.stdout:
            if cancelled():
                proc.kill()
                proc.wait()
                _remove(tmp)
                raise ConversionCancelled()
            if line.startswith("out_time_us=") and duration:
                try:
                    pct = min(100.0, int(line.split("=")[1]) / 1e6 / duration * 100)
                    on_progress(pct, f"{label} · {pct:.0f}%")
                except ValueError:
                    pass
        proc.wait()
        reader.join(timeout=5)
        if proc.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, final)
            if os.path.normcase(final) != os.path.normcase(src):
                _remove(src)
            log.info("Converted with %s: %s", name, final)
            return final
        last_error = "".join(stderr_lines).strip()
        log.warning("%s encoder failed (%s): %s", name, proc.returncode, last_error[-800:])
        _remove(tmp)

    raise RuntimeError(f"Couldn't convert to H.264: {last_error[-300:] or 'no working encoder'}")


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
