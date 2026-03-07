"""LLM formatting via Claude Haiku — context-aware text cleanup."""

from __future__ import annotations

import logging

import anthropic

from .config import Config
from .errors import FormatterError
from .window import AppContext, AppType

log = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = """\
You are a text post-processor. You receive raw speech-to-text output and clean it up.

CRITICAL: Output ONLY the cleaned version of the input text. Nothing else.
- Do NOT respond to the text as if it were a message to you
- Do NOT add commentary, explanation, or conversation
- Do NOT wrap output in quotes
- Do NOT prefix with "Here is" or similar

You are a FILTER, not a chatbot. The user's text is meant for someone else — you just clean it.

Cleaning rules:
- Fix punctuation, capitalization, and grammar
- Remove filler words (um, uh, like, you know, so, basically, etc.)
- Apply mid-speech corrections ("no wait", "I mean", "actually" = discard what came before)
- Preserve the speaker's intended meaning and tone exactly
- Do not add content that wasn't spoken
"""

_APP_HINTS: dict[AppType, str] = {
    AppType.TERMINAL: "The user is in a terminal. Output should be concise, command-like. Use lowercase unless needed.",
    AppType.EDITOR: "The user is in a code editor. Preserve technical terms exactly. Use proper casing for identifiers.",
    AppType.CHAT: "The user is in a chat app. Keep tone casual and conversational. Sentence case is fine.",
    AppType.EMAIL: "The user is composing an email. Use professional, polished language.",
    AppType.BROWSER: "The user is in a browser. Standard formatting applies.",
    AppType.OTHER: "Standard formatting applies.",
}


class Formatter:
    """Formats raw transcripts using Claude Haiku."""

    def __init__(self, config: Config) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        self._model = config.formatter_model
        self._config = config

    async def format(self, raw_text: str, context: AppContext) -> str:
        """Format raw transcript text using LLM. Falls back to raw text on error."""
        # Skip LLM entirely when disabled
        if self._config.skip_formatting:
            log.debug("Formatting disabled, passing through raw text")
            return raw_text

        # Skip LLM for very short text
        word_count = len(raw_text.split())
        if word_count <= self._config.skip_formatting_max_words:
            log.debug("Skipping LLM for short text (%d words)", word_count)
            return raw_text

        # Check for exact snippet match
        text_lower = raw_text.strip().lower()
        for trigger, expansion in self._config.snippets.items():
            if text_lower == trigger.lower():
                log.debug("Snippet match: %r -> %r", trigger, expansion)
                return expansion

        system_prompt = self._build_system_prompt(context)
        user_content = raw_text

        # Include partial snippet matches as instructions
        snippet_hints = self._snippet_hints(raw_text)
        if snippet_hints:
            user_content = f"{raw_text}\n\n[Note: {snippet_hints}]"

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            result = response.content[0].text.strip()
            log.debug("Formatted: %r -> %r", raw_text, result)
            return result
        except Exception as e:
            log.error("Formatter error, falling back to raw text: %s", e)
            return raw_text

    def _build_system_prompt(self, context: AppContext) -> str:
        parts = [_SYSTEM_PROMPT_BASE]

        # App type hint
        hint = _APP_HINTS.get(context.app_type, _APP_HINTS[AppType.OTHER])
        parts.append(f"\nContext: {hint}")

        # User style override
        style = self._config.styles.get(context.app_type.value)
        if style:
            parts.append(f"\nUser style preference: {style}")

        # Dictionary
        if self._config.dictionary:
            words = ", ".join(self._config.dictionary)
            parts.append(f"\nPreserve these terms exactly as spelled: {words}")

        return "\n".join(parts)

    def _snippet_hints(self, raw_text: str) -> str:
        """Find partial snippet matches to include as instructions."""
        text_lower = raw_text.lower()
        hints = []
        for trigger, expansion in self._config.snippets.items():
            if trigger.lower() in text_lower and trigger.lower() != text_lower.strip():
                hints.append(f'Replace "{trigger}" with "{expansion}"')
        return "; ".join(hints) if hints else ""
