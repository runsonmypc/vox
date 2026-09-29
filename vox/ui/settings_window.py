"""Settings window: General, Hotkey, Transcription, Vocabulary and Snippets.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.settings_window --config PATH [--page NAME]``). Every change is
written to config.toml as it is made, and the API key to the system keychain. The tray
keeps the hotkey from dictating while this window is open, and has the daemon read the
key and the file again the moment it closes. The window is native to each platform:
AppKit on macOS, GTK 4 with libadwaita on Linux. Both draw from the same models.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH
from .hotkey_model import HotkeyModel
from .key_model import KeyModel
from .settings_model import PAGES, SettingsModel
from .vocab_model import VocabModel


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-settings", description="Change Vox Transfer's settings")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    parser.add_argument("--page", choices=PAGES, default="general", help="The page to open on")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.platform == "darwin":
        from .mac.settings import run
    else:
        from .gtk.settings import run
    # The window reads the settings, the key and the devices when it is built: on Linux a second
    # launch only brings the open window forward
    path = args.config
    run(SettingsModel(path), HotkeyModel(path), VocabModel(path), KeyModel(), page=args.page)


if __name__ == "__main__":
    main()
