"""Shared tkinter helpers for the on-demand windows."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path


def create_root() -> tk.Tk:
    """Create the Tk root, pointing Tcl at the base install when running from a venv.

    Some standalone CPython builds (e.g. uv-managed 3.12.5) cannot locate
    init.tcl from inside a virtualenv. Tcl reads these variables once per
    process, so they must be set before the first interpreter is created.
    """
    base = Path(sys.base_prefix) / "lib"
    for var, name in (("TCL_LIBRARY", "tcl"), ("TK_LIBRARY", "tk")):
        if var in os.environ:
            continue
        lib = base / f"{name}{tk.TclVersion if name == 'tcl' else tk.TkVersion}"
        if lib.is_dir():
            os.environ[var] = str(lib)
    return tk.Tk()


def bring_to_front(root: tk.Tk) -> None:
    root.lift()
    root.focus_force()
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            pass
