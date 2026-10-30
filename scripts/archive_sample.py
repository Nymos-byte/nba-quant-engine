"""
Archives the bet history of a model version and restarts the bankroll, so samples never mix.
Usage: python scripts/archive_sample.py v1 [--bankroll 1000]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

ARCHIVED_PATTERNS = ("picks_*.csv", "settlement_*.done", "notification_*.done", "todas_las_apuestas.csv")


def archive_sample(
    version: str,
    bankroll: float = config.BANKROLL_CORE,
    history_dir: Optional[Path] = None,
    state_file: Optional[Path] = None,
) -> int:
    history_dir = history_dir or config.HISTORY_DIR
    state_file = state_file or config.BANKROLL_STATE_FILE
    target = history_dir / "archive" / version
    if target.exists():
        raise FileExistsError(f"La muestra {version} ya está archivada en {target}")
    target.mkdir(parents=True)

    moved = 0
    for pattern in ARCHIVED_PATTERNS:
        for path in history_dir.glob(pattern):
            shutil.move(str(path), str(target / path.name))
            moved += 1

    if state_file.exists():
        shutil.copy2(state_file, target / state_file.name)
    config.set_current_bankroll(bankroll, state_file=state_file)
    return moved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Archive a model version's bet history")
    parser.add_argument("version", help="Sample name, e.g. v1")
    parser.add_argument("--bankroll", type=float, default=config.BANKROLL_CORE)
    args = parser.parse_args()
    count = archive_sample(args.version, bankroll=args.bankroll)
    print(f"Archivados {count} archivos en data/history/archive/{args.version}. Bankroll: ${args.bankroll:.2f} MXN")
