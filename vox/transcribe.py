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

# Generic macOS UI menu words to ignore from screen OCR
_UI_IGNORE_WORDS = frozenset(
    "file edit view window help session scripts preferences settings tab terminal"
    " shell profiles search find replace undo redo copy paste cut close minimize"
    " zoom hide quit enter exit select tools options developer format run debug"
    " history bookmarks prof iles"
    .split()
)

# Match words that are likely technical: camelCase, has digits, underscores, etc.
_TECHNICAL_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2,}|_|\d")


def _extract_vocab(screen_text: str, max_words: int = 25) -> list[str]:
    """Extract unique, technical words from screen text for Whisper vocabulary hints."""
    words = re.findall(r"[A-Za-z][\w.-]*[A-Za-z\d]|[A-Za-z]+", screen_text)
    seen: set[str] = set()
    vocab: list[str] = []
    for w in words:
        lower = w.lower()
        if lower in seen or lower in _COMMON_WORDS or lower in _UI_IGNORE_WORDS or len(w) < 3:
            continue
        seen.add(lower)
        # Only prioritize technical terms or proper nouns
        if _TECHNICAL_RE.search(w) or w[0].isupper():
            vocab.append(w)
            if len(vocab) >= max_words:
                break
    return vocab


def is_prompt_hallucination(transcript: str, prompt: str) -> bool:
    """Detect if Whisper hallucinated by echoing back the vocabulary prompt on silence."""
    if not transcript or not prompt:
        return False

    t_norm = transcript.strip().rstrip(".").lower()
    p_norm = prompt.strip().rstrip(".").lower()

    # Exact or near-exact match
    if t_norm == p_norm:
        return True

    # Check if transcript consists of prompt terms separated by commas
    terms = [w.strip().lower() for w in prompt.replace("\n", ",").split(",") if w.strip()]
    if not terms:
        return False
    term_set = set(terms)

    # If transcript has comma-separated list structure
    tokens = [t.strip().strip(".?!,:;\"'").lower() for t in transcript.split(",") if t.strip()]
    if len(tokens) >= 3:
        matches = sum(1 for t in tokens if t in term_set)
        if matches / len(tokens) >= 0.7:
            return True

    # Also check if >80% of all words in the transcript are from the vocabulary list
    words = [w.strip(".?!,:;\"'").lower() for w in transcript.split() if w.strip()]
    if len(words) >= 5:
        matches = sum(1 for w in words if w in term_set)
        if matches / len(words) >= 0.8:
            return True

    return False


class Transcriber:
    """Async wrapper around OpenAI transcription API with context-aware prompting."""

    def __init__(self, config: Config) -> None:
        # Built on first use and rebuilt when the key changes, so Vox can start without a key and pick up a new one
        self._client: AsyncOpenAI | None = None
        self._client_key = ""
        self._config = config

    def _build_prompt(self, context: AppContext | None) -> str:
        return build_prompt(self._config, context)

    def _openai(self) -> AsyncOpenAI:
        key = self._config.openai_api_key
        if self._client is None or key != self._client_key:
            self._client = AsyncOpenAI(api_key=key)
            self._client_key = key
        return self._client

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        """Transcribe WAV audio bytes to text with optional context."""
        prompt = self._build_prompt(context)
        if prompt:
            log.debug("Transcription prompt: %d chars", len(prompt))

        if not self._config.openai_api_key:
            raise TranscriptionError("No OpenAI API key. Choose Set API Key… from the Vox menu.")

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                model = self._config.whisper_model
                kwargs: dict = {
                    "model": model,
                    "file": ("audio.wav", wav_bytes, "audio/wav"),
                }
                if model == "gpt-transcribe":
                    if self._config.whisper_language:
                        kwargs["languages"] = [self._config.whisper_language]
                    if self._config.whisper_prompt:
                        kwargs["prompt"] = self._config.whisper_prompt
                    keywords = [
                        word for word in build_vocabulary(self._config, context)
                        if not any(char in word for char in "<>\r\n")
                    ]
                    if keywords:
                        kwargs["keywords"] = keywords
                else:
                    kwargs["response_format"] = "text"
                    if self._config.whisper_language:
                        kwargs["language"] = self._config.whisper_language
                    if prompt:
                        kwargs["prompt"] = prompt

                result = await self._openai().audio.transcriptions.create(**kwargs)
                text = result.strip() if isinstance(result, str) else result.text.strip()

                # Guard against the model echoing context on empty/silent audio
                if prompt and is_prompt_hallucination(text, prompt):
                    log.warning("Detected transcription prompt echo on silence, dropping transcript")
                    return ""

                log.info("Transcript: %s", text)
                return text
            except Exception as e:
                last_error = e
                log.warning("Transcription API error (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, e)
        raise TranscriptionError(f"Transcription API failed after {MAX_ATTEMPTS} attempts: {last_error}")


WhisperTranscriber = Transcriber


def build_vocabulary(config: Config, context: AppContext | None) -> list[str]:
    """Collect unique vocabulary hints from the dictionary and active window."""
    vocab: list[str] = list(config.dictionary)
    if context:
        if context.window_title:
            vocab.extend(_extract_vocab(context.window_title, max_words=10))
        if context.screen_text:
            vocab.extend(_extract_vocab(context.screen_text, max_words=25))

    seen: set[str] = set()
    unique_vocab: list[str] = []
    for word in vocab:
        lower = word.lower()
        if lower not in seen:
            seen.add(lower)
            unique_vocab.append(word)

    return unique_vocab[:40]


def build_prompt(config: Config, context: AppContext | None, base_prompt: str | None = None) -> str:
    """Build a vocabulary-only prompt for legacy API models and local Whisper."""
    parts = []
    initial_prompt = base_prompt if base_prompt is not None else config.whisper_prompt
    if initial_prompt:
        parts.append(initial_prompt)
    vocab = build_vocabulary(config, context)
    if vocab:
        parts.append(", ".join(vocab))
    return "\n".join(parts)
