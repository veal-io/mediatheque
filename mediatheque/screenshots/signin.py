"""Sign In to Sites: the capture browser's profile, opened as a plain Microsoft Edge window.

Sign-ins (paywalls, Google / Apple pop-ups, two-factor) often break inside the automated
capture window. Here there's no automation at all, so they behave as in any browser, and
because it's the same profile, every capture afterwards is signed in.

The capture window and this window share the profile, so only one can be open at a time:
a capture session waits for this window to close before it starts.

Many sites keep you signed in with a *session* cookie, which Edge throws away when the
window closes, so the capture window would open signed out. While this window is open,
its session cookies are turned into cookies that last KEEP_DAYS, so Edge saves them
(encrypted, in the profile like any other cookie). That happens every couple of seconds,
so closing the window with its X is fine too.
"""
from __future__ import annotations

import html
import logging
import os
import socket
import subprocess
import sys
import threading
import time

from ..config import data_dir
from .naming import own_browser, profile_dir

SITES = [  # sites that are often behind a sign-in; anything else can be typed in the address bar
    ("Fortune", "https://fortune.com/"),
    ("The New York Times", "https://www.nytimes.com/"),
    ("The Wall Street Journal", "https://www.wsj.com/"),
    ("Bloomberg", "https://www.bloomberg.com/"),
    ("The Washington Post", "https://www.washingtonpost.com/"),
    ("Financial Times", "https://www.ft.com/"),
    ("Business Insider", "https://www.businessinsider.com/"),
    ("The Atlantic", "https://www.theatlantic.com/"),
    ("The Economist", "https://www.economist.com/"),
    ("Reuters", "https://www.reuters.com/"),
]

KEEP_DAYS = 30
_proc: subprocess.Popen | None = None
log = logging.getLogger("mediatheque.screenshots")


MAC_BROWSERS = [  # (Playwright channel, app binary)
    ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]


def browser_channel() -> str | None:
    """Which installed browser Playwright drives: Edge, or Chrome on a Mac without Edge.
    None: Playwright's own Chromium (needs `python -m playwright install chromium`)."""
    if sys.platform != "darwin":
        return "msedge"
    if own_browser():
        return None  # Playwright's own Chromium (see naming.own_browser)
    home = os.path.expanduser("~")
    for channel, binary in MAC_BROWSERS:
        if os.path.exists(binary) or os.path.exists(home + binary):
            return channel
    return None


def find_edge() -> str | None:
    """The browser binary for the Sign In window (Edge; on a Mac, Chrome if there's no Edge)."""
    if sys.platform == "darwin":
        if own := own_browser():
            return own
        home = os.path.expanduser("~")
        return next((b for _c, b in MAC_BROWSERS for b in (b, home + b) if os.path.exists(b)), None)
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
        except OSError:
            pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        path = os.path.join(base or "", "Microsoft", "Edge", "Application", "msedge.exe")
        if base and os.path.exists(path):
            return path
    return None


def is_open() -> bool:
    return _proc is not None and _proc.poll() is None


def _start_page() -> str:
    links = "\n".join(f'<li><a href="{html.escape(u)}">{html.escape(n)}</a></li>' for n, u in SITES)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Sign In to Sites</title>
<style>
 body {{ font: 15px "Segoe UI", Tahoma, sans-serif; background: #008080; margin: 0; padding: 40px; }}
 .win {{ max-width: 620px; margin: auto; background: #c0c0c0; border: 2px solid; border-color: #fff #000 #000 #fff; }}
 .title {{ background: #000080; color: #fff; font-weight: bold; padding: 4px 8px; }}
 .body {{ padding: 14px 18px 18px; }}
 ul {{ background: #fff; border: 2px solid; border-color: #808080 #fff #fff #808080; padding: 10px 30px; columns: 2; }}
 li {{ margin: 4px 0; }}
 b {{ color: #000080; }}
</style></head><body><div class="win"><div class="title">Mediatheque: Sign In to Sites</div><div class="body">
<p>This is the Screenshotter's own browser. Sign in to any site you capture from (open it below, or type its
address), and <b>the Screenshotter will be signed in too</b>.</p>
<ul>{links}</ul>
<p>When you're done, <b>close this window</b> (or click <b>Done Signing In</b> in Mediatheque).
Captures wait until it's closed.</p>
</div></div></body></html>"""
    path = data_dir() / "signin.html"
    path.write_text(page, encoding="utf-8")
    return path.as_uri()


def open_window(start: str = "") -> str:
    """Open the window (on the sign-in links page, or `start`). Returns an error message, or ""."""
    global _proc
    if is_open():
        return ""
    edge = find_edge()
    if not edge:
        return ("Microsoft Edge wasn't found. The Screenshotter needs it." if sys.platform != "darwin"
                else "Neither Microsoft Edge nor Google Chrome was found in Applications. Install one of them.")
    with socket.socket() as s:  # a free local port for keeping the sign-ins (see above)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _proc = subprocess.Popen([
        edge, f"--user-data-dir={profile_dir()}", "--no-first-run", "--no-default-browser-check",
        "--disable-background-mode", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
        # On a Mac, the capture window (run by Playwright) keeps sign-ins with a stand-in for the
        # Keychain; this window has to do the same, or each couldn't read the other's sign-ins.
        *(["--use-mock-keychain"] if sys.platform == "darwin" else []),
        "--new-window", start or _start_page(),
    ])
    threading.Thread(target=_keep_signins, args=(_proc, port), daemon=True, name="keep-signins").start()
    return ""


def keep_session_cookies(ctx) -> int:
    """Turn the browser's session cookies into ones that last KEEP_DAYS, so they're saved.
    Used by the Sign In window and by the capture window: sites like Fortune renew their
    sign-in cookie on every page as a new session cookie, which would otherwise be lost
    when the window closes. Returns how many were kept."""
    session = [c for c in ctx.cookies() if c.get("expires", -1) in (-1, 0)]
    if session:
        until = time.time() + KEEP_DAYS * 86400
        ctx.add_cookies([{k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "secure", "sameSite")
                          if k in c} | {"expires": until} for c in session])
    return len(session)


def _keep_signins(proc: subprocess.Popen, port: int) -> None:
    """Make the window's session cookies last KEEP_DAYS, until the window closes."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser = None
            for _ in range(60):  # Edge takes a moment to start listening
                if proc.poll() is not None:
                    return
                try:
                    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    time.sleep(0.5)
            if browser is None:
                log.warning("Sign In window: couldn't attach to keep session sign-ins")
                return
            ctx = browser.contexts[0]
            while proc.poll() is None and browser.is_connected():
                try:
                    if n := keep_session_cookies(ctx):
                        log.info("Sign In window: kept %d session cookie(s)", n)
                except Exception as e:  # the window is closing
                    log.debug("Sign In window: %s", e)
                    break
                time.sleep(2)
    except Exception:
        log.exception("Sign In window: keeping sign-ins failed")


def close_window() -> None:
    """Close it the way the X button does, so the sign-ins are written to disk first.
    (Asks the main window to close; Edge then shuts down its own helper processes.)"""
    if is_open():
        if sys.platform == "darwin":
            _proc.terminate()  # Chromium on a Mac shuts down cleanly on SIGTERM
            return
        subprocess.run(["taskkill", "/PID", str(_proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
