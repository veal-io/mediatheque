"""Has requirements.txt changed since its packages were last installed? Used by the launchers,
so an update that adds a package installs it, and one that doesn't skips the slow step.

    python -m mediatheque.reqcheck         exit 0 if up to date, 1 if pip install is needed
    python -m mediatheque.reqcheck --mark  remember the current requirements.txt as installed

Only uses the standard library: it runs before the packages are installed.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQS = ROOT / "requirements.txt"
MARK = ROOT / ".venv" / "requirements.sha"


def current() -> str:
    return hashlib.sha256(REQS.read_bytes()).hexdigest()


def main() -> int:
    if "--mark" in sys.argv:
        MARK.write_text(current() + "\n", encoding="utf-8")
        return 0
    try:
        return 0 if MARK.read_text(encoding="utf-8").strip() == current() else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
