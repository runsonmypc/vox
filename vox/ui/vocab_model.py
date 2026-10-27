"""What the vocabulary window shows and edits, independent of toolkit.

Every change is written to config.toml at once and read back, so the window
always shows exactly what the daemon will load. The daemon hot-reloads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import load_config, snippet_key, update_dictionary, update_replacement, update_snippet
from ..errors import ConfigError

# What both windows say, so macOS and Linux never drift apart
WORDS_INTRO = "Names, jargon and acronyms Vox Transfer should always spell exactly as written."
SNIPPETS_INTRO = "Say a trigger phrase on its own and Vox Transfer types the expansion instead."
REPLACEMENTS_INTRO = ("When a sentence starts with a phrase, Vox Transfer writes your replacement instead. "
                      "The same words mid-sentence are left alone.")
NO_WORDS = ("No Words Yet", "Add names and terms Vox Transfer tends to get wrong.")
NO_SNIPPETS = ("No Snippets Yet", "Type an address, a sign-off or a link just by saying a short phrase.")
NO_REPLACEMENTS = ("No Replacements Yet", "Fix how a sentence keeps starting, like “all right” to “alright”.")
LOAD_FAILED_TITLE = "Couldn’t Read Your Settings"
SAVE_FAILED_TITLE = "Couldn’t Save"


@dataclass(frozen=True)
class PhraseKind:
    """Snippets or replacements: phrase lists edited the same way, with their own page and words."""

    page: str  # the page name, and the config.toml table
    noun: str
    intro: str
    empty: tuple[str, str]
    say: str  # the phrase field's label
    write: str  # the text field's label
    example: str
    removed: str  # what deleting one means for dictation


SNIPPETS = PhraseKind("snippets", "Snippet", SNIPPETS_INTRO, NO_SNIPPETS, "When you say", "Vox Transfer types",
                      "my email", "Vox Transfer will stop expanding this phrase.")
REPLACEMENTS = PhraseKind("replacements", "Replacement", REPLACEMENTS_INTRO, NO_REPLACEMENTS,
                          "When a sentence starts with", "Vox Transfer writes", "all right",
                          "Vox Transfer will stop replacing this phrase.")
_UPDATE = {SNIPPETS: update_snippet, REPLACEMENTS: update_replacement}


def clash_warning(clash: str, kind: PhraseKind = SNIPPETS) -> str:
    return f"This replaces your “{clash}” {kind.noun.lower()}."


class VocabModel:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.words: list[str] = []
        self.snippets: dict[str, str] = {}
        self.replacements: dict[str, str] = {}
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
        self.replacements = dict(config.replacements)

    def phrases(self, kind: PhraseKind) -> dict[str, str]:
        return self.snippets if kind is SNIPPETS else self.replacements

    def _check_writable(self) -> None:
        # A file that parses but fails validation leaves the lists empty or stale, so a write could
        # silently replace a snippet the window never showed
        if self.load_error is not None:
            raise ConfigError(self.load_error)

    def add_words(self, text: str) -> list[str]:
        """Add one word, or several separated by commas or new lines. Returns those that were new."""
        self._check_writable()
        words = [w.strip() for w in re.split(r"[,\n]", text) if w.strip()]
        known = {w.lower() for w in self.words}
        if words:
            update_dictionary(self.path, add=words)
            self.reload()
        return [w for w in dict.fromkeys(words) if w.lower() not in known]

    def remove_words(self, words: list[str]) -> None:
        self._check_writable()
        if words:
            update_dictionary(self.path, remove=words)
            self.reload()

    def save_snippet(self, trigger: str, expansion: str, original: str | None = None,
                     kind: PhraseKind = SNIPPETS) -> None:
        """Add a snippet or replacement, or replace ``original`` with it when editing (renaming if the trigger changed)."""
        self._check_writable()
        update = _UPDATE[kind]
        update(self.path, trigger, expansion)
        if original is not None and snippet_key(original) != snippet_key(trigger):
            update(self.path, original, None)
        self.reload()

    def remove_snippet(self, trigger: str, kind: PhraseKind = SNIPPETS) -> None:
        self._check_writable()
        _UPDATE[kind](self.path, trigger, None)
        self.reload()

    def conflict(self, trigger: str, original: str | None = None, kind: PhraseKind = SNIPPETS) -> str | None:
        """An existing trigger, other than the one being edited, that saving ``trigger`` would replace."""
        key = snippet_key(trigger)
        if not key or (original is not None and snippet_key(original) == key):
            return None
        return next((t for t in self.phrases(kind) if snippet_key(t) == key), None)
