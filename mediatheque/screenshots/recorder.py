"""Screen recording for the capture window: records the whole monitor the article is on,
to an H.264 MP4 that Premiere can open. Video only. Stopped with ffmpeg's own "q", so the
file is finished properly.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from ..convert import working_encoders

log = logging.getLogger("mediatheque.screenshots")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
FRAMERATE = 60
RECORD_FOLDER = "Screen Records"
# Quality as in convert.py, at settings fast enough to keep up with a live 4K screen.
_LIVE_ARGS = {
    "NVIDIA GPU": ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-profile:v", "high"],
    "Intel Quick Sync": ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "20", "-profile:v", "high"],
    "Apple VideoToolbox": ["-c:v", "h264_videotoolbox", "-q:v", "65", "-profile:v", "high", "-realtime", "1"],
    "CPU": ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-profile:v", "high"],
}


def record_folder(shots_folder: str) -> str:
    """Screen Records sits beside Screen Captures in the project folder. If shots are going
    somewhere else (the Screenshots page's Save to), it goes inside that folder."""
    folder = os.path.normpath(shots_folder)
    if os.path.basename(folder).lower() == "screen captures":
        folder = os.path.dirname(folder)
    return os.path.join(folder, RECORD_FOLDER)


@dataclass
class Monitor:
    x: int        # physical pixels on the Windows desktop
    y: int
    width: int
    height: int
    device: str   # e.g. \\.\DISPLAY1, which is also QScreen.name()


def monitor_of_window(title_part: str) -> Monitor | None:
    """The monitor of the top-level window whose title contains title_part. On a Mac, the
    main display (the recorder captures a whole display there; see _mac_screen_index)."""
    if sys.platform == "darwin":
        return Monitor(0, 0, 0, 0, "")
    from ctypes import wintypes

    class _MonitorInfoEx(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfoEx)]
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if title_part in buf.value and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return None
    info = _MonitorInfoEx()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(found[0], 2), ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return Monitor(r.left, r.top, r.right - r.left, r.bottom - r.top, info.szDevice)


class Recorder:
    def __init__(self, ffmpeg: str, monitor: Monitor, path: str):
        self.ffmpeg, self.monitor, self.path = ffmpeg, monitor, path
        self.proc: subprocess.Popen | None = None
        self.error = ""
        self._errfile = None

    def _mac_screen_index(self) -> str | None:
        """AVFoundation numbers cameras first, then screens: find "Capture screen 0"."""
        out = subprocess.run([self.ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                             capture_output=True, text=True, errors="replace").stderr
        m = re.search(r"\[(\d+)\] Capture screen 0", out)
        return m.group(1) if m else None

    def _captures(self) -> list[tuple[str, list[str]]]:
        """Ways to grab the screen, best first. Desktop Duplication (ddagrab) keeps up with a
        4K screen at full frame rate, but only the primary monitor's number is certain. GDI
        (gdigrab) manages about 25 fps at 4K, and works on any monitor."""
        m = self.monitor
        w, h = m.width & ~1, m.height & ~1
        if sys.platform == "darwin":  # the whole main display (needs Screen Recording permission)
            from ..osutil import mac_screen_recording_allowed
            if not mac_screen_recording_allowed(ask=True):
                # Without it, ffmpeg runs but records nothing, so say so before starting.
                self.error = ("macOS hasn't allowed screen recording yet. In System Settings > Privacy & Security > "
                              "Screen & System Audio Recording, turn on Terminal, then quit and reopen Mediatheque.")
                return []
            index = self._mac_screen_index()
            if index is None:
                self.error = ("macOS didn't offer a screen to record. Allow Screen Recording for Terminal "
                              "(or Python) in System Settings > Privacy & Security, then restart Mediatheque.")
                return []
            return [("AVFoundation", ["-f", "avfoundation", "-capture_cursor", "1", "-framerate", str(FRAMERATE),
                                      "-pixel_format", "uyvy422",
                                      "-i", f"{index}:none",
                                      "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"])]
        # Proper HD colour: left to itself the encoder would tag the older SD standard.
        color = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
        gdi = ("GDI", ["-f", "gdigrab", "-framerate", str(FRAMERATE), "-draw_mouse", "1",
                       "-offset_x", str(m.x), "-offset_y", str(m.y), "-video_size", f"{w}x{h}", "-i", "desktop",
                       "-vf", color])
        if m.x == 0 and m.y == 0:
            dda = f"ddagrab=output_idx=0:framerate={FRAMERATE}:draw_mouse=1:video_size={w}x{h},hwdownload,format=bgra,{color}"
            return [("Desktop Duplication", ["-filter_complex", dda]), gdi]
        return [gdi]

    def start(self) -> bool:
        """Start recording with the first method and encoder that run. False if none did (see error)."""
        for capture_name, capture in self._captures():
            for name, _args in working_encoders(self.ffmpeg):
                self._errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
                # A locked frame rate and HD colour tags: Premiere handles these files properly.
                # Apple's encoder drops the colour tags below from the video itself; write them into it.
                tags = (["-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"]
                        if name == "Apple VideoToolbox" else [])
                cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", *capture, *_LIVE_ARGS[name], *tags,
                       "-fps_mode", "cfr", "-r", str(FRAMERATE), "-colorspace", "bt709", "-color_primaries", "bt709",
                       "-color_trc", "bt709", "-color_range", "tv", "-movflags", "+faststart", self.path]
                log.info("Recording with %s + %s: %s", capture_name, name, " ".join(cmd))
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=self._errfile, creationflags=_NO_WINDOW)
                time.sleep(0.7)
                if self.proc.poll() is None:
                    return True
                self.error = self._errors()
                log.warning("Recorder %s + %s failed: %s", capture_name, name, self.error)
        self.error = self.error or "No video encoder worked on this computer."
        return False

    def _errors(self) -> str:
        self._errfile.seek(0)
        lines = [line for line in self._errfile.read().splitlines() if line.strip()]
        return lines[-1][:200] if lines else "ffmpeg stopped unexpectedly."

    def stop(self) -> bool:
        """Finish the file. True if there's a playable recording."""
        if self.proc is None:
            return False
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        ok = self.proc.returncode == 0 and os.path.exists(self.path) and os.path.getsize(self.path) > 0
        if not ok:
            self.error = self._errors()
        self.proc = None
        return ok
