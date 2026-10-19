"""
In-memory Digital Signal Processing (DSP) for Discord Voice Co-Hosting (`src/skills/voice/engine/vad.py`):
- RMS energy (dBFS) Voice Activity Detection (VAD) on 16-bit signed PCM frames.
- 48 kHz stereo PCM (`s16le`) -> 16 kHz mono WAV in-memory encoder for STT (`gemma4a` / `faster-whisper`).
- MP3/WAV -> 48 kHz stereo PCM (`s16le`) in-memory decoder via `miniaudio` / `wave` for zero-FFmpeg Discord playback.
- Wake-word detection and spoken-text sanitization for natural neural TTS output.
"""

from __future__ import annotations

import io
import math
import re
from typing import Iterable, Tuple
import wave

import miniaudio
import numpy as np

from src.skills.voice.catalogs import DEFAULT_WAKE_WORDS


def compute_pcm_rms_dbfs(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """
    Compute the Root-Mean-Square (RMS) level in dBFS (`[-96.0, 0.0]`) for 16-bit signed PCM bytes.
    """
    if not pcm_bytes or len(pcm_bytes) < sample_width:
        return -96.0
    trim_len = (len(pcm_bytes) // sample_width) * sample_width
    samples = np.frombuffer(pcm_bytes[:trim_len], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return -96.0
    rms = float(np.sqrt(np.mean((samples / 32768.0) ** 2)))
    if rms <= 1e-5:
        return -96.0
    return float(max(-96.0, min(0.0, 20.0 * math.log10(rms))))


def is_speech_frame(pcm_bytes: bytes, threshold_dbfs: float = -42.0) -> bool:
    """Return True if the PCM audio frame's RMS energy exceeds the speech activity threshold in dBFS."""
    return compute_pcm_rms_dbfs(pcm_bytes) >= threshold_dbfs


def pcm_48k_stereo_to_16k_mono_wav(
    pcm_bytes: bytes,
    input_sample_rate: int = 48000,
    input_channels: int = 2,
    target_sample_rate: int = 16000,
) -> Tuple[bytes, float, float]:
    """
    Convert raw Discord 48 kHz 16-bit stereo PCM (`s16le`) into a normalized 16 kHz 16-bit mono WAV
    in memory. Returns `(wav_bytes, duration_sec, rms_dbfs)`.
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return b"", 0.0, -96.0

    frame_bytes = 2 * max(1, input_channels)
    aligned_len = (len(pcm_bytes) // frame_bytes) * frame_bytes
    raw = np.frombuffer(pcm_bytes[:aligned_len], dtype=np.int16).astype(np.float32)
    if raw.size == 0:
        return b"", 0.0, -96.0

    if input_channels > 1:
        mono = raw.reshape(-1, input_channels).mean(axis=1)
    else:
        mono = raw

    duration_sec = float(mono.size) / float(max(1, input_sample_rate))
    rms_norm = float(np.sqrt(np.mean((mono / 32768.0) ** 2))) if mono.size else 0.0
    rms_dbfs = float(max(-96.0, min(0.0, 20.0 * math.log10(rms_norm)))) if rms_norm > 1e-5 else -96.0

    # Decimate / resample from 48 kHz to 16 kHz
    if input_sample_rate == target_sample_rate:
        resampled = mono
    elif input_sample_rate % target_sample_rate == 0:
        step = input_sample_rate // target_sample_rate
        # Simple 3-tap boxcar smoothing before decimation to prevent aliasing
        if mono.size >= step * 2:
            kernel = np.ones(step, dtype=np.float32) / float(step)
            smoothed = np.convolve(mono, kernel, mode="same")
            resampled = smoothed[::step]
        else:
            resampled = mono[::step]
    else:
        out_len = max(1, int(round(mono.size * target_sample_rate / float(input_sample_rate))))
        x_old = np.linspace(0.0, 1.0, num=mono.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
        resampled = np.interp(x_new, x_old, mono).astype(np.float32)

    # Peak-normalize quiet speech up to -3 dBFS (max 4x gain) so STT models hear soft microphones clearly
    peak = float(np.max(np.abs(resampled))) if resampled.size else 0.0
    if peak > 200.0:
        target_peak = 23000.0
        gain = min(4.0, target_peak / peak)
        if gain > 1.05:
            resampled = np.clip(resampled * gain, -32767.0, 32767.0)

    pcm_16k = np.clip(resampled, -32768.0, 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sample_rate)
        wf.writeframes(pcm_16k.tobytes())

    return buf.getvalue(), round(duration_sec, 3), round(rms_dbfs, 2)


def audio_bytes_to_discord_pcm_48k_stereo(audio_bytes: bytes) -> bytes:
    """
    Decode MP3/WAV/OGG/FLAC bytes in memory into Discord-ready 48 kHz 16-bit stereo PCM (`s16le`).
    Uses `miniaudio` first and falls back to stdlib `wave` + `numpy` resampling.
    """
    if not audio_bytes:
        return b""

    try:
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2,
            sample_rate=48000,
        )
        return bytes(decoded.samples)
    except Exception:
        pass

    # Fallback for raw RIFF WAV buffers
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            raw_frames = wf.readframes(wf.getnframes())

        if sampwidth != 2 or not raw_frames:
            return b""
        samples = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32)
        if n_channels >= 2:
            frames = samples.reshape(-1, n_channels)[:, :2]
            left, right = frames[:, 0], frames[:, 1]
        else:
            left = samples
            right = samples

        if framerate != 48000 and left.size > 0:
            out_len = max(1, int(round(left.size * 48000.0 / float(framerate))))
            x_old = np.linspace(0.0, 1.0, num=left.size, endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
            left = np.interp(x_new, x_old, left)
            right = np.interp(x_new, x_old, right)

        stereo = np.empty((left.size * 2,), dtype=np.int16)
        stereo[0::2] = np.clip(left, -32768.0, 32767.0).astype(np.int16)
        stereo[1::2] = np.clip(right, -32768.0, 32767.0).astype(np.int16)
        return stereo.tobytes()
    except Exception:
        return b""


def synthesize_tone_envelope_wav(
    spoken_text: str,
    sample_rate: int = 24000,
    base_freq_hz: float = 190.0,
) -> Tuple[bytes, float]:
    """
    Generate a clean, valid 16-bit mono WAV audio buffer with syllable-cadence harmonic envelopes
    proportional to the words in `spoken_text`. Used as an offline/zero-dependency fallback when
    external neural TTS services (`edge-tts` / local HTTP TTS) are unreachable.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9']+", spoken_text or "") if w]
    if not words:
        words = ["ok"]

    # ~0.18s per word, bounded between 0.45s and 4.5s
    duration_sec = min(4.5, max(0.45, len(words) * 0.18))
    total_samples = int(sample_rate * duration_sec)
    t = np.linspace(0.0, duration_sec, num=total_samples, endpoint=False, dtype=np.float32)

    # Syllable modulation envelope + warm harmonic triad
    syllable_rate = max(2.5, min(6.0, len(words) / duration_sec))
    envelope = 0.55 + 0.45 * np.sin(2.0 * np.pi * syllable_rate * t - np.pi / 2.0)
    fade_samples = min(int(sample_rate * 0.04), total_samples // 4)
    if fade_samples > 0:
        fade = np.linspace(0.0, 1.0, num=fade_samples, dtype=np.float32)
        envelope[:fade_samples] *= fade
        envelope[-fade_samples:] *= fade[::-1]

    wave_f = (
        0.60 * np.sin(2.0 * np.pi * base_freq_hz * t)
        + 0.25 * np.sin(2.0 * np.pi * (base_freq_hz * 1.5) * t)
        + 0.15 * np.sin(2.0 * np.pi * (base_freq_hz * 2.0) * t)
    )
    pcm = np.clip(wave_f * envelope * 11000.0, -32768.0, 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    return buf.getvalue(), round(duration_sec, 3)


def extract_wake_word_command(
    transcript: str,
    wake_words: Iterable[str] = DEFAULT_WAKE_WORDS,
    require_wake_word: bool = True,
) -> Tuple[bool, str]:
    """
    Check whether a transcribed utterance triggers the voice co-host and strip any leading wake phrase.
    Returns `(is_triggered, cleaned_query)`.
    """
    clean = (transcript or "").strip()
    if not clean:
        return False, ""

    # Strip leading punctuation or filler words ("um", "uh")
    clean = re.sub(r"^(?:um+|uh+|er+|ah+|so)[,\s]+", "", clean, flags=re.IGNORECASE).strip()
    low = clean.lower()

    sorted_wakes = sorted((w.strip().lower() for w in wake_words if w and w.strip()), key=len, reverse=True)
    for wake in sorted_wakes:
        # Match wake word at the start or after a short greeting ("hey", "yo", "ok", "okay")
        pat = rf"^(?:(?:hey|yo|ok|okay|hi|hello)\s+)?{re.escape(wake)}\b[\s,.:;!?-]*(.*)$"
        m = re.match(pat, clean, flags=re.IGNORECASE)
        if m:
            remainder = m.group(1).strip()
            return True, (remainder or clean)

        # Also catch wake word mid-sentence ("what's the weather hey chappie" or "chappie what time is it")
        m_mid = re.search(rf"\b{re.escape(wake)}\b[\s,.:;!?-]*(.*)$", clean, flags=re.IGNORECASE)
        if m_mid:
            before = clean[: m_mid.start()].strip(" ,.:;!?-")
            after = m_mid.group(1).strip(" ,.:;!?-")
            combined = f"{before} {after}".strip()
            return True, (combined or clean)

    if not require_wake_word:
        return True, clean

    return False, clean


def sanitize_text_for_speech(text: str, max_chars: int = 380) -> str:
    """
    Transform a Markdown-formatted bot reply into clean, natural-sounding spoken prose for TTS:
    - Converts `[Label](<https://...>)` into `Label`
    - Strips raw URLs, code blocks, markdown headers/tables, and bullet glyphs
    - Expands financial/score shorthands (`+2.45%` -> `up 2.45 percent`, `$SHOP.TO` -> `SHOP dot T O`)
    - Truncates cleanly at a sentence boundary within `max_chars` so voice replies stay conversational.
    """
    if not text or not text.strip():
        return ""

    s = text.strip()
    # Remove fenced code blocks completely
    s = re.sub(r"```[\s\S]*?```", " ", s)
    # Replace markdown links [Label](<url>) or [Label](url) with Label
    s = re.sub(r"\[([^\]]+)\]\(<?https?://[^\s)>]+>?\)", r"\1", s)
    # Remove bare URLs
    s = re.sub(r"<?https?://\S+>?", "", s)
    # Remove Discord user/role mention brackets if raw IDs remain
    s = re.sub(r"<@!?\d+>", "", s)

    # Take non-table, non-divider lines
    spoken_lines = []
    for line in s.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        if line_clean.startswith(("|", "===", "---", "```")):
            continue
        # Strip markdown headers and bullet prefixes
        line_clean = re.sub(r"^(?:#{1,6}|[-*•]|\d+\.)\s+", "", line_clean).strip()
        if line_clean:
            spoken_lines.append(line_clean)
            if sum(len(x) for x in spoken_lines) >= max_chars:
                break

    s = " ".join(spoken_lines)
    # Strip bold/italic/code markdown markers
    s = re.sub(r"[*_`~]+", "", s)
    # Strip common status emojis so TTS doesn't read emoji names out loud
    s = re.sub(
        r"[📡📈📉⚖️🏆📰🟢🔴⚪✈️🛫🛬🌤️🌧️❄️🎵🎶🎧🔥🌊🎲📊🔔📅🏥💰⚠️❌🔒🧹🔄🌍🇨🇦🇺🇸🇪🇺🌏]+",
        " ",
        s,
    )

    # Expand signed percentages: +2.45% -> up 2.45 percent, -1.12% -> down 1.12 percent
    s = re.sub(r"\+(\d+(?:\.\d+)?)%", r"up \1 percent", s)
    s = re.sub(r"-(\d+(?:\.\d+)?)%", r"down \1 percent", s)
    s = re.sub(r"(\d+(?:\.\d+)?)%", r"\1 percent", s)

    # Expand .TO ticker suffix for natural pronunciation
    s = re.sub(r"\$?([A-Z]{2,5})\.TO\b", r"\1 dot T O", s)
    s = re.sub(r"\$([A-Z]{2,5})\b", r"\1", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    if len(s) > max_chars:
        cut = s[:max_chars]
        # Prefer cutting at the last sentence-ending punctuation
        m_sent = list(re.finditer(r"[.!?](?:\s|$)", cut))
        if m_sent and m_sent[-1].end() >= max_chars // 3:
            s = cut[: m_sent[-1].end()].strip()
        else:
            s = cut.rsplit(" ", 1)[0].strip() + "."

    return s
