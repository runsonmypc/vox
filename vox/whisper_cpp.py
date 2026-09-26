"""Local batch transcription with the whisper.cpp CLI."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

from .config import Config
from .errors import ConfigError, TranscriptionError
from .transcribe import build_prompt, is_prompt_hallucination
from .window import AppContext

log = logging.getLogger(__name__)


def _configured_path(value: str, config: Config) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and config.config_path is not None:
        path = config.config_path.parent / path
    return path


def _resolve_binary(config: Config) -> str:
    name = config.whisper_cpp_binary
    if not name:
        raise ConfigError("[whisper_cpp].binary must name a whisper-cli executable")
    if "/" not in name and "\\" not in name:
        found = shutil.which(name)
    else:
        found = shutil.which(str(_configured_path(name, config)))
    if found is None:
        raise ConfigError(f"whisper.cpp executable not found: {name}")
    return found


def _resolve_model(config: Config) -> Path:
    if not config.whisper_cpp_model:
        raise ConfigError("[whisper_cpp].model must point to a GGML model file")
    model = _configured_path(config.whisper_cpp_model, config)
    if not model.is_file():
        raise ConfigError(f"whisper.cpp model not found: {model}")
    return model


def _write_input(wav_bytes: bytes, path: Path) -> None:
    """Write a 16 kHz mono PCM16 WAV accepted by whisper-cli."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            rate = source.getframerate()
            channels = source.getnchannels()
            if source.getsampwidth() != 2 or channels < 1 or rate < 1:
                raise TranscriptionError("whisper.cpp requires 16-bit PCM WAV input")
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    except (EOFError, wave.Error) as exc:
        raise TranscriptionError(f"Invalid WAV input for whisper.cpp: {exc}") from exc

    if channels > 1:
        samples = samples.reshape(-1, channels).astype(np.float32).mean(axis=1)
    if rate != 16000 and len(samples):
        target_len = round(len(samples) * 16000 / rate)
        samples = np.interp(
            np.arange(target_len) * rate / 16000,
            np.arange(len(samples)),
            samples,
        )

    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(np.asarray(samples, dtype="<i2").tobytes())


class WhisperCppTranscriber:
    """Run one local whisper-cli process for each recorded utterance."""

    def __init__(self, config: Config) -> None:
        self._config = config
        _resolve_binary(config)
        _resolve_model(config)

    async def transcribe(self, wav_bytes: bytes, context: AppContext | None = None) -> str:
        config = self._config
        binary = _resolve_binary(config)
        model = _resolve_model(config)
        prompt = build_prompt(config, context)

        with tempfile.TemporaryDirectory(prefix="vox-whisper-") as directory:
            input_path = Path(directory) / "input.wav"
            output_base = Path(directory) / "transcript"
            await asyncio.to_thread(_write_input, wav_bytes, input_path)
            command = [binary, "-m", str(model), "-f", str(input_path),
                       "-otxt", "-of", str(output_base), "-nt"]
            if config.whisper_language:
                command.extend(["-l", config.whisper_language])
            else:
                command.extend(["-l", "auto"])
            if prompt:
                command.extend(["--prompt", prompt])

            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise TranscriptionError(f"Could not start whisper.cpp: {exc}") from exc
            try:
                _, stderr = await process.communicate()
            except asyncio.CancelledError:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                await process.communicate()
                raise

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[-1000:]
                raise TranscriptionError(f"whisper.cpp exited with status {process.returncode}: {detail}")
            try:
                text = output_base.with_suffix(".txt").read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise TranscriptionError(f"whisper.cpp did not write a transcript: {exc}") from exc

        if prompt and is_prompt_hallucination(text, prompt):
            log.warning("Detected whisper.cpp prompt hallucination, dropping transcript")
            return ""
        log.info("Transcript: %s", text)
        return text
