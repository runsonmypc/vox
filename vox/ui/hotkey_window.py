"""Hotkey window: record the key that starts and stops dictation, and an optional key combination.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.hotkey_window --config PATH``). Save writes ``[hotkey]`` to
config.toml; the tray keeps the hotkey from dictating while this window is open,
and has the daemon apply the new one when it closes. The window is native to
each platform: AppKit on macOS, GTK 4 with libadwaita on Linux. Both draw from
``HotkeyModel``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH
from .hotkey_model import HotkeyModel


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-hotkey", description="Choose the hotkey Vox Transfer listens for")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.platform == "darwin":
        from .mac.hotkey import run
    else:
        from .gtk.hotkey import run
    # The window reads the settings when it is built: on Linux a second launch only brings the open window forward
    run(HotkeyModel(args.config))


if __name__ == "__main__":
    main()
