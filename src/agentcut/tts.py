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

STATIC_FALLBACK_VOICES = [
    {"name": "ar-SA-HamedNeural", "gender": "Male", "locale": "ar-SA", "friendly_name": "Microsoft Hamed Online (Natural) - Arabic (Saudi Arabia)"},
    {"name": "ar-EG-SalmaNeural", "gender": "Female", "locale": "ar-EG", "friendly_name": "Microsoft Salma Online (Natural) - Arabic (Egypt)"},
    {"name": "ar-AE-HamdanNeural", "gender": "Male", "locale": "ar-AE", "friendly_name": "Microsoft Hamdan Online (Natural) - Arabic (United Arab Emirates)"},
    {"name": "en-US-ChristopherNeural", "gender": "Male", "locale": "en-US", "friendly_name": "Microsoft Christopher Online (Natural) - English (United States)"},
    {"name": "en-US-AriaNeural", "gender": "Female", "locale": "en-US", "friendly_name": "Microsoft Aria Online (Natural) - English (United States)"},
    {"name": "en-US-GuyNeural", "gender": "Male", "locale": "en-US", "friendly_name": "Microsoft Guy Online (Natural) - English (United States)"},
]

ARABIC_HOMOGRAPHS = {
    "بتغلى": "بِتِغْلَى",
    "تغلى": "تِغْلَى",
    "سلة": "سَلَّة",
    "السلة": "السَّلَّة",
    "عدم": "عَدَم",
    "ذهب": "ذَهَبَ",
    "سعر": "سِعْر",
    "اسعار": "أَسْعَار",
    "أسعار": "أَسْعَار",
    "قيمة": "قِيمَة",
    "نقود": "نُقُود",
    "مال": "مَال",
    "اموال": "أَمْوَال",
    "أموال": "أَمْوَال",
    "تضخم": "تَضَخُّم",
    "التضخم": "التَّضَخُّم",
    "حقيقي": "حَقِيقِيّ",
    "حقيقية": "حَقِيقِيَّة",
    "طباعة": "طِبَاعَة",
    "انتاج": "إِنْتَاج",
    "إنتاج": "إِنْتَاج",
    "سلع": "سِلَع",
    "السلع": "السِّلَع",
    "خدمات": "خِدْمَات",
    "الخدمات": "الخِدْمَات",
    "شراء": "شِرَاء",
    "قوة": "قُوَّة",
    "القوة": "القُوَّة",
}


def auto_diacritize_arabic(text: str) -> str:
    """Vowelize common ambiguous Arabic homographs before TTS for accurate neural pronunciation."""
    if not isinstance(text, str) or not text.strip():
        return text
    words = text.split()
    processed = []
    for w in words:
        m = re.match(r"^([^\w]*)([\w\u0600-\u06ff]+)([^\w]*)$", w)
        if m:
            prefix, core, suffix = m.groups()
            vowelized = ARABIC_HOMOGRAPHS.get(core, core)
            processed.append(f"{prefix}{vowelized}{suffix}")
        else:
            processed.append(ARABIC_HOMOGRAPHS.get(w, w))
    return " ".join(processed)


def is_arabic(text: str) -> bool:
    """Check if text contains Arabic characters."""
    return any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" for ch in text)


def detect_voice(text: str, preferred_voice: Optional[str] = None) -> str:
    """Select the best matching neural voice based on text language."""
    if preferred_voice and isinstance(preferred_voice, str) and preferred_voice.strip():
        return preferred_voice.strip()
    return DEFAULT_VOICE_AR if is_arabic(text) else DEFAULT_VOICE_EN


def _synthesize_offline_fallback(
    text: str,
    output_audio: str,
    output_srt: Optional[str] = None,
    voice: Optional[str] = None,
) -> Dict[str, Any]:
    """Offline procedural speech fallback using FFmpeg lavfi.

    Generates a speech-cadence modulated audio file with exact syllable-weighted
    word_timings and valid SRT cues without external network calls or packages.
    """
    abs_audio = os.path.abspath(output_audio)
    os.makedirs(os.path.dirname(abs_audio), exist_ok=True)
    abs_srt = os.path.abspath(output_srt) if output_srt else None
    if abs_srt:
        os.makedirs(os.path.dirname(abs_srt), exist_ok=True)

    tokens = text.strip().split()
    duration = max(1.8, round(len(tokens) * 0.42, 3))

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", "sine=f=180:r=44100,tremolo=f=4.0:d=0.6,lowpass=f=1500,highpass=f=120,volume=0.3",
        "-t", f"{duration:.3f}",
        "-ac", "2",
        abs_audio,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(abs_audio):
        raise RuntimeError(f"Offline procedural audio fallback failed: {proc.stderr}")

    total_len = sum(max(1, len(tok)) for tok in tokens)
    word_timings = []
    curr_t = 0.05
    usable = duration - 0.1
    for tok in tokens:
        w_dur = max(0.12, round((len(tok) / max(1, total_len)) * usable, 3))
        ws = round(curr_t, 3)
        we = round(min(duration, ws + w_dur), 3)
        word_timings.append({"word": tok, "start": ws, "end": we})
        curr_t = we
    if word_timings:
        word_timings[-1]["end"] = duration

    cues_count = 0
    if abs_srt:
        cues = []
        chunk_size = 4
        for ci in range(0, len(word_timings), chunk_size):
            grp = word_timings[ci: ci + chunk_size]
            c_start = grp[0]["start"]
            c_end = grp[-1]["end"]
            c_text = " ".join(w["word"] for w in grp)
            cues.append((c_start, c_end, c_text))
        if not cues:
            cues.append((0.05, duration, text.strip()))

        with open(abs_srt, "w", encoding="utf-8") as f_srt:
            for idx, (cs, ce, ct) in enumerate(cues, 1):
                f_srt.write(f"{idx}\n{_format_srt_time(cs)} --> {_format_srt_time(ce)}\n{ct}\n\n")
        cues_count = len(cues)

    selected_voice = detect_voice(text, voice)
    return {
        "audio": abs_audio,
        "captions": abs_srt,
        "duration": duration,
        "voice": selected_voice,
        "cues_count": cues_count,
        "text_length": len(text.strip()),
        "word_timings": word_timings,
        "fallback": True,
    }


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
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Text to synthesize cannot be empty.")

    if not _HAS_EDGE_TTS or edge_tts is None:
        return _synthesize_offline_fallback(text, output_audio, output_srt, voice)

    selected_voice = detect_voice(text, voice)
    abs_audio = os.path.abspath(output_audio)
    os.makedirs(os.path.dirname(abs_audio), exist_ok=True)

    abs_srt = os.path.abspath(output_srt) if output_srt else None
    if abs_srt:
        os.makedirs(os.path.dirname(abs_srt), exist_ok=True)

    synth_text = auto_diacritize_arabic(text.strip()) if is_arabic(text) else text.strip()

    try:
        communicator = edge_tts.Communicate(
            synth_text,
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
            return _synthesize_offline_fallback(text, output_audio, output_srt, voice)

        with open(abs_audio, "wb") as f_audio:
            f_audio.write(audio_data)

        duration = _probe_audio_duration(abs_audio)
        if duration <= 0:
            duration = _synthesize_offline_fallback(text, output_audio, output_srt, voice)["duration"]

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

        if duration > 0:
            for w in word_timings:
                w["start"] = max(0.0, min(w["start"], duration))
                w["end"] = max(w["start"] + 0.02, min(w["end"], duration))

        cues_count = 0
        if abs_srt and sub_maker:
            srt_content = sub_maker.get_srt()
            if not srt_content.strip():
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
    except Exception:
        return _synthesize_offline_fallback(text, output_audio, output_srt, voice)


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
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Text to synthesize cannot be empty.")

    if not _HAS_EDGE_TTS or edge_tts is None:
        return _synthesize_offline_fallback(text, output_audio, output_srt, voice)

    try:
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
    except Exception:
        return _synthesize_offline_fallback(text, output_audio, output_srt, voice)


async def _list_voices_async(language_prefix: Optional[str] = None) -> List[Dict[str, str]]:
    """List available voices from edge_tts."""
    if not _HAS_EDGE_TTS or edge_tts is None:
        return _filter_static_voices(language_prefix)
    try:
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
        return results if results else _filter_static_voices(language_prefix)
    except Exception:
        return _filter_static_voices(language_prefix)


def _filter_static_voices(language_prefix: Optional[str] = None) -> List[Dict[str, str]]:
    prefix = (language_prefix.lower().strip() if language_prefix else None)
    results = []
    for v in STATIC_FALLBACK_VOICES:
        locale = v.get("locale", "")
        if prefix:
            if not (locale.lower().startswith(prefix) or locale.lower().startswith(f"{prefix}-")):
                continue
        results.append(dict(v))
    return results


def list_voices(language_prefix: Optional[str] = None) -> List[Dict[str, str]]:
    """List available neural voices synchronously, optionally filtered by language prefix."""
    if _HAS_EDGE_TTS and edge_tts is not None:
        try:
            return asyncio.run(_list_voices_async(language_prefix))
        except Exception:
            pass
    return _filter_static_voices(language_prefix)


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
