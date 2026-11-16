"""Updates through GitHub.

The owner publishes with Tools > Publish Update… (ui/publish.py): a GitHub release tagged
v1.2.0 with the editor's package attached as Mediatheque-editor-1.2.0.zip.

An editor's copy names that repository in its edition.json and asks GitHub's public API
for the latest release (no sign-in needed). Installing:

1. Download the zip and check its SHA-256 against the digest GitHub reports for the asset
   (or a .sha256 file published beside it). No match, no install.
2. Unpack it into update-staging/ and check it's a complete Mediatheque.
3. Move the current version's files into update-backup/ (one version back) and the new
   ones into place: the whole mediatheque/ package, the launchers, Mediatheque.app's
   launcher, Info.plist and icon, requirements.txt, edition.json and the docs.
   .venv and the rest of Mediatheque.app (its copy of Python) stay. If any step fails,
   everything is put back as it was.

Nothing in ~/Library/Application Support/Mediatheque (projects, settings, logs) is touched.
The owner's own copy (no edition.json, or a git folder) never installs an update.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, edition

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "update-backup"
STAGING = ROOT / "update-staging"
API = "https://api.github.com/repos/{repo}/releases/latest"
CHECK_EVERY = 24 * 3600  # automatic checks: at most once a day
EXECUTABLE = {"Mediatheque.command", "Mediatheque.app/Contents/MacOS/Mediatheque"}  # as the packer marks them

# What an update replaces. Everything else in the folder (.venv, Mediatheque.app's copy of Python)
# stays as it is.
REPLACED_DIRS = ["mediatheque"]
REPLACED_FILES = ["Mediatheque.command", "Mediatheque.bat", "requirements.txt", "edition.json", "README.md",
                  "EDITOR_SETUP.md", "Mediatheque.app/Contents/Info.plist",
                  "Mediatheque.app/Contents/MacOS/Mediatheque", "Mediatheque.app/Contents/Resources/Mediatheque.icns"]

log = logging.getLogger("mediatheque")


class UpdateError(Exception):
    """Something went wrong, said in plain English."""


# -- which repository -----------------------------------------------------------------------------

def repo_from_git() -> str:
    """"owner/repo" from this folder's GitHub remote, or ""."""
    try:
        url = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else ""


def update_repo(settings=None) -> str:
    """The repository editors' copies update from: the editor's own edition.json, else the one
    Publish Update set up (saved in settings), else this folder's GitHub remote."""
    if edition.UPDATES:
        return edition.UPDATES
    if settings is not None and (repo := settings.data.get("publish", {}).get("repo")):
        return repo
    return repo_from_git()


def can_update() -> bool:
    """Only an editor's copy updates itself: never the owner's (no edition.json, or a git folder)."""
    return bool(edition.EDITOR and edition.UPDATES) and not (ROOT / ".git").exists()


# -- versions ---------------------------------------------------------------------------------------

def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def next_version(v: str = __version__) -> str:
    parts = list(parse_version(v)) + [0, 0]
    major, minor, patch = parts[:3]
    return f"{major}.{minor}.{patch + 1}"


def installed_version(root: Path = ROOT) -> str:
    """The version in a copy's mediatheque/__init__.py (read from the file, not the running code)."""
    try:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (root / "mediatheque" / "__init__.py").read_text("utf-8"))
        return m.group(1) if m else "0"
    except OSError:
        return "0"


# -- asking GitHub ----------------------------------------------------------------------------------

@dataclass
class Release:
    version: str
    tag: str
    notes: str
    url: str          # the release's page
    asset_url: str    # the zip
    asset_name: str
    size: int
    sha256: str       # expected, from GitHub's digest or the .sha256 file ("" if neither)


def _context() -> ssl.SSLContext:
    try:
        import certifi  # python.org's Mac Python has no certificates of its own until Install Certificates
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, timeout: float = 20, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Mediatheque/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No published version was found. (The update page may not have any releases yet.)")
        if e.code in (403, 429):
            raise UpdateError("GitHub is busy or limiting requests right now. Try again in an hour.")
        raise UpdateError(f"GitHub answered with an error ({e.code}). Try again later.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("Couldn't reach GitHub. Check the internet connection and try again.") from e


def latest_release(repo: str) -> Release:
    try:
        data = json.loads(_get(API.format(repo=repo)))
    except ValueError as e:
        raise UpdateError("GitHub's answer couldn't be read. Try again later.") from e
    tag = data.get("tag_name") or ""
    assets = data.get("assets") or []
    zip_asset = next((a for a in assets if re.fullmatch(r"Mediatheque-editor-.*\.zip", a.get("name", ""))), None)
    if not zip_asset:
        raise UpdateError(f"The latest version ({tag}) has no Mediatheque package attached.")
    sha = ""
    digest = zip_asset.get("digest") or ""
    if digest.startswith("sha256:"):
        sha = digest.split(":", 1)[1].lower()
    else:
        side = next((a for a in assets if a.get("name") == zip_asset["name"] + ".sha256"), None)
        if side:
            text = _get(side["browser_download_url"], accept="application/octet-stream").decode("utf-8", "replace")
            sha = (re.findall(r"\b[0-9a-fA-F]{64}\b", text) or [""])[0].lower()
    return Release(version=tag.lstrip("vV"), tag=tag, notes=(data.get("body") or "").strip(),
                   url=data.get("html_url") or "", asset_url=zip_asset["browser_download_url"],
                   asset_name=zip_asset["name"], size=int(zip_asset.get("size") or 0), sha256=sha)


def due_for_check(settings) -> bool:
    last = float(settings.data.get("updates", {}).get("last_check") or 0)
    return time.time() - last > CHECK_EVERY


def mark_checked(settings) -> None:
    settings.data.setdefault("updates", {})["last_check"] = time.time()
    settings.save()


# -- downloading and checking ------------------------------------------------------------------------

def download(rel: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the release's zip into update-staging/ and check its SHA-256."""
    if not rel.sha256:
        raise UpdateError("This version was published without a checksum, so it can't be checked. "
                          "Ask for it to be published again.")
    STAGING.mkdir(exist_ok=True)
    dest = STAGING / rel.asset_name
    req = urllib.request.Request(rel.asset_url, headers={"Accept": "application/octet-stream",
                                                         "User-Agent": f"Mediatheque/{__version__}"})
    h = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=60, context=_context()) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or rel.size or 0)
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download didn't finish. Check the internet connection and try again.") from e
    if h.hexdigest() != rel.sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download didn't match its checksum (it may have been damaged on the way), so it "
                          "wasn't installed. Nothing has changed. Try again later.")
    return dest


# -- installing ---------------------------------------------------------------------------------------

def _unpack(zip_path: Path) -> Path:
    """Unpack into update-staging/new and check it's a whole Mediatheque. Returns its folder."""
    out = STAGING / "new"
    shutil.rmtree(out, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename
                if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                    raise UpdateError("The package has a file in an unexpected place, so it wasn't installed.")
            z.extractall(out)
    except zipfile.BadZipFile as e:
        raise UpdateError("The package is damaged, so it wasn't installed. Nothing has changed.") from e
    new = out / "Mediatheque"
    for must in ("mediatheque/__init__.py", "mediatheque/__main__.py", "Mediatheque.command", "requirements.txt",
                 "edition.json"):
        if not (new / must).is_file():
            raise UpdateError("The package is incomplete, so it wasn't installed. Nothing has changed.")
    try:
        if not json.loads((new / "edition.json").read_text("utf-8")).get("updates"):
            raise UpdateError("The package isn't an editor's copy, so it wasn't installed.")
    except ValueError as e:
        raise UpdateError("The package's edition.json is damaged, so it wasn't installed.") from e
    return new


def _items(root: Path) -> list[str]:
    return [d for d in REPLACED_DIRS if (root / d).is_dir()] + [f for f in REPLACED_FILES if (root / f).is_file()]


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _set_modes(root: Path, names: list[str]) -> None:
    """The launchers stay double-clickable (zip extraction in Python drops the executable bit)."""
    for name in names:
        p = root / name
        if p.is_file():
            p.chmod(0o755 if name in EXECUTABLE else 0o644)


def _swap(incoming: Path, outgoing: Path) -> list[str]:
    """Move the current files to `outgoing`, then `incoming`'s into place. On any failure, put
    everything back and raise. Returns what was installed."""
    old_items = _items(ROOT)
    new_items = _items(incoming)
    moved_out: list[str] = []
    moved_in: list[str] = []
    try:
        for name in old_items:
            _move(ROOT / name, outgoing / name)
            moved_out.append(name)
        for name in new_items:
            _move(incoming / name, ROOT / name)
            moved_in.append(name)
        _set_modes(ROOT, [n for n in new_items if n in REPLACED_FILES])
    except OSError as e:
        for name in reversed(moved_in):  # put it all back as it was
            try:
                _move(ROOT / name, incoming / name)
            except OSError:
                log.exception("Couldn't take back %s", name)
        for name in reversed(moved_out):
            try:
                _move(outgoing / name, ROOT / name)
            except OSError:
                log.exception("Couldn't restore %s", name)
        raise UpdateError(f"The update couldn't be installed ({e.strerror or e}), so the current version was left "
                          "as it was.") from e
    return new_items


def install(zip_path: Path) -> str:
    """Install a downloaded, checked package. Returns the new version."""
    if not can_update():
        raise UpdateError("This copy of Mediatheque doesn't update itself.")
    new = _unpack(zip_path)
    version = installed_version(new)
    previous = installed_version()
    old_backup = STAGING / "old-backup"
    shutil.rmtree(old_backup, ignore_errors=True)
    if BACKUP.exists():
        os.replace(BACKUP, old_backup)  # kept until the new backup is complete
    BACKUP.mkdir()
    try:
        _swap(new, BACKUP)
    except UpdateError:
        shutil.rmtree(BACKUP, ignore_errors=True)
        if old_backup.exists():
            os.replace(old_backup, BACKUP)
        raise
    (BACKUP / "version.txt").write_text(previous + "\n", encoding="utf-8")
    shutil.rmtree(STAGING, ignore_errors=True)
    log.info("Updated Mediatheque %s -> %s", previous, version)
    return version


def backup_version() -> str:
    """The version Undo Last Update goes back to, or "" if there's none."""
    if not (BACKUP / "mediatheque" / "__init__.py").is_file():
        return ""
    return installed_version(BACKUP)


def undo() -> str:
    """Put the previous version back. Returns its version."""
    if not can_update():
        raise UpdateError("This copy of Mediatheque doesn't update itself.")
    previous = backup_version()
    if not previous:
        raise UpdateError("There's no earlier version to go back to.")
    replaced = STAGING / "replaced"
    shutil.rmtree(STAGING, ignore_errors=True)
    replaced.mkdir(parents=True)
    _swap(BACKUP, replaced)
    shutil.rmtree(BACKUP, ignore_errors=True)
    shutil.rmtree(STAGING, ignore_errors=True)
    log.info("Undid the last update: back to Mediatheque %s", previous)
    return previous


# -- restarting -------------------------------------------------------------------------------------

def restart_command() -> list[str]:
    """How to start Mediatheque again, the way it's normally started: through Mediatheque.app on a Mac
    (a moment after this copy has quit), else the same Python."""
    import sys
    app = ROOT / "Mediatheque.app"
    if sys.platform == "darwin" and (app / "Contents" / "MacOS" / "Mediatheque").exists():
        return ["/bin/sh", "-c", 'sleep 2; open -n "$0"', str(app)]
    if sys.platform == "darwin":
        return ["/bin/sh", "-c", 'sleep 2; open "$0"', str(ROOT / "Mediatheque.command")]
    return [sys.executable, "-m", "mediatheque"]
