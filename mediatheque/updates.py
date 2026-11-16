"""Updates through GitHub: where an editor's copy updates from.

The owner publishes with Tools > Publish Update… (ui/publish.py). An editor's copy names the
repository in its edition.json and checks it for new releases.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import edition

ROOT = Path(__file__).resolve().parent.parent


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
