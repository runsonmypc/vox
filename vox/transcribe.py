"""OpenAI transcription client with context-aware prompting."""

from __future__ import annotations

import logging
import re

from openai import AsyncOpenAI

from .config import Config
from .errors import TranscriptionError
from .window import AppContext

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


_COMMON_WORDS = frozenset(
    "the a an and or but in on at to for of is it that this with from by as are was were be"
    " been have has had do does did will would can could may might shall should not no yes"
    " if then else so than too also just only very much more most some any all each every"
    " i you he she we they me him her us them my your his its our their what which who how"
    " when where why about into through over after before between under during without"
    .split()
)

# Match words that are likely technical: camelCase, has digits, underscores, etc.
_TECHNICAL_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2,}|_|\d")


def _extract_vocab(screen_text: str, max_words: int = 80) -> list[str]:
    """Extract unique, non-trivial words from screen text for Whisper vocabulary hints."""
    words = re.findall(r"[A-Za-z][\w.-]*[A-Za-z\d]|[A-Za-z]", screen_text)
    seen: set[str] = set()
    vocab: list[str] = []
    for w in words:
        lower = w.lower()
        if lower in seen or lower in _COMMON_WORDS or len(w) < 3:
            continue
        seen.add(lower)
        # Prioritize technical terms but include all non-common words
        vocab.append(w)
        if len(vocab) >= max_words:
            break
    return vocab


class Transcriber:
    """Async wrapper around OpenAI transcription API with context-aware prompting."""

    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._model = config.whisper_model
        self._language = config.whisper_language
        self._base_prompt = config.whisper_prompt
        self._config = config

    def _build_prompt(self, context: AppContext | None) -> str:
        """Build a vocabulary-only prompt for Whisper.

        Whisper's prompt parameter is treated as example-output that biases
        style, not as instructions. We send only a bare comma-separated list
        of proper nouns and technical terms so it influences spelling without
        biasing phrasing.
        """
        vocab: list[str] = list(self._config.dictionary)
        if context:
            if context.window_title:
                vocab.extend(_extract_vocab(context.window_title))
            if context.screen_text:
                vocab.extend(_extract_vocab(context.screen_text))

        seen: set[str] = set()
        unique_vocab: list[str] = []
        for w in vocab:
            lower = w.lower()
            if lower in seen:
                continue
            seen.add(lower)
            unique_vocab.append(w)

        parts = []
        if self._base_prompt:
            parts.append(self._base_prompt)
        if unique_vocab:
            parts.append(", ".join(unique_vocab))
        return "\n".join(parts) if parts else ""

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        """Transcribe WAV audio bytes to text with optional context."""
        prompt = self._build_prompt(context)
        if prompt:
            log.debug("Transcription prompt: %d chars", len(prompt))

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                kwargs: dict = {
                    "model": self._model,
                    "file": ("audio.wav", wav_bytes, "audio/wav"),
                    "response_format": "text",
                }
                if self._language:
                    kwargs["language"] = self._language
                if prompt:
                    kwargs["prompt"] = prompt

                result = await self._client.audio.transcriptions.create(**kwargs)
                text = result.strip() if isinstance(result, str) else result.text.strip()
                log.info("Transcript: %s", text)
                return text
            except Exception as e:
                last_error = e
                log.warning("Whisper API error (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, e)
        raise TranscriptionError(f"Whisper API failed after {MAX_ATTEMPTS} attempts: {last_error}")
