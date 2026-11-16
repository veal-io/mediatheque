"""The few things that differ between Windows and macOS: opening and revealing files,
starting background programs without a console window, and keeping the recorder's control
bar out of screen recordings.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Words for messages: what the file manager and the shortcut key are called on this computer.
FILE_MANAGER = "Finder" if IS_MAC else "Explorer"
MOD_KEY = "Cmd" if IS_MAC else "Ctrl"

# For subprocess.Popen/run: no console window flashing up on Windows (0 elsewhere).
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def open_path(path: str) -> None:
    """Open a file or folder the way double-clicking it would."""
    if IS_WINDOWS:
        os.startfile(path)
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path: str) -> None:
    """Show a file selected in Explorer / Finder (or open a folder)."""
    path = os.path.normpath(path)
    if os.path.isdir(path):
        open_path(path)
    elif not os.path.exists(path):
        if os.path.isdir(os.path.dirname(path)):
            open_path(os.path.dirname(path))
    elif IS_WINDOWS:
        subprocess.Popen(f'explorer /select,"{path}"')
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        open_path(os.path.dirname(path))


# -- paths saved on another computer -----------------------------------------------------

_WINDOWS_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def is_foreign_windows_path(path: str) -> bool:
    """A Windows path (C:\\…, \\\\server\\…) read on a Mac: it can't be used as is."""
    return not IS_WINDOWS and bool(_WINDOWS_ABS.match(path or ""))


def is_foreign_path(path: str) -> bool:
    """A path saved on the other kind of computer: Windows on a Mac, or Mac on Windows."""
    return is_foreign_windows_path(path) or (IS_WINDOWS and (path or "").startswith("/"))


def path_name(path: str) -> str:
    """The last part of a path, whichever computer it came from."""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1] if path else ""


# -- the recorder's control bar ----------------------------------------------------------

def exclude_from_capture(widget) -> None:
    """Keep a window visible on screen but out of screen recordings and screenshots."""
    try:
        if IS_WINDOWS:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)  # WDA_EXCLUDEFROMCAPTURE
        elif IS_MAC:
            _mac_window_setup(int(widget.winId()))
    except Exception:  # cosmetic: the bar would show up in the video, nothing worse
        pass


def _mac_window_setup(view_ptr: int) -> None:
    """[[view window] setSharingType:NSWindowSharingNone], and let the bar float over a
    full-screen browser (join all Spaces, full-screen auxiliary)."""
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_ulong = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc))
    send_long = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(("objc_msgSend", objc))
    window = send_id(view_ptr, objc.sel_registerName(b"window"))
    if not window:
        return
    send_ulong(window, objc.sel_registerName(b"setSharingType:"), 0)                  # NSWindowSharingNone
    send_ulong(window, objc.sel_registerName(b"setCollectionBehavior:"), (1 << 0) | (1 << 8))
    send_long(window, objc.sel_registerName(b"setLevel:"), 25)                         # NSStatusWindowLevel
    # Only a non-activating panel may show on another app's full-screen Space (the browser's).
    send_ulong_ret = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    style = send_ulong_ret(window, objc.sel_registerName(b"styleMask"))
    send_ulong(window, objc.sel_registerName(b"setStyleMask:"), style | (1 << 7))       # NSWindowStyleMaskNonactivatingPanel
    # A Qt tool window is a panel that macOS hides whenever another app (the browser) is in front.
    send_ulong(window, objc.sel_registerName(b"setHidesOnDeactivate:"), 0)
    # Settings that decide which Space a window is on only apply when it's next put on screen.
    send_id(window, objc.sel_registerName(b"orderFrontRegardless"))


def set_mac_app_name(name: str) -> None:
    """Show `name` instead of "Python" in the Mac menu bar and Dock. Call before QApplication."""
    if not IS_MAC:
        return
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        msg = ("objc_msgSend", objc)
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(msg)
        send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(msg)
        send_2 = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(msg)
        sel = objc.sel_registerName
        nsstring = objc.objc_getClass(b"NSString")
        bundle = send(objc.objc_getClass(b"NSBundle"), sel(b"mainBundle"))
        info = send(bundle, sel(b"infoDictionary"))  # mutable in practice; Qt reads the name from it
        if info:
            value = send_str(nsstring, sel(b"stringWithUTF8String:"), name.encode())
            key = send_str(nsstring, sel(b"stringWithUTF8String:"), b"CFBundleName")
            send_2(info, sel(b"setObject:forKey:"), value, key)
    except Exception:  # cosmetic only
        pass


def mac_screen_recording_allowed(ask: bool = False) -> bool:
    """Whether macOS lets this app (really Terminal, which started it) record the screen.
    ask=True shows macOS's own permission prompt the first time. True when not a Mac or unknown."""
    if not IS_MAC:
        return True
    try:
        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if cg.CGPreflightScreenCaptureAccess():
            return True
        if ask:
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            return bool(cg.CGRequestScreenCaptureAccess())
        return False
    except Exception:  # older macOS without these calls: let ffmpeg try
        return True
