"""Mediatheque.app: start Mediatheque on a Mac like any other app, with no Terminal window.

The app is a small wrapper kept next to the Mediatheque folder's code (it isn't a copy of
it): its launcher does what Mediatheque.command does (first-time setup, new packages, the
browser download), then runs Mediatheque under the app's own name and icon. For that, the
app holds its own copy of the Python program, set up to use Mediatheque's .venv, so the Dock
and macOS's permissions say "Mediatheque" rather than "Python". Output goes to
~/Library/Application Support/Mediatheque/logs/launch.log.

    .venv/bin/python -m mediatheque.macapp          build or refresh the app
    .venv/bin/python -m mediatheque.macapp --check  exit 1 if it needs refreshing

Mediatheque.command builds it, and the app refreshes itself when Python is updated.
"""
from __future__ import annotations

import filecmp
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "Mediatheque.app"
BUNDLE_ID = "com.mediatheque.app"
PYTHON_NAME = "Mediatheque-python"

LAUNCHER = r'''#!/bin/bash
# Mediatheque.app's launcher: what Mediatheque.command does, without a Terminal window.
APP="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(dirname "$APP")"
[ -d "$ROOT/mediatheque" ] || ROOT="$HOME/Mediatheque"   # the app was moved out of the folder
LOGDIR="$HOME/Library/Application Support/Mediatheque/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/launch.log"
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ] && mv -f "$LOG" "$LOG.old"
exec >>"$LOG" 2>&1
echo "===== $(date)"

fail() {
    osascript -e "display alert \"Mediatheque couldn't start\" message \"$1

The details are in Application Support > Mediatheque > logs > launch.log. Mediatheque.command (in the Mediatheque folder) shows them as it starts.\" as critical" >/dev/null
    exit 1
}
note() { osascript -e "display notification \"$1\" with title \"Mediatheque\"" >/dev/null; }

cd "$ROOT" || fail "The Mediatheque folder wasn't found next to the app or in your home folder."
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null  # opened once already: no more warnings for its files

if [ ! -x ".venv/bin/python" ]; then
    note "Setting up for the first time. This takes a few minutes."
    PY="$(command -v python3)"
    [ -n "$PY" ] || fail "Python 3.10 or newer is needed: https://www.python.org/downloads/macos/"
    "$PY" -m venv .venv || fail "Couldn't set up Python."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || fail "Couldn't install Mediatheque's packages."
fi
# Packages added by an update are installed now; otherwise this is quick.
if ! .venv/bin/python -m mediatheque.reqcheck; then
    note "Installing what the update needs (a minute or two)."
    .venv/bin/python -m pip install -r requirements.txt && .venv/bin/python -m mediatheque.reqcheck --mark
fi
if ! .venv/bin/python -c "import sys; from mediatheque.screenshots.naming import own_browser; sys.exit(0 if own_browser() else 1)" 2>/dev/null; then
    note "Downloading Mediatheque's own browser (once, a few minutes)."
    .venv/bin/python -m playwright install chromium || echo "Couldn't download the browser; using Google Chrome."
fi
# Python was updated: refresh the app's copy of it.
.venv/bin/python -m mediatheque.macapp --check || .venv/bin/python -m mediatheque.macapp \
    || fail "Couldn't update Mediatheque.app for the new Python."

exec "$APP/Contents/MacOS/PYTHON_NAME" -m mediatheque
'''.replace("PYTHON_NAME", PYTHON_NAME)


def _venv() -> tuple[Path, Path, str]:
    """The .venv's pyvenv.cfg, the Python program inside python.org's Python.app, and "python3.x"."""
    venv = ROOT / ".venv"
    cfg = venv / "pyvenv.cfg"
    home = next(line.split("=", 1)[1].strip() for line in cfg.read_text().splitlines()
                if line.split("=", 1)[0].strip() == "home")
    src = Path(home).parent / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    lib = next(p.name for p in (venv / "lib").iterdir() if p.name.startswith("python3"))
    return cfg, src, lib


def info_plist(version: str | None = None) -> dict:
    from . import __version__
    version = version or __version__
    return {
        "CFBundleName": "Mediatheque", "CFBundleDisplayName": "Mediatheque",
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": "Mediatheque",
        "CFBundleIconFile": "Mediatheque", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version, "CFBundleVersion": version,
        "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
    }


def needs_refresh() -> bool:
    try:
        cfg, src, lib = _venv()
        contents = APP / "Contents"
        py = contents / "MacOS" / PYTHON_NAME
        return not (py.exists() and (contents / "lib" / lib / "site-packages").exists()
                    and (contents / "MacOS" / "Mediatheque").read_text() == LAUNCHER  # an update changed it
                    and (contents / "pyvenv.cfg").read_text() == cfg.read_text()
                    and (contents / ".python-source").read_text() == f"{src} {src.stat().st_mtime_ns}")
    except (OSError, StopIteration):
        return True


def _icon(resources: Path) -> None:
    """Mediatheque's window icon as the app's icon (Mediatheque.icns)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (needed for pixmaps)
    from .ui.theme import make_icon
    icon = make_icon()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Mediatheque.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = size * scale
                pm = icon.pixmap(QSize(16, 16)).scaled(px, px, Qt.KeepAspectRatio, Qt.FastTransformation)
                pm.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "Mediatheque.icns")],
                       check=True)


def build() -> Path:
    if sys.platform != "darwin":
        raise SystemExit("Mediatheque.app is for a Mac.")
    cfg, src, lib = _venv()
    contents = APP / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    for d in (macos, resources):
        d.mkdir(parents=True, exist_ok=True)

    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info_plist(), f)
    launcher = macos / "Mediatheque"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)

    # The app's own copy of Python: macOS names a program after the app it's in. Re-signed as
    # Mediatheque's (ad hoc, no hardened runtime) so it may load python.org's Python library.
    py = macos / PYTHON_NAME
    if not (py.exists() and filecmp.cmp(src, py, shallow=False)) or needs_refresh():
        tmp = macos / (PYTHON_NAME + ".new")
        shutil.copyfile(src, tmp)
        tmp.chmod(0o755)
        subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(tmp)],
                       check=True, capture_output=True)
        os.replace(tmp, py)
    # It runs as a virtual environment rooted at Contents/, whose packages are the .venv's.
    shutil.copyfile(cfg, contents / "pyvenv.cfg")
    for link, target in ((contents / "lib" / lib / "site-packages", ROOT / ".venv" / "lib" / lib / "site-packages"),
                         (contents / "bin", ROOT / ".venv" / "bin")):
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            if link.is_symlink() and os.readlink(link) == str(target):
                continue
            link.unlink()
        link.symlink_to(target)
    (contents / ".python-source").write_text(f"{src} {src.stat().st_mtime_ns}")

    if not (resources / "Mediatheque.icns").exists():
        try:
            _icon(resources)
        except Exception as e:  # the app works without its own icon
            print(f"Couldn't make the app icon: {e}")
    subprocess.run(["touch", str(APP)])  # Finder and the Dock pick up the icon
    return APP


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(1 if needs_refresh() else 0)
    print(f"Built {build()}")
