"""OpenAI API key window: paste, replace or remove the key.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.key_window``). The key goes straight into the system
keychain, and the tray has the daemon re-read it when this window closes. The
window is native to each platform: AppKit on macOS, GTK 4 with libadwaita on
Linux. Both draw from ``KeyModel``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .key_model import KeyModel


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-key", description="Set the OpenAI API key Vox Transfer uses")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if sys.platform == "darwin":
        from .mac.key import run
    else:
        from .gtk.key import run
    # The window reads the keyring when it is built: on Linux a second launch only brings the open window forward
    run(KeyModel())


if __name__ == "__main__":
    main()
