"""Dictation history window: search past dictations and copy one back.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.history_window --db PATH``) and exits when closed. The
window is native to each platform: AppKit on macOS, GTK 4 with libadwaita on
Linux. Both draw from ``HistoryModel``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..history import HistoryDB


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-history", description="Search past Vox Transfer dictations")
    parser.add_argument("--db", type=Path, default=None, help="Path to history.db")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.platform == "darwin":
        from .mac.history import run
    else:
        from .gtk.history import run
    db = HistoryDB(args.db)
    try:
        from ..recovery import RecoveryStore

        RecoveryStore(db).reconcile()
        run(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
