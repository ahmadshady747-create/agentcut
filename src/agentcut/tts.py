"""AgentCut TTS & Procedural Audio Engine (Phase 1):
Neural speech synthesis and procedural ambient generation for autonomous video pipelines.
"""

import asyncio
import os
import re
import subprocess
import shutil
from typing import Dict, List, Optional, Tuple, Any

try:
    import edge_tts
    _HAS_EDGE_TTS = True
except ImportError:
    edge_tts = None
    _HAS_EDGE_TTS = False


DEFAULT_VOICE_AR = "ar-SA-HamedNeural"
DEFAULT_VOICE_EN = "en-US-ChristopherNeural"


def is_arabic(text: str) -> bool:
    """Check if text contains Arabic characters."""
    return any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" for ch in text)


def detect_voice(text: str, preferred_voice: Optional[str] = None) -> str:
    """Select the best matching neural voice based on text language."""
    if preferred_voice and isinstance(preferred_voice, str) and preferred_voice.strip():
        return preferred_voice.strip()
    return DEFAULT_VOICE_AR if is_arabic(text) else DEFAULT_VOICE_EN


async def _synthesize_async(
    text: str,
    output_audio: str,
    output_srt: Optional[str] = None,
    voice: Optional[str] = None,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> Dict[str, Any]:
    """Internal async implementation of edge-tts synthesis."""
    if not _HAS_EDGE_TTS or edge_tts is None:
        raise RuntimeError(
            "edge-tts package is required for speech synthesis. "
            "Install it via: pip install edge-tts"
        )

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Text to synthesize cannot be empty.")

    selected_voice = detect_voice(text, voice)
    abs_audio = os.path.abspath(output_audio)
    os.makedirs(os.path.dirname(abs_audio), exist_ok=True)

    abs_srt = os.path.abspath(output_srt) if output_srt else None
    if abs_srt:
        os.makedirs(os.path.dirname(abs_srt), exist_ok=True)

    communicator = edge_tts.Communicate(
        text.strip(),
        voice=selected_voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        boundary="WordBoundary",
    )

    sub_maker = edge_tts.SubMaker() if abs_srt else None
    audio_data = bytearray()
    raw_words = []

    async for chunk in communicator.stream():
        chunk_type = chunk.get("type")
        if chunk_type == "audio":
            audio_data.extend(chunk.get("data", b""))
        elif chunk_type in ("WordBoundary", "SentenceBoundary"):
            if sub_maker:
                sub_maker.feed(chunk)
            if chunk_type == "WordBoundary":
                raw_words.append({
                    "text": chunk.get("text", ""),
                    "offset": chunk.get("offset", 0),
                    "duration": chunk.get("duration", 0),
                })

    if not audio_data:
        raise RuntimeError("No audio data received from speech synthesis service.")

    with open(abs_audio, "wb") as f_audio:
        f_audio.write(audio_data)

    duration = _probe_audio_duration(abs_audio)

    # Align word timings with tokenized text for BiDi/schema precision
    tokens = text.strip().split()
    word_timings = []
    prev_end = 0.0
    for wi, chunk_w in enumerate(raw_words):
        ws = max(prev_end, round(chunk_w["offset"] / 10_000_000, 3))
        we = max(ws + 0.05, round((chunk_w["offset"] + chunk_w["duration"]) / 10_000_000, 3))
        w_token = tokens[wi] if wi < len(tokens) else chunk_w["text"]
        word_timings.append({"word": w_token, "start": ws, "end": we})
        prev_end = we

    if len(word_timings) < len(tokens) and word_timings and duration > 0:
        last_end = word_timings[-1]["end"]
        step = max(0.1, (duration - last_end) / max(1, len(tokens) - len(word_timings)))
        for rem_i in range(len(word_timings), len(tokens)):
            ws = round(last_end, 3)
            we = round(last_end + step, 3)
            word_timings.append({"word": tokens[rem_i], "start": ws, "end": we})
            last_end = we

    # Clamp word timings to exact audio duration window
    if duration > 0:
        for w in word_timings:
            w["start"] = max(0.0, min(w["start"], duration))
            w["end"] = max(w["start"] + 0.02, min(w["end"], duration))

    cues_count = 0
    if abs_srt and sub_maker:
        srt_content = sub_maker.get_srt()
        if not srt_content.strip():
            # Fallback: if no boundary events fired, create one cue spanning full audio
            dur_srt = duration if duration > 0 else 5.0
            srt_content = f"1\n00:00:00,100 --> {_format_srt_time(dur_srt)}\n{text.strip()}\n"
            cues_count = 1
        else:
            cues_count = len(sub_maker.cues)

        with open(abs_srt, "w", encoding="utf-8") as f_srt:
            f_srt.write(srt_content)

    return {
        "audio": abs_audio,
        "captions": abs_srt,
        "duration": duration,
        "voice": selected_voice,
        "cues_count": cues_count,
        "text_length": len(text.strip()),
        "word_timings": word_timings,
    }


def synthesize_speech(
    text: str,
    output_audio: str,
    output_srt: Optional[str] = None,
    voice: Optional[str] = None,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> Dict[str, Any]:
    """Synchronous speech synthesis from text to audio (and optional SRT captions)."""
    return asyncio.run(
        _synthesize_async(
            text=text,
            output_audio=output_audio,
            output_srt=output_srt,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
    )


async def _list_voices_async(language_prefix: Optional[str] = None) -> List[Dict[str, str]]:
    """List available voices from edge_tts."""
    if not _HAS_EDGE_TTS or edge_tts is None:
        raise RuntimeError(
            "edge-tts package is required to list voices. "
            "Install it via: pip install edge-tts"
        )
    raw_voices = await edge_tts.list_voices()
    results = []
    prefix = (language_prefix.lower().strip() if language_prefix else None)

    for v in raw_voices:
        locale = v.get("Locale", "")
        if prefix:
            if not (locale.lower().startswith(prefix) or locale.lower().startswith(f"{prefix}-")):
                continue
        results.append({
            "name": v.get("ShortName", ""),
            "gender": v.get("Gender", ""),
            "locale": locale,
            "friendly_name": v.get("FriendlyName", ""),
        })
    return results


def list_voices(language_prefix: Optional[str] = None) -> List[Dict[str, str]]:
    """List available neural voices synchronously, optionally filtered by language prefix."""
    return asyncio.run(_list_voices_async(language_prefix))


def generate_ambient_audio(
    output_path: str,
    duration: float = 30.0,
    color: str = "pink",
    volume: float = 0.15,
) -> str:
    """Generate subtle procedural ambient background music/drone using FFmpeg lavfi anoisesrc.

    No external audio files or dependencies needed. Byte-reproducible with fixed seed.
    """
    abs_out = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    dur_str = f"{max(1.0, float(duration)):.2f}"
    vol_str = f"{max(0.01, min(1.0, float(volume))):.3f}"

    # anoisesrc pink noise lowpassed at 400Hz with volume scaling
    filter_chain = (
        f"anoisesrc=c={color}:r=44100:a=0.04:seed=42,"
        f"lowpass=f=400,volume={vol_str}"
    )

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", filter_chain,
        "-t", dur_str,
        "-ac", "2",
        abs_out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(abs_out):
        raise RuntimeError(f"FFmpeg ambient audio generation failed: {proc.stderr}")

    return abs_out


def _probe_audio_duration(path: str) -> float:
    """Read media duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass
    return 0.0


def _format_srt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS,mmm string."""
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
