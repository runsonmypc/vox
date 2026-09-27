"""OpenAI batch transcription, and the vocabulary hints every provider gets."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import wave
from typing import TYPE_CHECKING, Any

from .audio import upload_wavs
from .config import Config
from .errors import TranscriptionError
from .window import AppContext

if TYPE_CHECKING:
    from openai import AsyncOpenAI

log = logging.getLogger(__name__)


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

# Credential formats a terminal, .env file or dashboard on screen may show. They rank high as
# "technical" words, so without this filter they would be sent to the provider as hints.
_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-[\w-]{16,}|gh[po]_\w{16,}|github_pat_\w{16,}|xox[abprs]-[\w-]{8,}"
    r"|A(?:KIA|SIA)[0-9A-Z]{12,}|AIza[\w-]{20,}|eyJ[\w.-]{10,})"
)
# The value in "API_KEY=...", "password: ...", '"token": "..."' and the like
_SECRET_VALUE_RE = re.compile(
    r"""(?i)(\w*(?:key|token|secret|passw(?:or)?d)\w*)["']?\s*[=:]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s"',;]+)"""
)
# A PEM or OpenSSH key block, whose last line can be too short for the run rule below
_PEM_BLOCK_RE = re.compile(r"-----BEGIN [^-\n]+-----.*?(?:-----END [^-\n]+-----|\Z)", re.S)
# Text between whitespace and quotes. Secrets are judged per token, before the word split cuts
# a base64 key apart at "/", "+" and "=" into pieces too short to look random.
_TOKEN_RE = re.compile(r"""[^\s"'`]+""")
# 20+ characters of the base64/base64url alphabet mixing letters and digits: an AWS or Azure
# key, a private-key line, a hex or UUID token. Long identifiers with digits go too; nobody
# dictates those.
_KEY_RUN_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")


def _is_secret_token(token: str) -> bool:
    if _SECRET_RE.search(token):
        return True
    return any(
        any(c.isdigit() for c in run) and any(c.isalpha() for c in run) for run in _KEY_RUN_RE.findall(token)
    )


def _drop_secrets(text: str) -> str:
    text = _PEM_BLOCK_RE.sub(" ", text)
    text = _SECRET_VALUE_RE.sub(r"\1", text)
    return _TOKEN_RE.sub(lambda m: "" if _is_secret_token(m.group()) else m.group(), text)


def _looks_secret(word: str) -> bool:
    if _SECRET_RE.match(word):
        return True
    # Long random strings mixing cases and digits: API keys, session tokens, signatures
    return (
        len(word) >= 20
        and any(c.isdigit() for c in word)
        and any(c.isupper() for c in word)
        and any(c.islower() for c in word)
    )


def _extract_vocab(screen_text: str, max_words: int = 25) -> list[str]:
    """Extract unique, technical words from screen text for Whisper vocabulary hints, never secrets."""
    words = re.findall(r"[A-Za-z][\w.-]*[A-Za-z\d]|[A-Za-z]+", _drop_secrets(screen_text))
    seen: set[str] = set()
    vocab: list[str] = []
    for w in words:
        lower = w.lower()
        if lower in seen or lower in _COMMON_WORDS or lower in _UI_IGNORE_WORDS or len(w) < 3 or _looks_secret(w):
            continue
        seen.add(lower)
        # Only prioritize technical terms or proper nouns
        if _TECHNICAL_RE.search(w) or w[0].isupper():
            vocab.append(w)
            if len(vocab) >= max_words:
                break
    return vocab


# A model echoing its prompt repeats most of it; a user dictating a few on-screen names does not
_ECHO_COVERAGE = 0.7


def is_prompt_hallucination(transcript: str, prompt: str) -> bool:
    """Detect if the model hallucinated by echoing back the vocabulary prompt on silence."""
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

    def covers_prompt(matched: set[str]) -> bool:
        return len(matched) >= _ECHO_COVERAGE * len(term_set)

    # If transcript has comma-separated list structure
    tokens = [t.strip().strip(".?!,:;\"'").lower() for t in transcript.split(",") if t.strip()]
    if len(tokens) >= 3:
        matched = {t for t in tokens if t in term_set}
        if sum(1 for t in tokens if t in term_set) / len(tokens) >= 0.7 and covers_prompt(matched):
            return True

    # Also check if >80% of all words in the transcript are from the vocabulary list
    words = [w.strip(".?!,:;\"'").lower() for w in transcript.split() if w.strip()]
    if len(words) >= 5:
        matched = {w for w in words if w in term_set}
        if sum(1 for w in words if w in term_set) / len(words) >= 0.8 and covers_prompt(matched):
            return True

    return False


class PartialTranscriptionError(TranscriptionError):
    """A recording sent in parts failed partway; ``text`` is what the parts before the failure said."""

    def __init__(self, message: str, text: str) -> None:
        super().__init__(message)
        self.text = text


class Transcriber:
    """Async wrapper around OpenAI transcription API with context-aware prompting."""

    def __init__(self, config: Config) -> None:
        # Built on first use and rebuilt when the key changes, so Vox can start without a key and pick up a new one
        self._client: AsyncOpenAI | None = None
        self._client_key = ""
        self._client_lock = threading.Lock()  # warm_up() builds it on a worker thread
        self._config = config

    def _build_prompt(self, context: AppContext | None) -> str:
        return build_prompt(self._config, context)

    def _openai(self) -> AsyncOpenAI:
        with self._client_lock:
            key = self._config.openai_api_key
            if self._client is None or key != self._client_key:
                # Imported here: it takes about a second, and local-only Vox never needs it
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(api_key=key)
                self._client_key = key
            return self._client

    def warm_up(self) -> None:
        """Import the SDK and build the client ahead of the first dictation. Blocks; run it off the event loop."""
        if self._config.openai_api_key:
            self._openai()

    def _request(self, context: AppContext | None) -> tuple[dict[str, Any], str]:
        """The API parameters for this recording, and the text a prompt echo would repeat."""
        config = self._config
        model = config.whisper_model
        request: dict[str, Any] = {"model": model}
        if model == "gpt-transcribe":
            if config.whisper_language:
                request["languages"] = [config.whisper_language]
            if config.whisper_prompt:
                request["prompt"] = config.whisper_prompt
            keywords = api_keywords(config, context)
            if keywords:
                request["keywords"] = keywords
            return request, "\n".join(filter(None, [config.whisper_prompt, ", ".join(keywords)]))

        request["response_format"] = "text"
        if config.whisper_language:
            request["language"] = config.whisper_language
        prompt = self._build_prompt(context)
        if prompt:
            request["prompt"] = prompt
        return request, prompt

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        """Transcribe WAV audio bytes to text with optional context.

        The audio goes up as 16 kHz mono; a recording too large for one upload
        is sent in parts cut at pauses, and their transcripts are joined. The
        SDK retries transient failures itself; anything else fails at once.
        """
        if not self._config.openai_api_key:
            raise TranscriptionError("No OpenAI API key. Choose Set API Key… from the Vox menu.")
        try:
            parts = await asyncio.to_thread(upload_wavs, wav_bytes)
        except (EOFError, ValueError, wave.Error) as e:
            raise TranscriptionError(f"Invalid recording: {e}") from e

        from openai import OpenAIError

        request, echo = self._request(context)
        if echo:
            log.debug("Transcription prompt: %d chars", len(echo))
        if len(parts) > 1:
            log.info("Recording is over the upload limit; sending it in %d parts", len(parts))

        texts: list[str] = []
        for number, part in enumerate(parts, 1):
            try:
                result = await self._openai().audio.transcriptions.create(
                    file=("audio.wav", part, "audio/wav"), **request,
                )
            except OpenAIError as e:
                done = " ".join(texts)
                if done:
                    raise PartialTranscriptionError(f"part {number} of {len(parts)} failed: {e}", done) from e
                raise TranscriptionError(f"Transcription API failed: {e}") from e
            text = (result if isinstance(result, str) else result.text).strip()
            # Guard against the model echoing context on empty/silent audio
            if echo and is_prompt_hallucination(text, echo):
                log.warning("Dropped a transcript that only echoed the prompt")
                log.debug("Dropped echo: %s", text)
                continue
            if text:
                texts.append(text)

        text = " ".join(texts)
        log.info("Transcript: %d chars", len(text))
        log.debug("Transcript: %s", text)
        return text


WhisperTranscriber = Transcriber


def build_vocabulary(config: Config, context: AppContext | None) -> list[str]:
    """Unique vocabulary hints: the dictionary, plus window-title and screen words when screen context is on."""
    vocab: list[str] = list(config.dictionary)
    if context is not None and config.context_screen:
        vocab.extend(_extract_vocab(context.window_title, max_words=10))
        vocab.extend(_extract_vocab(context.screen_text, max_words=25))

    seen: set[str] = set()
    unique_vocab: list[str] = []
    for word in vocab:
        lower = word.lower()
        if lower not in seen:
            seen.add(lower)
            unique_vocab.append(word)

    return unique_vocab[:40]


def api_keywords(config: Config, context: AppContext | None) -> list[str]:
    """The vocabulary as the OpenAI ``keywords`` parameter, used by both batch and streaming."""
    return [word for word in build_vocabulary(config, context) if not any(char in word for char in "<>\r\n")]


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
