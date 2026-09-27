"""The transcription modes, their menu labels, and whether one can run now."""

from __future__ import annotations

from .config import MODES, Config
from .errors import ConfigError

LABELS: dict[str, str] = {
    "batch": "OpenAI (batch)",
    "streaming": "OpenAI (streaming)",
    "whisper_cpp": "Local (whisper.cpp)",
}
assert tuple(LABELS) == MODES


def mode_problem(config: Config, mode: str, *, check_setup: bool = True) -> str | None:
    """Why ``mode`` can't be used right now, or None if it can.

    Without ``check_setup`` the whisper.cpp binary and model aren't tried: for a caller about
    to build that transcriber, which checks them itself and raises ConfigError.
    """
    if mode not in MODES:
        return f"Invalid transcription mode: {mode}"
    if mode == "whisper_cpp":
        if not check_setup:
            return None
        from .whisper_cpp import WhisperCppTranscriber

        try:
            WhisperCppTranscriber(config)
        except ConfigError as e:
            return str(e)
        return None
    if not config.openai_api_key:
        return "Set an OpenAI API key before selecting OpenAI transcription"
    return None
