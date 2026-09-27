"""Custom vocabulary and snippet editor.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.vocab_window --config PATH``). Every change is written to
config.toml immediately; the running daemon hot-reloads it. The window is
native to each platform: AppKit on macOS, GTK 4 with libadwaita on Linux. Both
draw from ``VocabModel``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH
from .vocab_model import VocabModel


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-vocab", description="Edit Vox Transfer vocabulary and snippets")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.platform == "darwin":
        from .mac.vocab import run
    else:
        from .gtk.vocab import run
    run(VocabModel(args.config))


if __name__ == "__main__":
    main()
