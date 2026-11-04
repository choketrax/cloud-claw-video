#!/usr/bin/env python3
"""Local Whisper transcription adapter for Cloud-Claw / video-use.

Replaces the cloud-only ElevenLabs Scribe dependency with local faster-whisper.
Produces Scribe-compatible word-level JSON under <edit_dir>/transcripts/<clip_stem>.json
so that video-use's pack_transcripts.py, render.py, and timeline tools work seamlessly.

Schema compatibility:
{
    "text": "...",
    "duration": 12.34,
    "words": [
        {
            "type": "word",
            "text": "hello",
            "start": 0.12,
            "end": 0.45,
            "speaker_id": "speaker_0"
        },
        ...
    ]
}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("whisper_transcribe")


def extract_audio(video_path: Path, output_wav: Path) -> None:
    """Extract 16kHz mono WAV using ffmpeg for maximum whisper stability."""
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_wav),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        logger.error("ffmpeg audio extraction failed: %s", res.stderr)
        raise RuntimeError(f"ffmpeg extraction failed: {res.stderr}")


def transcribe_file(
    input_media: Path,
    output_json: Path,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    beam_size: int = 5,
    vad_filter: bool = True,
) -> dict:
    """Transcribe audio with faster-whisper and write Scribe-compatible JSON."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper is not installed. Install via pip/uv inside video-venv.")
        sys.exit(1)

    logger.info("Loading faster-whisper model '%s' (device=%s, compute=%s)...", model_size, device, compute_type)
    # Check for local model cache directory
    download_root = os.environ.get("WHISPER_MODELS_DIR", "/opt/cloudclaw/cache/whisper")
    os.makedirs(download_root, exist_ok=True)

    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = Path(tmpdir) / "extracted.wav"
        logger.info("Extracting audio from %s...", input_media.name)
        extract_audio(input_media, wav_path)

        logger.info("Transcribing %s with word timestamps...", input_media.name)
        segments, info = model.transcribe(
            str(wav_path),
            beam_size=beam_size,
            word_timestamps=True,
            language=language if language and language != "auto" else None,
            vad_filter=vad_filter,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        words_payload = []
        full_text_parts = []
        last_end = 0.0

        for seg in segments:
            full_text_parts.append(seg.text.strip())
            for word in seg.words:
                w_text = word.word.strip()
                if not w_text:
                    continue

                w_start = round(float(word.start), 3)
                w_end = round(float(word.end), 3)

                # Optional: insert spacing token if gap is significant
                if w_start > last_end + 0.08 and last_end > 0:
                    words_payload.append({
                        "type": "spacing",
                        "text": " ",
                        "start": last_end,
                        "end": w_start,
                    })

                words_payload.append({
                    "type": "word",
                    "text": w_text,
                    "start": w_start,
                    "end": w_end,
                    "speaker_id": "speaker_0",
                    "probability": round(float(word.probability), 3),
                })
                last_end = w_end

        total_duration = round(float(info.duration), 3) if info.duration else (last_end if last_end > 0 else 0.0)

        data = {
            "text": " ".join(full_text_parts),
            "duration": total_duration,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "words": words_payload,
            "provider": "faster-whisper",
            "model": model_size,
        }

        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Successfully wrote Scribe-compatible transcript to %s (%d words)", output_json, len(words_payload))
        return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Whisper transcription for video-use.")
    parser.add_argument("input_file", type=Path, help="Path to input video or audio file")
    parser.add_argument("--edit-dir", type=Path, default=None, help="Root edit directory (writes to <edit-dir>/transcripts/<stem>.json)")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Direct output JSON file path")
    parser.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "small"), help="Whisper model size (tiny, base, small, medium, large-v3)")
    parser.add_argument("--device", default=os.environ.get("WHISPER_DEVICE", "cpu"), help="Device: cpu or cuda")
    parser.add_argument("--compute-type", default=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"), help="int8, float16, float32")
    parser.add_argument("--language", default=None, help="Language code (e.g. en, es) or auto")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached transcript")
    args = parser.parse_args()

    input_path = args.input_file.resolve()
    if not input_path.exists():
        sys.exit(f"Input file not found: {input_path}")

    if args.output:
        out_json = args.output.resolve()
    elif args.edit_dir:
        out_json = (args.edit_dir / "transcripts" / f"{input_path.stem}.json").resolve()
    else:
        out_json = (input_path.parent / "edit" / "transcripts" / f"{input_path.stem}.json").resolve()

    if out_json.exists() and not args.force:
        logger.info("Transcript already exists at %s. Use --force to regenerate.", out_json)
        sys.exit(0)

    transcribe_file(
        input_media=input_path,
        output_json=out_json,
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
    )


if __name__ == "__main__":
    main()
