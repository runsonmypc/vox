"""Exercise the local CLI boundary with a stand-in whisper-cli executable."""

import asyncio
import json
import os
import sys
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vox.config import Config
from vox.daemon import _process
from vox.errors import ConfigError, TranscriptionError
from vox.history import HistoryDB
from vox.whisper_cpp import WhisperCppTranscriber
from vox.window import AppContext, AppType


def _wav(rate=48000, channels=2):
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setframerate(rate)
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.writeframes(np.arange(rate // 10 * channels, dtype="<i2").tobytes())
    return buffer.getvalue()


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
async def test_local_cli_receives_converted_audio_prompt_and_model(tmp_path):
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
    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste") as paste:
        await _process(
            wav_data=_wav(), config=config, batch_transcriber=transcriber,
            streaming_transcriber=None, stream_task=None, sounds=MagicMock(),
            queue=queue, context=context, screen_capture_future=None, history=history,
        )
    transcriber.transcribe.assert_awaited_once()
    paste.assert_called_once_with("alex@example.com", AppType.EMAIL)
    assert history.recent(1)[0].transcription_mode == "whisper_cpp"
    assert queue.get_nowait() == "process_done"
    history.close()
