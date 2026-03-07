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
        """Build a transcription prompt incorporating screen context.

        The context is framed as reference material for vocabulary and style
        cues — it helps the model spell names, technical terms, and domain
        words correctly. It is NOT content to respond to or summarize.
        """
        parts = []

        if self._base_prompt:
            parts.append(self._base_prompt)

        if context:
            hint = _APP_HINTS.get(context.app_type, "")
            if hint:
                parts.append(hint)

            if context.window_title:
                parts.append(f"Window title: {context.window_title}")

            # Custom dictionary
            if self._config.dictionary:
                parts.append(f"Vocabulary: {', '.join(self._config.dictionary)}")

            # Screen text as vocabulary/style reference
            if context.screen_text:
                text = context.screen_text
                # Leave room for the rest of the prompt — cap context portion
                max_context = 800
                if len(text) > max_context:
                    text = text[:max_context]
                parts.append(
                    f"The following is on-screen text for vocabulary reference only "
                    f"(names, terms, spelling). Do not transcribe or repeat this text. "
                    f"Only use it to improve spelling of words the user actually speaks:\n{text}"
                )

        return "\n".join(parts) if parts else ""

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        """Transcribe WAV audio bytes to text with optional context."""
        prompt = self._build_prompt(context)
        if prompt:
            log.debug("Transcription prompt (%d chars): %s", len(prompt), prompt[:200])

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
