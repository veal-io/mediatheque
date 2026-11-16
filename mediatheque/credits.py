"""Credit Generator: turn a Patreon CSV export into three columns of names for
the credits MOGRT in Premiere. Ported from the standalone split_names.py tool."""
from __future__ import annotations

import csv
import math


class CreditsError(Exception):
    pass


def load_names(path: str) -> list[str]:
    """Names from the CSV's "Name" column, in file order, blanks skipped."""
    # utf-8-sig: tolerate the byte-order mark Excel adds, which would otherwise
    # turn the first header into "﻿Name".
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "Name" not in (reader.fieldnames or []):
            raise CreditsError("This CSV has no \"Name\" column. Is it a Patreon members export?")
        return [name for row in reader if (name := (row.get("Name") or "").strip())]


def split_three(names: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split into three columns, filling the first two evenly and the last with the rest."""
    chunk = math.ceil(len(names) / 3)
    return names[:chunk], names[chunk:chunk * 2], names[chunk * 2:]
