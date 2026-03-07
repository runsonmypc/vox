"""Custom exception hierarchy for vox."""


class VoxError(Exception):
    """Base exception for all vox errors."""


class ConfigError(VoxError):
    """Configuration loading or validation error."""


class AudioError(VoxError):
    """Audio recording error."""


class TranscriptionError(VoxError):
    """Whisper API error."""


class InjectionError(VoxError):
    """Text injection (clipboard/paste) error."""


class DependencyError(VoxError):
    """Missing system dependency."""
