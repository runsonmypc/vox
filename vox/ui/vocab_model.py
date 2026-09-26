"""What the vocabulary window shows and edits, independent of toolkit.

Every change is written to config.toml at once and read back, so the window
always shows exactly what the daemon will load. The daemon hot-reloads it.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import load_config, snippet_key, update_dictionary, update_snippet


class VocabModel:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.words: list[str] = []
        self.snippets: dict[str, str] = {}
        self.load_error: str | None = None

    @property
    def shown_path(self) -> str:
        return str(self.path).replace(str(Path.home()), "~", 1)

    def reload(self) -> None:
        """Re-read config.toml. On failure, keep the last good lists and set ``load_error``."""
        try:
            config = load_config(self.path)
        except Exception as e:
            self.load_error = str(e)
            return
        self.load_error = None
        self.words = list(config.dictionary)
        self.snippets = dict(config.snippets)

    def add_words(self, text: str) -> list[str]:
        """Add one word, or several separated by commas or new lines. Returns those that were new."""
        words = [w.strip() for w in re.split(r"[,\n]", text) if w.strip()]
        known = {w.lower() for w in self.words}
        if words:
            update_dictionary(self.path, add=words)
            self.reload()
        return [w for w in dict.fromkeys(words) if w.lower() not in known]

    def remove_words(self, words: list[str]) -> None:
        if words:
            update_dictionary(self.path, remove=words)
            self.reload()

    def save_snippet(self, trigger: str, expansion: str, original: str | None = None) -> None:
        """Add a snippet, or replace ``original`` with it when editing (renaming if the trigger changed)."""
        update_snippet(self.path, trigger, expansion)
        if original is not None and snippet_key(original) != snippet_key(trigger):
            update_snippet(self.path, original, None)
        self.reload()

    def remove_snippet(self, trigger: str) -> None:
        update_snippet(self.path, trigger, None)
        self.reload()

    def conflict(self, trigger: str, original: str | None = None) -> str | None:
        """An existing trigger, other than the one being edited, that saving ``trigger`` would replace."""
        key = snippet_key(trigger)
        if not key or (original is not None and snippet_key(original) == key):
            return None
        return next((t for t in self.snippets if snippet_key(t) == key), None)
