"""Tests for the OpenAI transcriber and the vocabulary hints. Dummy keys only; OpenAI is never called."""

import io
import logging
import subprocess
import sys
import wave
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from openai import OpenAIError

from vox.config import Config
from vox.errors import TranscriptionError
from vox.transcribe import (
    PartialTranscriptionError,
    Transcriber,
    _extract_vocab,
    build_prompt,
    build_vocabulary,
    is_prompt_hallucination,
)
from vox.window import AppContext, AppType

KEY = "sk-test-dummy-0001"
OTHER = "sk-test-dummy-0002"


def make_wav(samples, rate=48000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


WAV = make_wav(np.arange(4800) % 1000)


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
    # Patched where transcribe.py imports it from, at first use
    with patch("openai.AsyncOpenAI", FakeClient):
        yield FakeClient


def create_mock(fake_openai):
    return fake_openai.instances[0].audio.transcriptions.create


def test_starts_without_a_key():
    Transcriber(Config(openai_api_key=""))  # the real client raises without a key


def test_importing_the_daemon_does_not_load_the_openai_sdk():
    """The SDK takes about a second to import; local-only Vox never needs it."""
    code = "import sys, vox.daemon; print('openai' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert result.stdout.strip() == "False"


@pytest.mark.anyio
async def test_no_key_fails_without_calling_openai(fake_openai):
    with pytest.raises(TranscriptionError, match="Set API Key"):
        await Transcriber(Config()).transcribe(WAV)
    assert fake_openai.made == []


@pytest.mark.anyio
async def test_client_follows_the_current_key(fake_openai):
    config = Config(openai_api_key=KEY)
    transcriber = Transcriber(config)
    assert await transcriber.transcribe(WAV) == "hello there"
    await transcriber.transcribe(WAV)
    config.openai_api_key = OTHER
    await transcriber.transcribe(WAV)
    assert fake_openai.made == [KEY, OTHER]


def test_warm_up_builds_the_client_once_and_only_with_a_key(fake_openai):
    config = Config(openai_api_key="")
    transcriber = Transcriber(config)
    transcriber.warm_up()
    assert fake_openai.made == []
    config.openai_api_key = KEY
    transcriber.warm_up()
    transcriber.warm_up()
    assert fake_openai.made == [KEY]


@pytest.mark.anyio
async def test_gpt_transcribe_uses_keywords_and_plural_languages(fake_openai):
    config = Config(
        openai_api_key=KEY,
        whisper_language="en",
        whisper_prompt="Software dictation",
        dictionary=["Kubernetes", "FastAPI", "<invalid>"],
    )
    transcriber = Transcriber(config)
    assert await transcriber.transcribe(WAV) == "hello there"

    kwargs = create_mock(fake_openai).call_args.kwargs
    assert kwargs["model"] == "gpt-transcribe"
    assert kwargs["languages"] == ["en"]
    assert kwargs["prompt"] == "Software dictation"
    assert kwargs["keywords"] == ["Kubernetes", "FastAPI"]
    assert "language" not in kwargs

    config.whisper_model = "whisper-1"
    await transcriber.transcribe(WAV)
    kwargs = create_mock(fake_openai).call_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["language"] == "en"
    assert kwargs["response_format"] == "text"


@pytest.mark.anyio
async def test_upload_is_16k_mono(fake_openai):
    ramp = (np.arange(48000) % 20000).astype(np.int16)
    await Transcriber(Config(openai_api_key=KEY)).transcribe(make_wav(ramp))

    name, data, mime = create_mock(fake_openai).call_args.kwargs["file"]
    assert (name, mime) == ("audio.wav", "audio/wav")
    with wave.open(io.BytesIO(data), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    np.testing.assert_array_equal(samples, ramp[::3])


@pytest.mark.anyio
async def test_invalid_recording_fails_without_calling_openai(fake_openai):
    with pytest.raises(TranscriptionError, match="Invalid recording"):
        await Transcriber(Config(openai_api_key=KEY)).transcribe(b"wav")
    assert fake_openai.made == []


@pytest.mark.anyio
async def test_api_failure_is_not_retried_on_top_of_the_sdk(fake_openai):
    transcriber = Transcriber(Config(openai_api_key=KEY))
    transcriber._openai()
    create_mock(fake_openai).side_effect = OpenAIError("server exploded")

    with pytest.raises(TranscriptionError, match="server exploded") as excinfo:
        await transcriber.transcribe(WAV)
    assert not isinstance(excinfo.value, PartialTranscriptionError)
    assert create_mock(fake_openai).await_count == 1


@pytest.mark.anyio
async def test_a_long_recording_goes_up_in_parts_joined_with_a_space(fake_openai):
    transcriber = Transcriber(Config(openai_api_key=KEY))
    transcriber._openai()
    create_mock(fake_openai).side_effect = ["First part.", "  ", "second part."]

    with patch("vox.transcribe.upload_wavs", return_value=[b"one", b"two", b"three"]):
        assert await transcriber.transcribe(WAV) == "First part. second part."
    uploads = [c.kwargs["file"][1] for c in create_mock(fake_openai).call_args_list]
    assert uploads == [b"one", b"two", b"three"]


@pytest.mark.anyio
async def test_a_failed_part_keeps_the_text_already_transcribed(fake_openai):
    transcriber = Transcriber(Config(openai_api_key=KEY))
    transcriber._openai()
    create_mock(fake_openai).side_effect = ["First part.", OpenAIError("rate limited")]

    with patch("vox.transcribe.upload_wavs", return_value=[b"one", b"two", b"three"]):
        with pytest.raises(PartialTranscriptionError, match="part 2 of 3") as excinfo:
            await transcriber.transcribe(WAV)
    assert excinfo.value.text == "First part."
    assert create_mock(fake_openai).await_count == 2


@pytest.mark.anyio
async def test_transcript_text_is_logged_only_at_debug(fake_openai, caplog):
    transcriber = Transcriber(Config(openai_api_key=KEY))
    transcriber._openai()
    create_mock(fake_openai).return_value = "my bank PIN is private"

    with caplog.at_level(logging.INFO, logger="vox.transcribe"):
        await transcriber.transcribe(WAV)
    assert "Transcript: 22 chars" in caplog.text
    assert "private" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="vox.transcribe"):
        await transcriber.transcribe(WAV)
    assert "my bank PIN is private" in caplog.text


@pytest.mark.anyio
async def test_a_transcript_that_only_echoes_the_hints_is_dropped(fake_openai):
    words = ["Kubernetes", "FastAPI", "Postgres", "Terraform", "useState"]
    transcriber = Transcriber(Config(openai_api_key=KEY, dictionary=words))
    transcriber._openai()

    create_mock(fake_openai).return_value = ", ".join(words) + "."
    assert await transcriber.transcribe(WAV) == ""

    create_mock(fake_openai).return_value = "Deploy FastAPI, Postgres, and Terraform."
    assert await transcriber.transcribe(WAV) == "Deploy FastAPI, Postgres, and Terraform."


def test_echo_guard_needs_most_of_the_prompt_repeated():
    prompt = ", ".join(f"Term{i}" for i in range(20))
    assert is_prompt_hallucination(prompt, prompt)
    assert is_prompt_hallucination(", ".join(f"Term{i}" for i in range(16)), prompt)
    # A user reading out a few on-screen names is dictation, not an echo
    assert not is_prompt_hallucination("Term1, Term2, Term3", prompt)
    assert not is_prompt_hallucination("Term1 Term2 Term3 Term4 Term5", prompt)


# -- Vocabulary hints ---------------------------------------------------------------


def test_screen_vocabulary_never_includes_secrets():
    screen = "\n".join([
        "OPENAI_API_KEY=sk-proj-Abc123Def456Ghi789Jkl",
        'export GITHUB_TOKEN="ghp_0123456789abcdefABCDEF0123"',
        "password: hunter2Correct",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "AKIAIOSFODNN7EXAMPLE",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        "session 9fQ2xLmP4rT7vB1nC5dF3hJ8",
        "Kubernetes useState FastAPI",
    ])
    vocab = _extract_vocab(screen)
    joined = " ".join(vocab)
    for secret in ["sk-proj", "ghp_", "hunter2", "wJalr", "AKIA", "eyJ", "fQ2xLmP"]:
        assert secret not in joined
    for word in ["Kubernetes", "useState", "FastAPI"]:
        assert word in vocab


@pytest.mark.parametrize("secret, pieces", [
    # A bare AWS secret key: the word split used to cut it at "/" into short harmless-looking words
    ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", ["wJalr", "K7MDENG", "bPxRfi"]),
    ("AccountKey Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
     ["Eby8", "K1SZ", "KBHB"]),
    ("QyNTUxOQAAACB+jTgB5sFpf4Mn/PlGvXq9t3YkR2wZ8hL0cN1mJ6uA7QAAAJgAbCdeAGwn", ["QyNTU", "jTgB", "PlGv"]),
    ("api key a1b2c3d4e5f60718293a4b5c6d7e8f90", ["a1b2"]),
    ("token 3f2504e0-4f89-11d3-9a0c-0305e82c3301", ["3f25", "f2504e0"]),
])
def test_screen_vocabulary_drops_base64_hex_and_uuid_keys_whole(secret, pieces):
    joined = " ".join(_extract_vocab(f"Kubernetes {secret} FastAPI"))
    for piece in pieces:
        assert piece not in joined
    assert "Kubernetes" in joined and "FastAPI" in joined


def test_a_private_key_on_screen_sends_none_of_its_lines():
    screen = "\n".join([
        "cat ~/.ssh/id_ed25519",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
        "QyNTUxOQAAACB+jTgB5sFpf4Mn/PlGvXq9t3YkR2wZ8hL0cN1mJ6uA7QAAAJgAbCdeAGwn",
        "jTgB5sFpf4Mn/PlGv",  # a short last line
        "-----END OPENSSH PRIVATE KEY-----",
        "KubeClient",
    ])
    context = AppContext(wm_class="term", window_title="Terminal", app_type=AppType.TERMINAL, screen_text=screen)
    vocab = build_vocabulary(Config(), context)
    assert "KubeClient" in vocab
    assert not any(piece in " ".join(vocab) for piece in ["b3Bl", "QyNT", "jTgB", "PlGv"])


def test_long_words_without_digits_and_dashed_words_are_not_mistaken_for_keys():
    vocab = _extract_vocab("snake_case_identifier_name Task-Management-Dashboard monkeyJumpsOverTheLazyDog")
    assert vocab == ["snake_case_identifier_name", "Task-Management-Dashboard", "monkeyJumpsOverTheLazyDog"]


def _context(title="main.py - Visual Studio Code", screen="def handleRequest(): KubeClient"):
    return AppContext(wm_class="code", window_title=title, app_type=AppType.EDITOR, screen_text=screen)


def test_screen_context_off_sends_only_the_dictionary():
    context = _context()
    on = build_vocabulary(Config(dictionary=["Vox"]), context)
    assert "handleRequest" in on and "KubeClient" in on

    config = Config(dictionary=["Vox"], context_screen=False)
    assert build_vocabulary(config, context) == ["Vox"]
    assert build_prompt(config, context) == "Vox"


@pytest.mark.anyio
async def test_screen_context_off_keeps_screen_words_out_of_the_request(fake_openai):
    config = Config(openai_api_key=KEY, dictionary=["Vox"], context_screen=False)
    await Transcriber(config).transcribe(WAV, _context())
    assert create_mock(fake_openai).call_args.kwargs["keywords"] == ["Vox"]


def test_vocabulary_is_deduplicated_and_capped():
    config = Config(dictionary=[f"Word{i}" for i in range(50)] + ["word1"])
    vocab = build_vocabulary(config, None)
    assert len(vocab) == 40
    assert vocab.count("Word1") == 1 and "word1" not in vocab
