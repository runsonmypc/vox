"""Local batch transcription with the whisper.cpp CLI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

from .audio import Resampler, mono_blocks, read_wav
from .config import Config
from .errors import ConfigError, TranscriptionError
from .transcribe import build_prompt, is_prompt_hallucination
from .window import AppContext

log = logging.getLogger(__name__)


def uses_screen_hints() -> bool:
    """Whether the whisper-cli prompt may carry window-title and screen words.

    Not on Linux: the prompt is a command-line argument, and other local accounts can read those
    in the process list there. The dictionary, which the user chose to write down, still goes.
    """
    return not sys.platform.startswith("linux")


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
    """Write a 16 kHz mono PCM16 WAV accepted by whisper-cli, converting a block at a time."""
    try:
        samples, rate, channels = read_wav(wav_bytes)
    except (EOFError, ValueError, wave.Error) as exc:
        raise TranscriptionError(f"Invalid WAV input for whisper.cpp: {exc}") from exc
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        for block in mono_blocks(samples, channels, Resampler(rate, 16000)):
            wf.writeframes(block)


def _out_of_gpu_memory(log_output: str) -> bool:
    """Whether whisper.cpp failed because the graphics card could not hold the model, as CUDA reports it."""
    return "out of memory" in log_output.lower()


async def _run(command: list[str]) -> tuple[int, str]:
    """Run whisper-cli to the end and return its exit status and log; a cancelled run is killed."""
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
    assert process.returncode is not None
    return process.returncode, stderr.decode("utf-8", errors="replace")


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
        prompt = build_prompt(config, context if uses_screen_hints() else None)

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

            started = time.monotonic()
            returncode, log_output = await _run(command)
            if returncode != 0 and _out_of_gpu_memory(log_output) and config.whisper_cpp_cpu_fallback:
                log.warning("whisper.cpp ran out of GPU memory; transcribing on the CPU instead")
                returncode, log_output = await _run([*command, "-ng"])
            if returncode != 0:
                if _out_of_gpu_memory(log_output):
                    fix = "" if config.whisper_cpp_cpu_fallback else ", or set [whisper_cpp] cpu_fallback = true"
                    raise TranscriptionError(f"whisper.cpp ran out of memory: close apps that use the graphics card{fix}")
                raise TranscriptionError(f"whisper.cpp exited with status {returncode}: {log_output.strip()[-1000:]}")
            log.debug("whisper.cpp finished in %.2fs", time.monotonic() - started)
            for line in log_output.splitlines():
                if line.startswith("whisper_print_timings"):  # model load, encode and decode times
                    log.debug("%s", line.strip())
            try:
                # A segment line can end inside a multi-byte character (tokens are bytes); keep the
                # transcript with a replacement character rather than lose it
                text = output_base.with_suffix(".txt").read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                raise TranscriptionError(f"whisper.cpp did not write a transcript: {exc}") from exc

        if prompt and is_prompt_hallucination(text, prompt):
            log.warning("Dropped a whisper.cpp transcript that only echoed the prompt")
            log.debug("Dropped echo: %s", text)
            return ""
        log.info("Transcript: %d chars", len(text))
        log.debug("Transcript: %s", text)
        return text
