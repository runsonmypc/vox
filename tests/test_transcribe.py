"""Tests for the OpenAI transcriber's client, which follows the current key. Dummy keys only."""

from unittest.mock import AsyncMock, patch

import pytest

from vox.config import Config
from vox.errors import TranscriptionError
from vox.transcribe import WhisperTranscriber

KEY = "sk-test-dummy-0001"
OTHER = "sk-test-dummy-0002"


class FakeClient:
    made: list[str] = []
    instances: list["FakeClient"] = []

    def __init__(self, api_key: str) -> None:
        FakeClient.made.append(api_key)
        FakeClient.instances.append(self)
        self.audio = AsyncMock()
        self.audio.transcriptions.create = AsyncMock(return_value="hello there")


@pytest.fixture
def fake_openai():
    FakeClient.made = []
    FakeClient.instances = []
    with patch("vox.transcribe.AsyncOpenAI", FakeClient):
        yield FakeClient


def test_starts_without_a_key():
    WhisperTranscriber(Config(openai_api_key=""))  # the real client raises without a key


@pytest.mark.anyio
async def test_no_key_fails_without_calling_openai(fake_openai):
    with pytest.raises(TranscriptionError, match="Set API Key"):
        await WhisperTranscriber(Config()).transcribe(b"wav")
    assert fake_openai.made == []


@pytest.mark.anyio
async def test_client_follows_the_current_key(fake_openai):
    config = Config(openai_api_key=KEY)
    transcriber = WhisperTranscriber(config)
    assert await transcriber.transcribe(b"wav") == "hello there"
    await transcriber.transcribe(b"wav")
    config.openai_api_key = OTHER
    await transcriber.transcribe(b"wav")
    assert fake_openai.made == [KEY, OTHER]


@pytest.mark.anyio
async def test_gpt_transcribe_uses_keywords_and_plural_languages(fake_openai):
    config = Config(
        openai_api_key=KEY,
        whisper_language="en",
        whisper_prompt="Software dictation",
        dictionary=["Kubernetes", "FastAPI", "<invalid>"],
    )
    transcriber = WhisperTranscriber(config)
    assert await transcriber.transcribe(b"wav") == "hello there"

    kwargs = fake_openai.instances[0].audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-transcribe"
    assert kwargs["languages"] == ["en"]
    assert kwargs["prompt"] == "Software dictation"
    assert kwargs["keywords"] == ["Kubernetes", "FastAPI"]
    assert "language" not in kwargs

    config.whisper_model = "whisper-1"
    await transcriber.transcribe(b"wav")
    kwargs = fake_openai.instances[0].audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["language"] == "en"
    assert kwargs["response_format"] == "text"
