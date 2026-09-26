"""Custom vocabulary and snippet editor.

Launched on demand from the menu bar as its own process
(``python -m vox.ui.vocab_window --config PATH``). Every change is written to
config.toml immediately; the running daemon hot-reloads it.
"""

from __future__ import annotations

import argparse
import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..config import DEFAULT_CONFIG_PATH, load_config, update_dictionary, update_snippet
from .tkutil import bring_to_front, create_root

log = logging.getLogger(__name__)


class VocabWindow:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self._root = root
        self._path = config_path
        self._snippet_map: dict[str, str] = {}

        root.title("Vox Vocabulary & Snippets")
        root.geometry("620x440")
        root.minsize(460, 320)

        # Pack the footer first so the expanding notebook cannot squeeze it out
        footer = ttk.Frame(root, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.status = tk.StringVar()
        ttk.Label(footer, textvariable=self.status).pack(side=tk.LEFT)
        shown_path = str(config_path).replace(str(Path.home()), "~", 1)
        ttk.Label(footer, text=shown_path, foreground="gray").pack(side=tk.RIGHT)

        notebook = ttk.Notebook(root, padding=10)
        notebook.pack(fill=tk.BOTH, expand=True)
        notebook.add(self._build_vocab_tab(notebook), text="Vocabulary")
        notebook.add(self._build_snippet_tab(notebook), text="Snippets")

        root.bind("<Escape>", lambda _e: root.destroy())
        self.reload()

    # -- Layout ------------------------------------------------------------

    def _build_vocab_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        ttk.Label(tab, text="Words and names Vox should spell exactly as written.").pack(anchor=tk.W)

        add_row = ttk.Frame(tab)
        add_row.pack(fill=tk.X, pady=(8, 8))
        self.word = tk.StringVar()
        entry = ttk.Entry(add_row, textvariable=self.word)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind("<Return>", lambda _e: self.add_word())
        ttk.Button(add_row, text="Add", command=self.add_word).pack(side=tk.LEFT, padx=(8, 0))

        self.words = tk.Listbox(tab, selectmode=tk.EXTENDED, activestyle="none")
        self.words.pack(fill=tk.BOTH, expand=True)
        self.words.bind("<BackSpace>", lambda _e: self.remove_selected_words())
        self.words.bind("<Delete>", lambda _e: self.remove_selected_words())
        ttk.Button(tab, text="Remove Selected", command=self.remove_selected_words).pack(anchor=tk.E, pady=(8, 0))
        return tab

    def _build_snippet_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        ttk.Label(tab, text="Say a trigger phrase on its own to paste the expansion instead.").pack(anchor=tk.W)

        form = ttk.Frame(tab)
        form.pack(fill=tk.X, pady=(8, 8))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Trigger").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.trigger = tk.StringVar()
        ttk.Entry(form, textvariable=self.trigger).grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(form, text="Expansion").grid(row=1, column=0, sticky=tk.NW, padx=(0, 8), pady=(6, 0))
        self.expansion = tk.Text(form, height=3, wrap=tk.WORD, font="TkDefaultFont")
        self.expansion.grid(row=1, column=1, sticky=tk.EW, pady=(6, 0))
        ttk.Button(form, text="Save Snippet", command=self.save_snippet).grid(row=2, column=1, sticky=tk.E, pady=(6, 0))

        self.snippets = ttk.Treeview(tab, columns=("trigger", "expansion"), show="headings", selectmode="extended")
        self.snippets.heading("trigger", text="Trigger")
        self.snippets.heading("expansion", text="Expansion")
        self.snippets.column("trigger", width=160, stretch=False)
        self.snippets.pack(fill=tk.BOTH, expand=True)
        self.snippets.bind("<<TreeviewSelect>>", lambda _e: self._edit_selected_snippet())
        self.snippets.bind("<BackSpace>", lambda _e: self.remove_selected_snippets())
        self.snippets.bind("<Delete>", lambda _e: self.remove_selected_snippets())
        ttk.Button(tab, text="Remove Selected", command=self.remove_selected_snippets).pack(anchor=tk.E, pady=(8, 0))
        return tab

    # -- Data --------------------------------------------------------------

    def reload(self) -> None:
        """Show the dictionary and snippets exactly as the daemon will load them."""
        try:
            config = load_config(self._path)
        except Exception as e:
            self.status.set(f"Could not read config: {e}")
            return
        self._snippet_map = dict(config.snippets)
        self.words.delete(0, tk.END)
        for word in config.dictionary:
            self.words.insert(tk.END, word)
        self.snippets.delete(*self.snippets.get_children())
        for trigger, expansion in config.snippets.items():
            self.snippets.insert("", tk.END, iid=trigger, values=(trigger, " ".join(expansion.split())))

    def add_word(self) -> None:
        word = self.word.get().strip()
        if not word:
            return
        if self._save(lambda: update_dictionary(self._path, add=[word]), f"Added “{word}”"):
            self.word.set("")

    def remove_selected_words(self) -> None:
        selected = [self.words.get(i) for i in self.words.curselection()]
        if selected:
            self._save(lambda: update_dictionary(self._path, remove=selected), f"Removed {len(selected)} word(s)")

    def save_snippet(self) -> None:
        trigger = self.trigger.get().strip()
        expansion = self.expansion.get("1.0", "end-1c")
        if self._save(lambda: update_snippet(self._path, trigger, expansion), f"Saved “{trigger}”"):
            self.trigger.set("")
            self.expansion.delete("1.0", tk.END)

    def remove_selected_snippets(self) -> None:
        selected = list(self.snippets.selection())
        if not selected:
            return

        def remove() -> None:
            for trigger in selected:
                update_snippet(self._path, trigger, None)

        self._save(remove, f"Removed {len(selected)} snippet(s)")

    def _edit_selected_snippet(self) -> None:
        selection = self.snippets.selection()
        if len(selection) != 1:
            return
        trigger = selection[0]
        self.trigger.set(trigger)
        self.expansion.delete("1.0", tk.END)
        self.expansion.insert("1.0", self._snippet_map.get(trigger, ""))

    def _save(self, write, message: str) -> bool:
        try:
            write()
        except Exception as e:
            log.warning("Config update failed: %s", e)
            self.status.set(f"Not saved: {e}")
            return False
        self.reload()
        self.status.set(f"{message}. Vox picks this up within a few seconds.")
        return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vox-vocab", description="Edit Vox vocabulary and snippets")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = create_root()
    VocabWindow(root, args.config)
    bring_to_front(root)
    root.mainloop()


if __name__ == "__main__":
    main()
