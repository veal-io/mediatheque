"""Moving Mediatheque to another computer (Windows -> Mac, and back).

Tools > Pack for Another Computer… makes one zip: the app (without its Windows-only Python environment)
plus every saved script project and the Suggest profile, in a `transfer` folder. On the
other computer, the first launch copies those projects in (import_transfer) and renames the
folder so it only happens once. Media (BRoll, Screen Captures…) and the .docx scripts stay
in each video's project folder: copy those separately.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from pathlib import Path

from . import edition
from .config import data_dir

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".git", ".diag", "transfer", "Mediatheque.app", "_macdev"}  # the app is rebuilt on each Mac
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
    info = zipfile.ZipInfo.from_file(src, arcname.as_posix())
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix permissions, so the Mac launcher stays double-clickable
    info.external_attr = (0o100755 if src.name in EXECUTABLE else 0o100644) << 16
    z.writestr(info, src.read_bytes())


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
