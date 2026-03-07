"""OpenAI transcription client with context-aware prompting."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from .config import Config
from .errors import TranscriptionError
from .window import AppContext, AppType

log = logging.getLogger(__name__)

MAX_RETRIES = 2

_APP_HINTS: dict[AppType, str] = {
    AppType.TERMINAL: "The user is dictating into a terminal/command line.",
    AppType.EDITOR: "The user is dictating into a code editor.",
    AppType.CHAT: "The user is dictating a chat message.",
    AppType.EMAIL: "The user is composing an email.",
    AppType.BROWSER: "The user is typing in a browser.",
    AppType.OTHER: "",
}


class Transcriber:
    """Async wrapper around OpenAI transcription API with context-aware prompting."""

    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._model = config.whisper_model
        self._language = config.whisper_language
        self._base_prompt = config.whisper_prompt
        self._config = config

    def _build_prompt(self, context: AppContext | None) -> str:
        """Build a transcription prompt incorporating window and text context."""
        parts = []

        if self._base_prompt:
            parts.append(self._base_prompt)

        if context:
            # App type hint
            hint = _APP_HINTS.get(context.app_type, "")
            if hint:
                parts.append(hint)

            # Window title for additional context
            if context.window_title:
                parts.append(f"Window: {context.window_title}")

            # Surrounding text gives the model vocabulary/style cues
            if context.surrounding_text:
                # Trim to keep prompt reasonable
                text = context.surrounding_text.strip()
                if len(text) > 300:
                    text = text[:300]
                parts.append(f"Surrounding text: {text}")

        # Custom dictionary
        if self._config.dictionary:
            parts.append(f"Vocabulary: {', '.join(self._config.dictionary)}")

        return " ".join(parts) if parts else ""

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        """Transcribe WAV audio bytes to text with optional context."""
        prompt = self._build_prompt(context)
        if prompt:
            log.debug("Transcription prompt: %s", prompt[:200])

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 2):
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
                log.debug("Transcription (attempt %d): %r", attempt, text)
                return text
            except Exception as e:
                last_error = e
                log.warning("Whisper API error (attempt %d/%d): %s", attempt, MAX_RETRIES + 1, e)
        raise TranscriptionError(f"Whisper API failed after {MAX_RETRIES + 1} attempts: {last_error}")
