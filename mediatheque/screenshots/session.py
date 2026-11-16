"""Interactive capture session: a visible Edge window that steps through each article.

The user aims a box over the page and clicks; the region is captured at 3x the page's
CSS resolution (independent of screen scaling), then they press Y to keep and move on,
A to keep and take another, or N to retake. Runs on its own thread; the browser profile
is persistent, so sign-ins and age-gate clicks are remembered between sessions.
"""
from __future__ import annotations

import base64
import itertools
import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import recorder, signin
from .links import Fragment
from .naming import META_JS, clean_headline, file_name, prefix_for, profile_dir, unique_path

OUTPUT_SCALE = 3  # image pixels per CSS pixel
OVERLAY_JS = (Path(__file__).with_name("overlay.js")).read_text(encoding="utf-8")
QUOTE_JS = (Path(__file__).with_name("quote.js")).read_text(encoding="utf-8")

# Consent popups only; site headers and logos are left alone (C toggles those in the window).
CONSENT_JS = r"""
() => {
  const selectors = [
    '#onetrust-consent-sdk', '#onetrust-banner-sdk', '.onetrust-pc-dark-filter', '#CybotCookiebotDialog',
    '#CybotCookiebotDialogBodyUnderlay', '#didomi-host', '.didomi-popup-backdrop', '.qc-cmp2-container',
    '#qc-cmp2-container', '.fc-consent-root', '[id^="sp_message_container"]', '#truste-consent-track',
    '.truste_box_overlay', '.truste_overlay', '#usercentrics-root', '#cmpbox', '#cmpbox2', '.cc-window',
    '#wm-ipp-base', '#wm-ipp-print', '#donato',
  ];
  let removed = 0;
  for (const sel of selectors) document.querySelectorAll(sel).forEach(e => { e.remove(); removed++; });
  if (removed) for (const el of [document.documentElement, document.body]) el.style.setProperty('overflow', 'auto', 'important');
}
"""


@dataclass
class Stop:
    id: int
    url: str
    fragment: Fragment | None
    folder: str
    context: str = ""  # e.g. the script passage this capture belongs to
    prefix: str = ""   # added to file names, e.g. "7_" for script highlight 7
    label: str = ""    # the script highlight's number, e.g. "7" or "12a"
    note: str = ""     # the editor's note on that highlight
    cancelled: threading.Event = field(default_factory=threading.Event)


def _hide_fullscreen_toolbar(profile: str) -> None:
    """Chrome on a Mac keeps its tabs and address bar in full screen unless "Always Show Toolbar
    in Full Screen" is off, and they'd be in every recording. Turn it off in the capture profile
    (Chrome isn't running yet, so it reads this when it starts)."""
    import json
    prefs = Path(profile) / "Default" / "Preferences"
    try:
        data = json.loads(prefs.read_text(encoding="utf-8")) if prefs.exists() else {}
        if data.get("browser", {}).get("show_fullscreen_toolbar") is False:
            return
        data.setdefault("browser", {})["show_fullscreen_toolbar"] = False
        prefs.parent.mkdir(parents=True, exist_ok=True)
        prefs.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        logging.getLogger("mediatheque.screenshots").debug("Couldn't set the full-screen toolbar", exc_info=True)


class _EndSession(Exception):
    """Esc pressed or the window was closed."""


class SessionWorker(QObject):
    stop_started = Signal(int)
    shot_saved = Signal(int, str)       # stop id, file path
    stop_finished = Signal(int, str)    # stop id, "done" | "skipped" | "stopped" | "cancelled" | "failed:<msg>"
    session_ended = Signal(int)         # shots saved this session
    record_state = Signal(str, dict)    # the recorder's control bar: "ready" | "recording" | "review" | "hidden"
    status = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.log = logging.getLogger("mediatheque.screenshots")
        self.log.setLevel(logging.DEBUG)
        self._queue: queue.Queue[Stop | None] = queue.Queue()
        self._ids = itertools.count(1)
        self._stops: dict[int, Stop] = {}
        self._box: tuple[float, float] | None = None  # the user's box size, kept between pages
        self.active = False  # a capture window is open (the Sign In window has to wait)
        self._record_choices: queue.Queue[str] = queue.Queue()  # clicks on the recorder's control bar
        self._thread = threading.Thread(target=self._run, name="screenshots", daemon=True)
        self._thread.start()

    # -- called from the UI thread -------------------------------------------------

    def add(self, url: str, fragment: Fragment | None, folder: str, context: str = "", prefix: str = "",
            label: str = "", note: str = "") -> Stop:
        stop = Stop(next(self._ids), url, fragment, folder, context, prefix, label, note)
        self._stops[stop.id] = stop
        self._queue.put(stop)
        return stop

    def record_choice(self, choice: str) -> None:
        self._record_choices.put(choice)

    def cancel(self, stop_id: int) -> None:
        if stop := self._stops.get(stop_id):
            stop.cancelled.set()

    def shutdown(self) -> None:
        for stop in self._stops.values():
            stop.cancelled.set()
        self._queue.put(None)
        self._thread.join(timeout=5)

    # -- background thread ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            stop = self._queue.get()
            if stop is None:
                return
            if stop.cancelled.is_set():
                self._finish(stop, "cancelled")
                continue
            self._session(stop)

    def _finish(self, stop: Stop, outcome: str) -> None:
        self._stops.pop(stop.id, None)
        self.stop_finished.emit(stop.id, outcome)

    def _session(self, first: Stop) -> None:
        from playwright.sync_api import sync_playwright

        saved_total = 0
        done = 0
        pw = ctx = None
        self.active = True
        try:
            if signin.is_open():  # it has the browser profile: wait for it to be closed
                self.status.emit("Captures will start when the Sign In window is closed.")
                while signin.is_open():
                    if first.cancelled.is_set():
                        break
                    time.sleep(0.5)
            self.status.emit("Opening the capture window…")
            pw = sync_playwright().start()
            ctx = self._launch(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            stop = first
            while stop is not None:
                if stop.cancelled.is_set():
                    self._finish(stop, "cancelled")
                else:
                    done += 1
                    total = done + self._queue.qsize()
                    try:
                        saved = self._visit(page, stop, done, total)
                    except _EndSession:
                        self._finish(stop, "stopped")
                        raise
                    except Exception as e:
                        if page.is_closed():
                            self._finish(stop, "stopped")
                            raise _EndSession() from e
                        self.log.exception("Page failed: %s", stop.url)
                        self._finish(stop, f"failed:{str(e).splitlines()[0][:160]}")
                    else:
                        saved_total += saved
                        self._finish(stop, "done" if saved else "skipped")
                try:
                    stop = self._queue.get_nowait()  # late additions join the running session
                except queue.Empty:
                    break
                if stop is None:
                    self._queue.put(None)  # shutdown requested: hand the signal back to _run
                    break
        except _EndSession:
            # Everything still queued is left for another session.
            while True:
                try:
                    rest = self._queue.get_nowait()
                except queue.Empty:
                    break
                if rest is None:
                    self._queue.put(None)
                    break
                self._finish(rest, "stopped")
        except Exception as e:
            self.log.exception("Capture window failed")
            self.status.emit(f"The capture window failed: {str(e).splitlines()[0][:160]}")
            self._finish(first, f"failed:{str(e).splitlines()[0][:160]}") if first.id in self._stops else None
        finally:
            self.active = False
            if ctx is not None:
                try:
                    signin.keep_session_cookies(ctx)  # sign-ins renewed during the session
                except Exception:
                    pass  # the window was closed with its X: the last page's cookies were kept already
            for closer in (ctx, pw):
                try:
                    if closer is pw and pw:
                        pw.stop()
                    elif closer:
                        closer.close()
                except Exception:
                    pass
            self.session_ended.emit(saved_total)

    def _launch(self, pw):
        if sys.platform == "darwin":
            _hide_fullscreen_toolbar(profile_dir())
        # --test-type: no "unsupported command-line flag" warning strip, which recordings would catch.
        args = ["--disable-blink-features=AutomationControlled", "--start-maximized", "--app=about:blank", "--test-type"]
        try:
            return pw.chromium.launch_persistent_context(
                profile_dir(), channel=signin.browser_channel(), headless=False, no_viewport=True,
                color_scheme="light", args=args, chromium_sandbox=True)
        except Exception as e:
            if "in use" in str(e).lower() or "lock" in str(e).lower():
                raise RuntimeError("The capture browser is already open. Is another Mediatheque window running?")
            raise

    # -- one article -----------------------------------------------------------------

    def _visit(self, page, stop: Stop, index: int, total: int) -> int:
        self.stop_started.emit(stop.id)
        self.log.info("=" * 70)
        self.log.info("Article %d: %s  quote=%r", index, stop.url, stop.fragment.start if stop.fragment else None)
        stem = self._open(page, stop, index, total)
        saved = 0
        while True:
            try:
                action = self._aim(page)
            except Exception:
                if page.is_closed():
                    raise _EndSession()
                # The page navigated (signing in, following a link): set up again and carry on.
                page.wait_for_load_state("domcontentloaded", timeout=60000)
                stem = self._prepare(page, index, total, stop)
                continue
            if action["type"] == "reset":
                stem = self._open(page, stop, index, total)
                continue
            if action["type"] == "skip":
                return saved
            if action["type"] == "quit":
                raise _EndSession()
            if action["type"] == "record":
                count, done = self._record(page, stop, stem)
                saved += count
                if done:
                    return saved
                stem = self._prepare(page, index, total, stop)  # the page may have moved on meanwhile
                continue

            self._box = (action["boxW"], action["boxH"])
            page.evaluate("window.__mt.hide()")
            png = self._snap(page, action)
            try:
                decision = self._review(page)
            except Exception:
                if page.is_closed():
                    raise _EndSession()
                stem = self._prepare(page, index, total, stop)
                continue
            if decision == "retake":
                continue
            if decision == "reset":  # discard this shot and start the page over
                stem = self._open(page, stop, index, total)
                continue
            os.makedirs(stop.folder, exist_ok=True)
            path = unique_path(stop.folder, stem)
            with open(path, "wb") as f:
                f.write(png)
            saved += 1
            self.log.info("Saved %s", path)
            self.shot_saved.emit(stop.id, path)
            if decision == "next":
                return saved

    # -- screen recording ------------------------------------------------------------

    def _record(self, page, stop: Stop, stem: str) -> tuple[int, bool]:
        """Full screen, page working normally, controls on the floating bar. Returns (recordings
        saved, whether to move on to the next link)."""
        while not self._record_choices.empty():  # any stray clicks from last time
            self._record_choices.get_nowait()
        page.evaluate("window.__mt.hide()")
        page.evaluate("window.__mt.idle()")
        self._fullscreen(page, True)
        saved = 0
        rec = None
        monitor = None
        try:
            monitor = self._monitor(page)
            if monitor is None:
                self.status.emit("Couldn't find the capture window's screen to record.")
                return 0, False
            folder = recorder.record_folder(stop.folder)
            os.makedirs(folder, exist_ok=True)
            info = {"device": monitor.device, "context": stop.context, "label":
                    f"Highlight {stop.label}" if stop.label else "", "note": stop.note}
            settle = True  # Edge's "To exit full screen" notice has to fade before recording
            while True:
                self.record_state.emit("ready", {**info, "settle": settle})
                settle = False
                if self._record_wait(page, "start", "cancel") == "cancel":
                    return saved, False
                temp = unique_path(folder, stem + " (recording)", ".mp4")
                from ..engine import ffmpeg_path
                rec = recorder.Recorder(ffmpeg_path(), monitor, temp)
                if not rec.start():
                    info = {**info, "message": f"Couldn't start recording: {rec.error}"}
                    self.log.error("Recording failed to start: %s", rec.error)
                    rec = None
                    continue
                began = time.monotonic()
                self.record_state.emit("recording", info)
                self._record_wait(page, "stop")
                seconds = time.monotonic() - began
                ok = rec.stop()
                error, rec = rec.error, None
                if not ok:
                    info = {**info, "message": f"The recording failed: {error}"}
                    self.log.error("Recording failed: %s", error)
                    self._remove(temp)
                    continue
                self.record_state.emit("review", {**info, "path": temp, "seconds": seconds})
                decision = self._record_wait(page, "next", "more", "discard")
                if decision == "discard":
                    self._remove(temp)
                    return saved, False
                path = unique_path(folder, stem, ".mp4")
                os.replace(temp, path)
                saved += 1
                self.log.info("Saved recording %s", path)
                self.shot_saved.emit(stop.id, path)
                info.pop("message", None)
                if decision == "next":
                    return saved, True
        finally:
            self.record_state.emit("hidden", {})
            if rec is not None:  # the window was closed mid-recording
                rec.stop()
                self._remove(rec.path)
            if not page.is_closed():
                try:
                    self._fullscreen(page, False)
                except Exception:
                    self.log.debug("Couldn't leave full screen", exc_info=True)

    def _record_wait(self, page, *allowed: str) -> str:
        """Wait for a click on the control bar. Keeps the browser connection serviced, and
        raises if the capture window is closed. On a Mac the bar is hidden while recording (macOS
        would record it), so Control+S in the page ends the recording instead."""
        by_key = sys.platform == "darwin" and "stop" in allowed
        if by_key:
            self._stop_key_pressed(page)  # forget any earlier press
        while True:
            try:
                choice = self._record_choices.get_nowait()
            except queue.Empty:
                page.wait_for_timeout(100)
                if by_key and self._stop_key_pressed(page):
                    return "stop"
                continue
            if choice in allowed:
                return choice

    def _stop_key_pressed(self, page) -> bool:
        """Control+S pressed in the page since recording started (set up again after the page
        navigates). Not Cmd+S: that's the browser's Save."""
        try:
            return bool(page.evaluate("""() => {
                if (!window.__mtStopKey) {
                    window.__mtStopKey = true;
                    window.__mtStopPressed = false;
                    addEventListener('keydown', e => {
                        if (e.ctrlKey && !e.metaKey && e.key.toLowerCase() === 's') {
                            window.__mtStopPressed = true; e.preventDefault(); e.stopImmediatePropagation();
                        }
                    }, true);
                }
                const pressed = window.__mtStopPressed;
                window.__mtStopPressed = false;
                return pressed;
            }"""))
        except Exception:  # navigating: try again next time round
            return False

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _fullscreen(self, page, on: bool) -> None:
        cdp = page.context.new_cdp_session(page)
        try:
            window = cdp.send("Browser.getWindowForTarget")["windowId"]
            if on:
                cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "fullscreen"}})
            else:  # a window has to pass through normal on its way out of full screen
                cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "normal"}})
                cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "maximized"}})
        finally:
            cdp.detach()
        page.wait_for_timeout(500)

    def _monitor(self, page) -> recorder.Monitor | None:
        """Which screen is the capture window on? Found by briefly marking its title."""
        marker = f"mediatheque-{os.getpid()}-{next(self._ids)}"
        old = page.evaluate("m => { const t = document.title; document.title = m; return t; }", marker)
        try:
            for _ in range(20):
                page.wait_for_timeout(100)
                if monitor := recorder.monitor_of_window(marker):
                    return monitor
            return None
        finally:
            page.evaluate("t => { document.title = t; }", old)

    def _open(self, page, stop: Stop, index: int, total: int) -> str:
        """Load the article fresh: consent popups cleared, overlay installed, and the linked
        quote scrolled to and flashed. Also used by R to reset the page."""
        page.goto(stop.url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        stem = self._prepare(page, index, total, stop)
        if stop.fragment:
            found = page.evaluate(QUOTE_JS, {"start": stop.fragment.start, "end": stop.fragment.end,
                                             "prefix": stop.fragment.prefix, "suffix": stop.fragment.suffix})
            page.evaluate("t => window.__mt.message(t)", {
                "found": "",
                "hidden": "The quote is hidden: the site wants you signed in (press B to browse).",
                "missing": "Couldn't find the linked quote on this page.",
            }[found])
        return stem

    # Waiting on the user. Kept separate so a test can stand in for the person.
    def _aim(self, page) -> dict:
        return page.evaluate("window.__mt.aim()")

    def _review(self, page) -> str:
        return page.evaluate("window.__mt.review()")

    def _keep_signins(self, page) -> None:
        try:
            signin.keep_session_cookies(page.context)
        except Exception:
            self.log.debug("Couldn't keep session cookies", exc_info=True)

    def _prepare(self, page, index: int, total: int, stop: Stop) -> str:
        """Remove consent popups, work out the file name, install the overlay (with the
        script passage this shot is for, if any; the overlay is hidden before every shot)."""
        self._keep_signins(page)  # after every page load (and sign-in), keep its sign-in cookies
        page.evaluate(CONSENT_JS)
        meta = page.evaluate(META_JS)
        prefix = prefix_for(page.url, meta.get("siteName", ""))
        stem = stop.prefix + file_name(prefix, clean_headline(meta, prefix))
        w, h = self._box or (None, None)
        page.evaluate(OVERLAY_JS, {"index": index, "total": total, "name": stem + ".png", "w": w, "h": h,
                                   "context": stop.context, "label": stop.label, "note": stop.note})
        return stem

    def _snap(self, page, a: dict) -> bytes:
        cdp = page.context.new_cdp_session(page)
        try:
            # Ask the browser for the real device-pixel ratio (includes screen scaling and page
            # zoom); pages can fake window.devicePixelRatio.
            m = cdp.send("Page.getLayoutMetrics")
            try:
                dpr = m["layoutViewport"]["clientWidth"] / m["cssLayoutViewport"]["clientWidth"]
            except (KeyError, ZeroDivisionError):
                dpr = a.get("dpr") or 1
            scale = max(1.0, OUTPUT_SCALE / dpr)
            shot = cdp.send("Page.captureScreenshot", {
                "format": "png", "fromSurface": True, "captureBeyondViewport": False,
                "clip": {"x": a["x"], "y": a["y"], "width": a["w"], "height": a["h"], "scale": scale},
            })
        finally:
            cdp.detach()
        return base64.b64decode(shot["data"])
