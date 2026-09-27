"""What the API key window shows and does, independent of toolkit.

The key goes straight from the window into the keychain through ``vox.keystore``:
never through the daemon, a pipe, argv or the environment. Once saved, the
window shows only its last four characters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .. import keystore

KEYS_URL = "https://platform.openai.com/api-keys"

# What both windows say, so macOS and Linux never drift apart
INTRO = "Vox sends your dictation to OpenAI to transcribe it, using your own API key."
CHECKING = "Checking with OpenAI…"
CHECK_FAILED_TITLE = "Couldn’t Check the Key"
SAVE_ANYWAY = "Save Anyway"
SAVE_FAILED_TITLE = "Couldn’t Save the Key"
REMOVE_TITLE = "Remove the Saved Key?"
REMOVE_MESSAGE = "Vox can’t transcribe with OpenAI until you save a key again."
REMOVE_BUTTON = "Remove"
REMOVE_FAILED_TITLE = "Couldn’t Remove the Key"

# "Check with OpenAI before saving" starts ticked, so a mistyped or revoked key is caught before it is saved
CHECK_BY_DEFAULT = True


class Outcome(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCHECKED = "unchecked"  # OpenAI couldn't say either way


@dataclass(frozen=True)
class CheckResult:
    outcome: Outcome
    message: str = ""

    @property
    def save_anyway_question(self) -> str:
        return f"{self.message} Save it anyway?"


class KeyModel:
    def __init__(self) -> None:
        self.saved_suffix: str | None = None
        self.read_error: str | None = None

    def reload(self) -> None:
        """Re-read the saved key, keeping only its last four characters."""
        try:
            key = keystore.get_stored_key()
        except keystore.KeystoreError as e:
            self.saved_suffix, self.read_error = None, str(e)
            return
        self.read_error = None
        self.saved_suffix = key[-4:] if key else None

    @property
    def has_saved_key(self) -> bool:
        return self.saved_suffix is not None

    @property
    def encrypted(self) -> bool:
        return keystore.storage_name() is not None

    @property
    def storage_text(self) -> str:
        name = keystore.storage_name()
        if name is not None:
            return f"Vox keeps your key in {name}, which encrypts it."
        return (
            "No keyring is running, so Vox keeps your key without encryption in "
            f"{_shown(keystore.FALLBACK_PATH)}, readable only by you."
        )

    @property
    def saved_text(self) -> str:
        if self.read_error:
            return f"Couldn’t read the saved key: {self.read_error}"
        if self.saved_suffix:
            return f"A key ending in {self.saved_suffix} is saved."
        return "No key saved yet."

    @property
    def override_text(self) -> str | None:
        if keystore.env_override():
            return "OPENAI_API_KEY is set in Vox’s environment, so Vox uses it instead of the saved key."
        return None

    @staticmethod
    def problem(key: str) -> str | None:
        """Why ``key`` can't be saved as typed, or None."""
        key = key.strip()
        if not key:
            return "Paste your OpenAI API key."
        if any(c.isspace() for c in key):
            return "The key can’t contain spaces or line breaks. Copy it again."
        return None

    def save(self, key: str) -> None:
        """Store the key. Raises KeystoreError or OSError, never falling back to plain text."""
        keystore.set_api_key(key.strip())
        self.reload()

    def remove(self) -> None:
        keystore.delete_api_key()
        self.reload()


def check_key(key: str, timeout: float = 15.0) -> CheckResult:
    """Ask OpenAI whether it accepts ``key`` by listing models, which transcribes nothing and uses no tokens."""
    import openai

    try:
        openai.OpenAI(api_key=key.strip(), max_retries=0, timeout=timeout).models.list()
    except openai.AuthenticationError:
        return CheckResult(Outcome.REJECTED, "OpenAI didn’t accept this key. Check that you copied all of it.")
    except openai.PermissionDeniedError:
        return CheckResult(Outcome.UNCHECKED, "This key isn’t allowed to list models, so Vox couldn’t check it.")
    except openai.APIConnectionError:  # includes timeouts
        return CheckResult(Outcome.UNCHECKED, "Vox couldn’t reach OpenAI to check the key.")
    except openai.APIStatusError as e:
        return CheckResult(Outcome.UNCHECKED, f"OpenAI couldn’t check the key right now (error {e.status_code}).")
    except Exception as e:
        return CheckResult(Outcome.UNCHECKED, f"Vox couldn’t check the key: {e}")
    return CheckResult(Outcome.ACCEPTED)


def _shown(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)
