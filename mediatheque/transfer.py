"""Moving Mediatheque to another computer (Windows -> Mac, and back), and packing a clean copy
for another editor.

Tools > Pack for Another Computer… makes one zip: the app (without its Windows-only Python environment)
plus every saved script project and the Suggest profile, in a `transfer` folder. On the
other computer, the first launch copies those projects in (import_transfer) and renames the
folder so it only happens once. Media (BRoll, Screen Captures…) and the .docx scripts stay
in each video's project folder: copy those separately.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import edition
from .config import data_dir

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".git", ".diag", "transfer", "Mediatheque.app", "_macdev",  # the app is rebuilt on each Mac
             "update-backup", "update-staging", "dist-editor"}
EXECUTABLE = {"Mediatheque.command"}
log = logging.getLogger("mediatheque")


def pack(dest: Path) -> int:
    """Write the zip. Returns how many script projects went in."""
    projects = sorted((data_dir() / "projects").glob("*.json"))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or any(part in SKIP_DIRS or part.startswith("transfer-imported") for part in rel.parts):
                continue
            _add(z, path, Path("Mediatheque") / rel)
        for p in projects:
            _add(z, p, Path("Mediatheque") / "transfer" / "projects" / p.name)
        profile = data_dir() / "suggest_profile.json"
        if edition.SUGGEST and profile.exists():
            _add(z, profile, Path("Mediatheque") / "transfer" / profile.name)
    return len(projects)


def _add(z: zipfile.ZipFile, src: Path, arcname: Path) -> None:
    _write(z, src.read_bytes(), arcname, src.name in EXECUTABLE, src)


def _write(z: zipfile.ZipFile, data: bytes, arcname: Path, executable: bool = False, src: Path | None = None) -> None:
    info = zipfile.ZipInfo.from_file(src, arcname.as_posix()) if src else \
        zipfile.ZipInfo(arcname.as_posix(), time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix permissions, so the Mac launcher stays double-clickable
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    z.writestr(info, data)


# -- Pack for Another Editor ------------------------------------------------------------------------

# What an editor's copy is made of. Everything else (the owner's notes, the old Credit Generator
# folder and its patron list, .venv, git, transfers) stays out, and so does all of the owner's data,
# which lives outside this folder anyway (projects, settings, Suggest profile, logs, browser profiles).
EDITOR_FILES = ["Mediatheque.command", "Mediatheque.bat", "requirements.txt", "README.md", "EDITOR_SETUP.md"]
EDITOR_DIRS = ["mediatheque"]
NEVER = (".csv", ".pyc", ".DS_Store")


def editor_text(md: str) -> str:
    """README.md as the editor gets it: parts marked <!-- owner -->…<!-- /owner --> (the owner's own
    notes) and <!-- suggest -->…<!-- /suggest --> (Suggest, which the editor's copy doesn't have) are
    taken out."""
    for tag in ("owner", "suggest"):
        md = re.sub(rf"<!-- {tag} -->.*?<!-- /{tag} -->\n?", "", md, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", md)


def editor_zip_name(version: str | None = None) -> str:
    from . import __version__
    return f"Mediatheque-editor-{version or __version__}.zip"


def mac_app_shell(version: str | None = None) -> list[tuple[str, bytes, bool]]:
    """Mediatheque.app as it goes in a package: its launcher, Info.plist and icon, with no paths from
    this computer. On the editor's Mac, the first launch adds the rest (see macapp.py)."""
    import plistlib
    from . import macapp
    files = [("Mediatheque.app/Contents/Info.plist", plistlib.dumps(macapp.info_plist(version)), False),
             ("Mediatheque.app/Contents/MacOS/Mediatheque", macapp.LAUNCHER.encode(), True)]
    icon = ROOT / "Mediatheque.app" / "Contents" / "Resources" / "Mediatheque.icns"
    if icon.exists():
        files.append(("Mediatheque.app/Contents/Resources/Mediatheque.icns", icon.read_bytes(), False))
    return files


def editor_files() -> list[tuple[Path, str]]:
    """(file, path in the package) for the app's own files."""
    out = []
    for name in EDITOR_FILES:
        if (ROOT / name).is_file():
            out.append((ROOT / name, name))
    for d in EDITOR_DIRS:
        for path in sorted((ROOT / d).rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or "__pycache__" in rel.parts or path.name.endswith(NEVER):
                continue
            out.append((path, rel.as_posix()))
    return out


def pack_editor(dest: Path, updates: str = "", version: str | None = None) -> list[str]:
    """Write an editor's copy: the app with Suggest switched off, updating itself from `updates`
    ("owner/repo" on GitHub, or "" for none). Returns the names in the zip, after checking that
    nothing personal got in."""
    edition_json = json.dumps({"suggest": False, "updates": updates} if updates else {"suggest": False}, indent=2)
    tmp = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for src, rel in editor_files():
            data = src.read_bytes()
            if rel.endswith(".md"):
                data = editor_text(data.decode("utf-8")).encode("utf-8")
            _write(z, data, Path("Mediatheque") / rel, src.name in EXECUTABLE, src)
        for rel, data, executable in mac_app_shell(version):
            _write(z, data, Path("Mediatheque") / rel, executable)
        _write(z, (edition_json + "\n").encode(), Path("Mediatheque") / "edition.json")
    names = zipfile.ZipFile(tmp).namelist()
    problems = check_editor_zip(names)
    if problems:
        tmp.unlink()
        raise ValueError("The package would have included: " + ", ".join(problems))
    os.replace(tmp, dest)
    return names


def check_editor_zip(names: list[str]) -> list[str]:
    """Anything in a package that mustn't be there."""
    bad = []
    for n in names:
        parts = n.split("/")
        low = n.lower()
        if (low.endswith(".csv") or "projects" in parts or any(p == "transfer" or p.startswith("transfer-imported") for p in parts[:-1])
                or any(p in (".venv", ".git", "Credit Generator", "update-backup") for p in parts)
                or parts[-1] in ("LESSONS.md", "MAC.md", "EDITOR_EDITION.md", "settings.json",
                                 "suggest_profile.json")
                or parts[-1].endswith((".log", ".pyc"))):
            bad.append(n)
    if "Mediatheque/edition.json" not in names:
        bad.append("(no edition.json)")
    return bad


def import_transfer() -> int:
    """Copy projects from a `transfer` folder (made by Pack for Another Computer) into this computer's
    Mediatheque data, once. A project already here is replaced only by a newer save."""
    folder = ROOT / "transfer"
    if not folder.is_dir():
        return 0
    dest = data_dir() / "projects"
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted((folder / "projects").glob("*.json")):
        target = dest / src.name
        if target.exists() and _updated(target) >= _updated(src):
            continue
        shutil.copy2(src, target)
        count += 1
    profile = folder / "suggest_profile.json"
    if edition.SUGGEST and profile.exists() and not (data_dir() / profile.name).exists():
        shutil.copy2(profile, data_dir() / profile.name)
    folder.rename(ROOT / f"transfer-imported-{time.strftime('%Y%m%d-%H%M%S')}")
    log.info("Imported %d script project(s) from the transfer folder", count)
    return count


def _updated(path: Path) -> float:
    try:
        return float(json.loads(path.read_text(encoding="utf-8")).get("updated") or 0)
    except (OSError, ValueError):
        return 0.0
