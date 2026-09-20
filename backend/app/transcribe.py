"""Local speech-to-text via whisper.cpp. Audio never leaves the machine."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import get_preferences, get_settings

log = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    pass


def to_wav(source: Path) -> Path:
    """Convert any input to the 16 kHz mono PCM whisper.cpp requires.

    Telegram voice notes arrive as OGG/Opus and the macOS app sends m4a or wav;
    normalising here keeps the rest of the pipeline format-agnostic.
    """
    if shutil.which("ffmpeg") is None:
        raise TranscriptionError("ffmpeg not found on PATH")
    target = Path(tempfile.mkdtemp()) / "audio.wav"
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(source),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.exists():
        raise TranscriptionError(f"ffmpeg failed: {result.stderr[-500:]}")
    return target


def transcribe(source: Path) -> str:
    """Transcribe an audio file to text."""
    settings = get_settings()
    prefs = get_preferences()

    cli = str(settings.whisper_cli)
    if shutil.which(cli) is None and not Path(cli).exists():
        raise TranscriptionError(
            f"whisper.cpp CLI not found at {cli!r}. Build whisper.cpp and set "
            f"WHISPER_CLI in .env."
        )
    if not settings.whisper_model_path.exists():
        raise TranscriptionError(
            f"whisper model not found at {settings.whisper_model_path}. "
            f"Download one with whisper.cpp's models/download-ggml-model.sh."
        )

    wav = to_wav(source)
    command = [
        cli,
        "-m", str(settings.whisper_model_path),
        "-f", str(wav),
        "--no-timestamps",
        "--output-txt",
        "--output-file", str(wav.with_suffix("")),
    ]
    # "auto" lets whisper detect English vs Lithuanian per clip, which matters
    # for a user who switches between them mid-sentence.
    if prefs.whisper_language and prefs.whisper_language != "auto":
        command += ["-l", prefs.whisper_language]
    else:
        command += ["-l", "auto"]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise TranscriptionError(f"whisper.cpp failed: {result.stderr[-500:]}")

    transcript_file = wav.with_suffix(".txt")
    text = (
        transcript_file.read_text(encoding="utf-8")
        if transcript_file.exists()
        else result.stdout
    )
    text = text.strip()
    if not text:
        raise TranscriptionError("transcription produced no text")
    log.info("transcribed %d chars", len(text))
    return text
