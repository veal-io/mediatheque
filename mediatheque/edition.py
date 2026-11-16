"""Which edition this copy of Mediatheque is.

The owner's copy has everything. An editor's copy has an `edition.json` next to
Mediatheque.command, made by Tools > Pack for Another Editor…:

    {"suggest": false, "updates": "owner/repo"}

    suggest   False hides Suggest (Claude) everywhere, and the anthropic and keyring
              packages aren't loaded at all.
    updates   The GitHub repository this copy updates itself from (Help > Check for Updates).

With no edition.json (the owner's copy), Suggest is on and the copy never updates itself.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "edition.json"


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logging.getLogger("mediatheque").warning("Couldn't read %s; using the full edition", FILE)
        return {}


_data = _load()

EDITOR: bool = FILE.exists()                     # an editor's copy (made by Pack for Another Editor)
SUGGEST: bool = bool(_data.get("suggest", True))  # Suggest (Claude) on highlights
UPDATES: str = str(_data.get("updates") or "")    # "owner/repo" on GitHub, or "" for no updates
