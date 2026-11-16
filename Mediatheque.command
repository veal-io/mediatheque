#!/bin/bash
# Launches Mediatheque on a Mac (double-click in Finder). On first run, sets up its private
# Python environment. Keep this window open while Mediatheque runs; closing it quits the app.
cd "$(dirname "$0")" || exit 1

# A downloaded copy is quarantined by macOS. Once you've chosen to open this file, clear that for the
# whole Mediatheque folder, so Mediatheque.app (and later updates) open without the warning.
xattr -dr com.apple.quarantine . 2>/dev/null

if [ ! -x ".venv/bin/python" ]; then
    echo "Setting up Mediatheque for the first time. This takes a few minutes..."
    PY="$(command -v python3)"
    if [ -z "$PY" ]; then
        echo "Python 3.10 or newer is needed: https://www.python.org/downloads/macos/"
        read -n 1 -s -r -p "Press any key to close."
        exit 1
    fi
    "$PY" -m venv .venv || { read -n 1 -s -r -p "Couldn't set up Python. Press any key to close."; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { read -n 1 -s -r -p "Press any key to close."; exit 1; }
fi

# Packages added since the first setup are installed on the next launch.
.venv/bin/python -c "import yt_dlp, PySide6" 2>/dev/null || .venv/bin/python -m pip install -r requirements.txt

# Mediatheque's own browser (Chrome for Testing), a separate app from Google Chrome, so the
# browser it runs in the background never stops your Chrome from opening. Downloaded once.
if ! .venv/bin/python -c "import sys; from mediatheque.screenshots.naming import own_browser; sys.exit(0 if own_browser() else 1)" 2>/dev/null; then
    echo "Downloading Mediatheque's own browser (once, a few minutes)..."
    .venv/bin/python -m playwright install chromium || echo "Couldn't download it; Mediatheque will use Google Chrome for now."
fi

# Mediatheque.app: the same thing as a normal app, with no Terminal window. Made (or refreshed
# after a Python update) here; after that, open Mediatheque with it.
.venv/bin/python -m mediatheque.macapp --check 2>/dev/null || .venv/bin/python -m mediatheque.macapp

exec .venv/bin/python -m mediatheque
