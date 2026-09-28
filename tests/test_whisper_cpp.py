"""Exercise the local CLI boundary with a stand-in whisper-cli executable."""

import asyncio
import io
import json
import logging
import os
import sys
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vox import whisper_cpp
from vox.audio import to_16k_mono
from vox.config import Config
from vox.daemon import _process
from vox.errors import ConfigError, TranscriptionError
from vox.history import HistoryDB
from vox.whisper_cpp import WhisperCppTranscriber
from vox.window import AppContext, AppType


def _wav(rate=48000, channels=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setframerate(rate)
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.writeframes(np.arange(rate // 10 * channels, dtype="<i2").tobytes())
    return buffer.getvalue()


@pytest.fixture
def screen_hints(monkeypatch):
    """The macOS behavior, where the prompt may carry title and screen words, on any test machine."""
    monkeypatch.setattr(whisper_cpp, "uses_screen_hints", lambda: True)


def _config(tmp_path, script):
    binary = tmp_path / "bin" / "whisper-cli"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + script)
    binary.chmod(0o755)
    model = tmp_path / "models" / "ggml-test.bin"
    model.parent.mkdir()
    model.write_bytes(b"model")
    config = Config(
        mode="whisper_cpp", whisper_cpp_binary="bin/whisper-cli",
        whisper_cpp_model="models/ggml-test.bin", whisper_language="en",
        whisper_prompt="FastAPI", dictionary=["Kubernetes"],
    )
    config._config_path = tmp_path / "config.toml"
    return config


@pytest.mark.anyio
async def test_local_cli_receives_converted_audio_prompt_and_model(tmp_path, screen_hints):
    script = '''import json, sys, wave
from pathlib import Path
a = sys.argv
audio = Path(a[a.index("-f") + 1])
with wave.open(str(audio), "rb") as wav:
    assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (16000, 1, 2, 1600)
Path(a[a.index("-m") + 1]).read_bytes()
Path(__file__).with_suffix(".json").write_text(json.dumps({"args": a, "audio": str(audio)}))
Path(a[a.index("-of") + 1] + ".txt").write_text("  deploy Kubernetes cluster.  ")
'''
    config = _config(tmp_path, script)
    context = AppContext("code", "VSCode Terminal", AppType.TERMINAL)
    text = await WhisperCppTranscriber(config).transcribe(_wav(), context)

    assert text == "deploy Kubernetes cluster."
    recorded = json.loads((tmp_path / "bin" / "whisper-cli.json").read_text())
    args = recorded["args"]
    assert args[args.index("-l") + 1] == "en"
    assert args[args.index("--prompt") + 1] == "FastAPI\nKubernetes, VSCode"
    assert "-otxt" in args and "-nt" in args
    assert not Path(recorded["audio"]).exists()


@pytest.mark.anyio
async def test_local_cli_reports_failures_and_requires_model(tmp_path):
    config = _config(tmp_path, 'import sys; sys.stderr.write("bad model\\n"); sys.exit(3)\n')
    with pytest.raises(TranscriptionError, match="status 3: bad model"):
        await WhisperCppTranscriber(config).transcribe(_wav())
    config.whisper_cpp_model = "missing.bin"
    with pytest.raises(ConfigError, match="model not found"):
        WhisperCppTranscriber(config)


@pytest.mark.anyio
async def test_cancelling_local_transcription_stops_cli(tmp_path):
    config = _config(tmp_path, '''import os, time
from pathlib import Path
Path(__file__).with_suffix(".pid").write_text(str(os.getpid()))
time.sleep(60)
''')
    task = asyncio.create_task(WhisperCppTranscriber(config).transcribe(_wav()))
    pid_file = tmp_path / "bin" / "whisper-cli.pid"
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.anyio
async def test_local_mode_uses_batch_pipeline_and_records_mode(tmp_path):
    config = Config(mode="whisper_cpp", snippets={"my email": "alex@example.com"})
    context = AppContext("mail", "Mail", AppType.EMAIL)
    transcriber = MagicMock()
    transcriber.transcribe = AsyncMock(return_value="my email.")
    history = HistoryDB(tmp_path / "history.db")
    queue = asyncio.Queue()
    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste") as paste, \
         patch("vox.daemon.detect_active_window", return_value=AppContext("", "", AppType.OTHER)):
        await _process(
            wav_data=_wav(), config=config, batch_transcriber=transcriber,
            streaming_transcriber=None, stream_task=None, sounds=MagicMock(),
            queue=queue, context=context, screen_capture_future=None, mode="whisper_cpp", history=history,
        )
    transcriber.transcribe.assert_awaited_once()
    paste.assert_called_once_with("alex@example.com", AppType.EMAIL)
    assert history.recent(1)[0].transcription_mode == "whisper_cpp"
    assert queue.get_nowait() == "process_done"
    history.close()


_COPY_INPUT = '''import shutil, sys
from pathlib import Path
a = sys.argv
shutil.copy(a[a.index("-f") + 1], Path(__file__).with_name("input-copy.wav"))
Path(__file__).with_name("argv.txt").write_text("\\n".join(a))
sys.stderr.write("whisper_init_from_file: loading model\\n")
sys.stderr.write("whisper_print_timings:     load time =    12.00 ms\\n")
sys.stderr.write("whisper_print_timings:    total time =    99.00 ms\\n")
Path(a[a.index("-of") + 1] + ".txt").write_text("my private note")
'''


@pytest.mark.anyio
@pytest.mark.parametrize("rate", [48000, 44100])
async def test_local_input_is_16k_mono_and_matches_the_recording(tmp_path, rate):
    config = _config(tmp_path, _COPY_INPUT)
    t = np.arange(rate // 2) / rate
    # A voice-band tone, plus a 12 kHz one that plain decimation would fold down to 4 kHz
    audio = (np.sin(2 * np.pi * 1000 * t) * 8000 + np.sin(2 * np.pi * 12000 * t) * 8000).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setframerate(rate)
        output.setnchannels(1)
        output.setsampwidth(2)
        output.writeframes(audio.tobytes())

    await WhisperCppTranscriber(config).transcribe(buffer.getvalue())

    with wave.open(str(tmp_path / "bin" / "input-copy.wav"), "rb") as sent:
        assert (sent.getframerate(), sent.getnchannels(), sent.getnframes()) == (16000, 1, 8000)
        samples = np.frombuffer(sent.readframes(sent.getnframes()), dtype="<i2")
    np.testing.assert_array_equal(samples, to_16k_mono(audio, rate))  # the same conversion as uploads
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    bins = np.fft.rfftfreq(len(samples), 1 / 16000)
    assert spectrum[bins == 4000] < spectrum[bins == 1000] / 100


@pytest.mark.anyio
async def test_local_run_logs_timings_at_debug_and_text_never_at_info(tmp_path, caplog):
    config = _config(tmp_path, _COPY_INPUT)

    with caplog.at_level(logging.INFO, logger="vox.whisper_cpp"):
        assert await WhisperCppTranscriber(config).transcribe(_wav()) == "my private note"
    assert "Transcript: 15 chars" in caplog.text
    assert "private" not in caplog.text
    assert "whisper_print_timings" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="vox.whisper_cpp"):
        await WhisperCppTranscriber(config).transcribe(_wav())
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(m.startswith("whisper.cpp finished in") for m in debug)
    assert "whisper_print_timings:     load time =    12.00 ms" in debug
    assert "whisper_print_timings:    total time =    99.00 ms" in debug
    assert not any("loading model" in m for m in debug)
    assert "Transcript: my private note" in debug


@pytest.mark.anyio
async def test_local_prompt_never_carries_secrets_from_the_screen(tmp_path, screen_hints):
    config = _config(tmp_path, _COPY_INPUT)
    context = AppContext(
        "code", "settings.env", AppType.EDITOR,
        screen_text="OPENAI_API_KEY=sk-proj-Abc123Def456Ghi789Jkl\nDeploymentConfig",
    )
    await WhisperCppTranscriber(config).transcribe(_wav(), context)
    argv = (tmp_path / "bin" / "argv.txt").read_text()
    assert "sk-proj" not in argv
    assert "DeploymentConfig" in argv


@pytest.mark.anyio
async def test_local_prompt_never_carries_passwords_from_urls_or_command_lines(tmp_path, screen_hints):
    config = _config(tmp_path, _COPY_INPUT)
    screen = "\n".join([
        "DATABASE_URL=postgres://app:Xy7pQ9zRw2@db.internal:5432/prod",
        "mysql -uroot -pS3cr3tRoot",
        "curl -u alice:S3cretPass https://example.com",
        "password: Correct Horse Battery Staple",
        "DeploymentConfig",
    ])
    context = AppContext("term", "psql --password Hunter2Xyz", AppType.TERMINAL, screen_text=screen)
    await WhisperCppTranscriber(config).transcribe(_wav(), context)
    argv = (tmp_path / "bin" / "argv.txt").read_text()
    for secret in ["Xy7pQ9zRw2", "S3cr3tRoot", "S3cretPass", "Horse", "Staple", "Hunter2Xyz"]:
        assert secret not in argv
    assert "DATABASE_URL" in argv and "DeploymentConfig" in argv



def test_screen_hints_stay_out_of_the_prompt_only_on_linux(monkeypatch):
    for platform, hints in (("linux", False), ("darwin", True)):
        monkeypatch.setattr(whisper_cpp.sys, "platform", platform)
        assert whisper_cpp.uses_screen_hints() is hints


@pytest.mark.anyio
async def test_local_prompt_on_linux_holds_only_the_dictionary(tmp_path, monkeypatch):
    """Other local accounts can read the whisper-cli arguments in the Linux process list."""
    monkeypatch.setattr(whisper_cpp, "uses_screen_hints", lambda: False)
    config = _config(tmp_path, _COPY_INPUT)
    context = AppContext("mail", "Re: Offer letter for Jane Doe", AppType.EMAIL, screen_text="AcmePayroll salary")
    await WhisperCppTranscriber(config).transcribe(_wav(), context)
    argv = (tmp_path / "bin" / "argv.txt").read_text()
    assert "FastAPI" in argv and "Kubernetes" in argv
    for word in ["Offer", "Jane", "AcmePayroll"]:
        assert word not in argv


@pytest.mark.anyio
async def test_local_cli_rejects_invalid_audio_before_running(tmp_path):
    config = _config(tmp_path, _COPY_INPUT)
    with pytest.raises(TranscriptionError, match="Invalid WAV input"):
        await WhisperCppTranscriber(config).transcribe(b"not audio")
    assert not (tmp_path / "bin" / "argv.txt").exists()


@pytest.mark.anyio
async def test_a_transcript_that_is_not_valid_utf8_is_kept(tmp_path):
    # whisper.cpp tokens are bytes, so a segment line can end inside a character: here "日" is split
    char = "日".encode()
    output = b"Hello " + char[:2] + b"\n" + char[2:] + b" world"
    script = f"""import sys
from pathlib import Path
a = sys.argv
Path(a[a.index("-of") + 1] + ".txt").write_bytes({output!r})
"""
    text = await WhisperCppTranscriber(_config(tmp_path, script)).transcribe(_wav())
    assert text.startswith("Hello ") and text.endswith(" world")
    assert "�" in text


GPU_FULL = '''import json, sys
from pathlib import Path
a = sys.argv
calls = Path(__file__).with_suffix(".calls")
calls.write_text(calls.read_text() + json.dumps(a[1:]) + "\\n" if calls.exists() else json.dumps(a[1:]) + "\\n")
if "-ng" not in a:
    sys.stderr.write("ggml_backend_cuda_buffer_type_alloc_buffer: allocating 1548.69 MiB on device 0: cudaMalloc failed: out of memory\\n")
    sys.stderr.flush()
    sys.exit(1)
Path(a[a.index("-of") + 1] + ".txt").write_text("on the processor")
'''


def _calls(tmp_path):
    return [json.loads(line) for line in (tmp_path / "bin" / "whisper-cli.calls").read_text().splitlines()]


@pytest.mark.anyio
async def test_a_full_graphics_card_falls_back_to_the_cpu(tmp_path, caplog):
    config = _config(tmp_path, GPU_FULL)
    with caplog.at_level(logging.WARNING, logger="vox.whisper_cpp"):
        text = await WhisperCppTranscriber(config).transcribe(_wav())

    assert text == "on the processor"
    first, second = _calls(tmp_path)
    assert "-ng" not in first and second == [*first, "-ng"]
    assert "transcribing on the CPU instead" in caplog.text


@pytest.mark.anyio
async def test_without_the_fallback_a_full_graphics_card_says_so(tmp_path):
    config = _config(tmp_path, GPU_FULL)
    config.whisper_cpp_cpu_fallback = False
    with pytest.raises(TranscriptionError, match=r"ran out of memory: close apps that use the graphics card, "
                                                 r"or set \[whisper_cpp\] cpu_fallback = true$"):
        await WhisperCppTranscriber(config).transcribe(_wav())
    assert len(_calls(tmp_path)) == 1


@pytest.mark.anyio
async def test_a_cpu_run_that_also_runs_out_of_memory_fails_once(tmp_path):
    script = GPU_FULL.replace('if "-ng" not in a:', "if True:")
    config = _config(tmp_path, script)
    with pytest.raises(TranscriptionError, match="ran out of memory: close apps that use the graphics card$"):
        await WhisperCppTranscriber(config).transcribe(_wav())
    assert len(_calls(tmp_path)) == 2


@pytest.mark.anyio
async def test_other_failures_are_not_retried_on_the_cpu(tmp_path):
    script = GPU_FULL.replace("cudaMalloc failed: out of memory", "failed to load model")
    config = _config(tmp_path, script)
    with pytest.raises(TranscriptionError, match="failed to load model"):
        await WhisperCppTranscriber(config).transcribe(_wav())
    assert len(_calls(tmp_path)) == 1
