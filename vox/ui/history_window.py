"""Dictation history search window.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.history_window --db PATH``) and exits when closed.
"""

from __future__ import annotations

import argparse
import logging
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from typing import Callable

from ..history import HistoryDB, HistoryRecord
from .tkutil import bring_to_front, create_root

log = logging.getLogger(__name__)

_SEARCH_DEBOUNCE_MS = 120
_PREVIEW_CHARS = 90
_COPY_COLUMN = "#4"


def _default_copy(text: str) -> None:
    from ..injector import set_clipboard

    set_clipboard(text)


def local_time(created_at: str) -> str:
    """Format a UTC SQLite timestamp in local time."""
    try:
        utc = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return created_at or ""
    return utc.astimezone().strftime("%Y-%m-%d %H:%M")


def preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[: _PREVIEW_CHARS - 1].rstrip() + "…"


class HistoryWindow:
    def __init__(self, root: tk.Tk, db: HistoryDB, copy: Callable[[str], None] = _default_copy) -> None:
        self._root = root
        self._db = db
        self._copy = copy
        self._records: dict[str, HistoryRecord] = {}
        self._pending: str | None = None

        root.title("Vox History")
        root.geometry("680x440")
        root.minsize(420, 260)

        # Pack the bottom bar first so the expanding list cannot squeeze it out
        bottom = ttk.Frame(root, padding=(10, 0, 10, 10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.detail = tk.Text(bottom, height=4, wrap=tk.WORD, font="TkDefaultFont", state=tk.DISABLED)
        self.detail.pack(fill=tk.X)
        self.status = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status).pack(side=tk.LEFT, pady=(6, 0))
        ttk.Button(bottom, text="Copy", command=self.copy_selected).pack(side=tk.RIGHT, pady=(6, 0))

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        self.query = tk.StringVar()
        search_row = ttk.Frame(frame)
        search_row.pack(fill=tk.X)
        ttk.Label(search_row, text="Search").pack(side=tk.LEFT, padx=(0, 8))
        search = ttk.Entry(search_row, textvariable=self.query)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search.focus_set()
        self.query.trace_add("write", lambda *_: self._schedule_refresh())

        self.tree = ttk.Treeview(frame, columns=("when", "app", "text", "copy"), show="headings", selectmode="browse")
        for col, heading, width, stretch in (
            ("when", "When", 130, False),
            ("app", "App", 80, False),
            ("text", "Dictation", 360, True),
            ("copy", "", 60, False),
        ):
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, stretch=stretch, anchor=tk.CENTER if col == "copy" else tk.W)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(8, 0))
        scroll.pack(side=tk.LEFT, fill=tk.Y, pady=(8, 0))

        self.tree.bind("<ButtonRelease-1>", self._on_click)
        self.tree.bind("<Double-1>", lambda _e: self.copy_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        root.bind("<Return>", lambda _e: self.copy_selected())
        root.bind("<Escape>", lambda _e: root.destroy())
        search.bind("<Down>", lambda _e: self._focus_results())

        self.refresh()

    def refresh(self) -> None:
        """Re-run the search for the current query, newest first."""
        self._pending = None
        records = self._db.search(self.query.get())
        self.tree.delete(*self.tree.get_children())
        self._records = {}
        for rec in records:
            iid = str(rec.id)
            self._records[iid] = rec
            self.tree.insert("", tk.END, iid=iid, values=(local_time(rec.created_at), rec.app_type or "", preview(rec.text), "Copy"))
        if records:
            self.tree.selection_set(str(records[0].id))
        self.status.set(f"{len(records)} dictation{'s' if len(records) != 1 else ''}")
        self._show_detail()

    def visible_texts(self) -> list[str]:
        return [self._records[iid].text for iid in self.tree.get_children()]

    def copy_record(self, iid: str) -> None:
        rec = self._records.get(iid)
        if rec is None:
            return
        try:
            self._copy(rec.text)
        except Exception as e:
            log.warning("Copy failed: %s", e)
            self.status.set(f"Copy failed: {e}")
            return
        self.status.set(f"Copied {len(rec.text)} characters")

    def copy_selected(self) -> None:
        selection = self.tree.selection()
        if selection:
            self.copy_record(selection[0])

    def _on_click(self, event: tk.Event) -> None:
        # One click on a row's "Copy" cell copies that row
        if self.tree.identify_column(event.x) == _COPY_COLUMN:
            iid = self.tree.identify_row(event.y)
            if iid:
                self.tree.selection_set(iid)
                self.copy_record(iid)

    def _schedule_refresh(self) -> None:
        if self._pending is not None:
            self._root.after_cancel(self._pending)
        self._pending = self._root.after(_SEARCH_DEBOUNCE_MS, self.refresh)

    def _show_detail(self) -> None:
        selection = self.tree.selection()
        text = self._records[selection[0]].text if selection and selection[0] in self._records else ""
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", text)
        self.detail.configure(state=tk.DISABLED)

    def _focus_results(self) -> None:
        self.tree.focus_set()
        selection = self.tree.selection()
        if selection:
            self.tree.focus(selection[0])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-history", description="Search past Vox dictations")
    parser.add_argument("--db", type=Path, default=None, help="Path to history.db")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    db = HistoryDB(args.db)
    root = create_root()
    try:
        HistoryWindow(root, db)
        bring_to_front(root)
        root.mainloop()
    finally:
        db.close()


if __name__ == "__main__":
    main()
