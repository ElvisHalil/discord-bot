"""
Acoustic DSP & LLM Audio Intelligence Module
--------------------------------------------
Combines fast in-memory MP3 digital signal processing (miniaudio + numpy) with
gemma4:e4b's native mel-spectrogram audio encoder (gemma4a) in Ollama:

1. In-memory DSP (~20ms/track on CPU, 0 VRAM):
   - Decodes 30-second Deezer MP3 previews into 22,050 Hz mono float32 waveforms.
   - Extracts mathematical BPM (via STFT spectral flux onset envelope + autocorrelation),
     percussive onset density (hits/sec), spectral centroid harshness (Hz),
     high-frequency energy ratio (>= 2.5 kHz), and RMS energy.
   - Computes calibrated `speed_score` (1.0 - 10.0) and `intensity_score` (1.0 - 10.0)
     that distinguish warm/muffled loud drums (e.g. chill jazz-hop) from genuinely
     hard/aggressive tracks (e.g. dubstep, hard techno, thrash metal).

2. Parallel Candidate Shift Re-Ranker (~0.25s for 6-8 tracks):
   - Ranks candidate tracks for 'faster', 'slower', 'harder', and 'softer' spectrum
     shifts against the anchor track's real acoustic profile.

3. Native gemma4:e4b Audio Listening (~2.7s warm):
   - Sends raw MP3 bytes to gemma4:e4b's audio encoder (`gemma4a`) with `think=False`
     to identify instruments, vocal delivery, and emotional vibe/mood without requiring
     any external paid audio API keys.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import miniaudio
import numpy as np
import requests

from src.skills.music.models import Track
from src.skills.music.utils import _norm_track_token

logger = logging.getLogger(__name__)

# Session cache for analyzed tracks: { "norm_title|norm_artist": dict }
AUDIO_FEATURE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 1800.0


def clear_audio_feature_cache() -> None:
    """Clear the in-memory acoustic feature cache."""
    AUDIO_FEATURE_CACHE.clear()


def _cache_key(title: str, artist: str) -> str:
    return f"{_norm_track_token(title)}|{_norm_track_token(artist)}"


def decode_mp3_bytes_to_mono(mp3_bytes: bytes, sample_rate: int = 22050) -> np.ndarray:
    """Decode raw MP3 bytes in memory into a 1D float32 mono numpy waveform."""
    if not mp3_bytes:
        return np.array([], dtype=np.float32)
    decoded = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=sample_rate,
    )
    return np.frombuffer(decoded.samples, dtype=np.float32).copy()


def analyze_waveform(
    samples: np.ndarray,
    sample_rate: int = 22050,
    catalog_bpm: Optional[float] = None,
    catalog_gain: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute deterministic acoustic features from a mono float32 waveform:
      - bpm: float (estimated or catalog-verified BPM in [65, 185])
      - onset_rate: float (percussive/note onsets per second)
      - spectral_centroid_hz: float (brightness / harshness in Hz)
      - hf_energy_ratio: float (fraction of spectral power >= 2500 Hz)
      - rms: float (root-mean-square amplitude)
      - gain_db: float (catalog ReplayGain dB or RMS-estimated dB)
      - speed_score: float in [1.0, 10.0]
      - intensity_score: float in [1.0, 10.0]
      - ui_zone: str (mapped 3x3 spectrum zone, e.g. 'mid_mid', 'fast_hard')
    """
    if samples is None or len(samples) < sample_rate:
        fallback_bpm = float(catalog_bpm) if (catalog_bpm and catalog_bpm > 0) else 120.0
        fallback_gain = float(catalog_gain) if catalog_gain is not None else -10.0
        speed_s = float(np.clip((fallback_bpm - 70.0) / 115.0 * 9.0 + 1.0, 1.0, 10.0))
        intens_s = float(np.clip((fallback_gain + 20.0) / 15.0 * 9.0 + 1.0, 1.0, 10.0))
        return {
            "bpm": round(fallback_bpm, 1),
            "onset_rate": 3.5,
            "spectral_centroid_hz": 1800.0,
            "hf_energy_ratio": 0.03,
            "rms": 0.18,
            "gain_db": round(fallback_gain, 1),
            "speed_score": round(speed_s, 2),
            "intensity_score": round(intens_s, 2),
            "ui_zone": classify_scores_to_zone(speed_s, intens_s),
        }

    y = np.asarray(samples, dtype=np.float32)
    rms = float(np.sqrt(np.mean(y ** 2)))

    hop = 512
    n_fft = 1024
    n_frames = max(1, (len(y) - n_fft) // hop)
    window = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop][:n_frames] * window
    spec = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)

    # 1. Spectral Centroid (Brightness) & High-Frequency Harshness Ratio (>= 2.5 kHz)
    total_mag = float(np.sum(spec)) + 1e-9
    centroid_hz = float(np.sum(spec * freqs) / total_mag)

    spec_power = spec ** 2
    total_power = float(np.sum(spec_power)) + 1e-9
    hf_mask = freqs >= 2500.0
    hf_ratio = float(np.sum(spec_power[:, hf_mask]) / total_power)

    # 2. Spectral Flux Onset Envelope & Onset Density (Hits / sec)
    log_spec = np.log1p(10.0 * spec)
    diff = np.maximum(0.0, np.diff(log_spec, axis=0))
    onset_env = np.mean(diff, axis=1)

    duration_sec = max(1.0, len(y) / float(sample_rate))
    if len(onset_env) > 4:
        thr = float(np.mean(onset_env) + 0.75 * np.std(onset_env))
        peaks = np.where(
            (onset_env[1:-1] > onset_env[:-2])
            & (onset_env[1:-1] > onset_env[2:])
            & (onset_env[1:-1] > thr)
        )[0]
        onset_rate = float(len(peaks)) / duration_sec
    else:
        onset_rate = 3.5

    # 3. Harmonic Comb-Filter BPM Estimation (Kick/Snare Weighted at 256-sample hop resolution)
    est_bpm = 120.0
    hop_bpm = 256
    n_frames_bpm = max(1, (len(y) - n_fft) // hop_bpm)
    fps = float(sample_rate) / float(hop_bpm)
    if n_frames_bpm > int(fps * 3):
        frames_b = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop_bpm][:n_frames_bpm] * window
        spec_b = np.abs(np.fft.rfft(frames_b, axis=1))
        log_b = np.log1p(10.0 * spec_b)
        diff_b = np.maximum(0.0, np.diff(log_b, axis=0))

        # Weight low/kick-snare frequencies (<= 450 Hz) at 60% and mid frequencies (450-2500 Hz) at 40%
        low_mask = freqs <= 450.0
        mid_mask = (freqs > 450.0) & (freqs <= 2500.0)
        low_flux = np.mean(diff_b[:, low_mask], axis=1) if np.any(low_mask) else np.mean(diff_b, axis=1)
        mid_flux = np.mean(diff_b[:, mid_mask], axis=1) if np.any(mid_mask) else np.mean(diff_b, axis=1)
        beat_env = 0.60 * low_flux + 0.40 * mid_flux
        if float(np.std(beat_env)) < 1e-6:
            beat_env = np.mean(diff_b, axis=1)

        onset_zero = beat_env - np.mean(beat_env)
        ac = np.correlate(onset_zero, onset_zero, mode="full")[len(onset_zero) - 1 :]
        ac = ac / (float(ac[0]) + 1e-9)

        min_lag = max(1, int(round(fps * 60.0 / 182.0)))
        max_lag = min(len(ac) - 1, int(round(fps * 60.0 / 68.0)))
        if max_lag > min_lag:
            lags = np.arange(min_lag, max_lag + 1, dtype=np.int32)
            bpms = 60.0 * fps / lags.astype(np.float32)

            # Power-of-2 Harmonic Comb Filter (1 beat, 2 beats, 4 beats = 1 bar)
            comb = np.zeros(len(lags), dtype=np.float32)
            for idx_l, lag_val in enumerate(lags):
                s = float(ac[lag_val])
                if 2 * lag_val < len(ac):
                    s += 0.50 * float(ac[2 * lag_val])
                if 4 * lag_val < len(ac):
                    s += 0.25 * float(ac[4 * lag_val])
                comb[idx_l] = s

            prior_center = 132.0 if (onset_rate >= 4.7 or hf_ratio >= 0.065) else 118.0
            prior = np.exp(-0.5 * ((bpms - prior_center) / 36.0) ** 2)
            weighted_comb = comb * prior
            best_idx = int(np.argmax(weighted_comb))
            best_lag = int(lags[best_idx])

            # Parabolic sub-frame lag interpolation for 0.1 BPM precision
            refined_lag = float(best_lag)
            if 0 < best_idx < len(comb) - 1:
                y_prev = float(comb[best_idx - 1])
                y_0 = float(comb[best_idx])
                y_next = float(comb[best_idx + 1])
                denom = y_prev - 2.0 * y_0 + y_next
                if abs(denom) > 1e-6:
                    delta_lag = float(np.clip(0.5 * (y_prev - y_next) / denom, -0.45, 0.45))
                    refined_lag = float(best_lag) + delta_lag

            est_bpm = float(60.0 * fps / refined_lag)

            # Half-time -> Double-time check for fast DnB / Thrash (e.g. 87 BPM -> 174 BPM)
            if est_bpm < 92.0 and (onset_rate >= 4.85 or hf_ratio >= 0.07):
                half_lag = best_lag // 2
                if half_lag >= min_lag and ac[half_lag] >= 0.45 * ac[best_lag]:
                    est_bpm *= 2.0
            # Double-time -> Half-time check for quiet acoustic/piano ballads (e.g. 156 BPM -> 78 BPM)
            elif est_bpm > 144.0 and onset_rate < 3.6 and hf_ratio < 0.025:
                est_bpm *= 0.5

    final_bpm = float(catalog_bpm) if (catalog_bpm and catalog_bpm > 40.0) else est_bpm

    # 4. Gain / Loudness (dB)
    if catalog_gain is not None:
        gain_db = float(catalog_gain)
    else:
        # Estimate ReplayGain-equivalent dB from waveform RMS (calibrated to Deezer's -21 dB to -5 dB range)
        rms_db = 20.0 * float(np.log10(max(rms, 1e-4)))
        gain_db = float(np.clip(rms_db - 1.5, -22.0, -4.0))

    # 5. Calibrated Speed Score (1.0 - 10.0):
    # Combines BPM (75%) and Percussive Onset Density (25%) so 134+ BPM tracks cleanly enter the 'fast' zone (>= 6.4)
    bpm_norm = float(np.clip((final_bpm - 72.0) / (160.0 - 72.0), 0.0, 1.0))
    onset_norm = float(np.clip((onset_rate - 1.8) / (5.2 - 1.8), 0.0, 1.0))
    speed_score = 1.0 + 9.0 * (0.75 * bpm_norm + 0.25 * onset_norm)
    speed_score = float(np.clip(speed_score, 1.0, 10.0))

    # 6. Calibrated Intensity / Hardness Score (1.0 - 10.0):
    # Combines Mastered Gain/Loudness (35%), Spectral Centroid Brightness (30%), and High-Freq Harshness >=2.5kHz (35%)
    # This prevents loud warm/muffled drums (like Nujabes - Feather at -6.3 dB, hf=0.015) from scoring as 'hard'.
    loudness_norm = float(np.clip((gain_db - (-20.0)) / (-5.0 - (-20.0)), 0.0, 1.0))
    centroid_norm = float(np.clip((centroid_hz - 600.0) / (3600.0 - 600.0), 0.0, 1.0))
    hf_norm = float(np.clip(hf_ratio / 0.13, 0.0, 1.0))
    intensity_score = 1.0 + 9.0 * (0.35 * loudness_norm + 0.30 * centroid_norm + 0.35 * hf_norm)
    intensity_score = float(np.clip(intensity_score, 1.0, 10.0))

    return {
        "bpm": round(final_bpm, 1),
        "onset_rate": round(onset_rate, 2),
        "spectral_centroid_hz": round(centroid_hz, 0),
        "hf_energy_ratio": round(hf_ratio, 4),
        "rms": round(rms, 4),
        "gain_db": round(gain_db, 1),
        "speed_score": round(speed_score, 2),
        "intensity_score": round(intensity_score, 2),
        "ui_zone": classify_scores_to_zone(speed_score, intensity_score),
    }


def classify_scores_to_zone(speed_score: float, intensity_score: float) -> str:
    """Map numerical speed_score (1-10) and intensity_score (1-10) onto the 3x3 spectrum ui_zone."""
    if speed_score < 4.2:
        t_label = "slow"
    elif speed_score < 6.4:
        t_label = "mid"
    else:
        t_label = "fast"

    if intensity_score < 4.2:
        i_label = "soft"
    elif intensity_score < 6.8:
        i_label = "mid"
    else:
        i_label = "hard"

    return f"{t_label}_{i_label}"


def analyze_mp3_bytes(
    mp3_bytes: bytes,
    catalog_bpm: Optional[float] = None,
    catalog_gain: Optional[float] = None,
) -> Dict[str, Any]:
    """Decode raw 30s MP3 preview bytes and extract acoustic features."""
    samples = decode_mp3_bytes_to_mono(mp3_bytes, sample_rate=22050)
    return analyze_waveform(
        samples,
        sample_rate=22050,
        catalog_bpm=catalog_bpm,
        catalog_gain=catalog_gain,
    )


def get_track_acoustic_profile(
    title: str,
    artist: str = "",
    deezer_id: Optional[int] = None,
    preview_url: Optional[str] = None,
    deezer_agg: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a track on Deezer, fetch its 30s MP3 preview and single-track gain/bpm metadata,
    and run in-memory DSP analysis. Cached per session.
    """
    clean_title = (title or "").strip()
    clean_artist = (artist or "").strip()
    if not clean_title:
        return None

    c_key = _cache_key(clean_title, clean_artist)
    now = time.monotonic()
    if c_key in AUDIO_FEATURE_CACHE:
        cached = AUDIO_FEATURE_CACHE[c_key]
        if now - cached.get("_ts", 0) < _CACHE_TTL_SECONDS:
            return dict(cached)

    if deezer_agg is None:
        from src.skills.music.aggregators.deezer import DeezerAggregator
        deezer_agg = DeezerAggregator()

    detail = None
    if hasattr(deezer_agg, "resolve_track_details"):
        detail = deezer_agg.resolve_track_details(clean_title, clean_artist, deezer_id=deezer_id)

    mp3_url = (detail or {}).get("preview") or preview_url
    cat_bpm = (detail or {}).get("bpm")
    cat_gain = (detail or {}).get("gain")

    mp3_bytes = b""
    if mp3_url:
        try:
            r = requests.get(mp3_url, timeout=5)
            if r.status_code == 200 and r.content:
                mp3_bytes = r.content
        except Exception as e:
            logger.debug("MP3 preview fetch failed for %r: %s", mp3_url, e)

    if not mp3_bytes and detail is None:
        return None

    profile = analyze_mp3_bytes(mp3_bytes, catalog_bpm=cat_bpm, catalog_gain=cat_gain)
    profile["title"] = (detail or {}).get("title") or clean_title
    profile["artist"] = (detail or {}).get("artist") or clean_artist
    profile["album"] = (detail or {}).get("album")
    profile["deezer_id"] = (detail or {}).get("id") or deezer_id
    profile["preview_url"] = mp3_url
    profile["_mp3_bytes"] = mp3_bytes
    profile["_ts"] = now

    AUDIO_FEATURE_CACHE[c_key] = profile
    return dict(profile)


def rank_candidates_by_acoustic_shift(
    candidates: List[Track],
    d_tempo: int,
    d_intensity: int,
    anchor_profile: Optional[Dict[str, Any]] = None,
    max_analyze: int = 14,
) -> List[Track]:
    """
    Analyze up to `max_analyze` candidate tracks in parallel via the worker pool and rank them
    by how well they satisfy the requested (d_tempo, d_intensity) shift relative to `anchor_profile`.
    """
    if not candidates:
        return candidates

    from src.utils.workers import submit_to_worker

    # Prioritize candidates that already carry a Deezer preview_url, interleaved round-robin by genre
    # so every fetched tag/playlist gets fair representation in the DSP batch.
    raw_with_preview = [t for t in candidates if getattr(t, "preview_url", None)]
    without_preview = [t for t in candidates if not getattr(t, "preview_url", None)]

    genre_buckets: Dict[str, List[Track]] = {}
    for trk in raw_with_preview:
        g_key = (trk.genre or "default").strip().lower()
        genre_buckets.setdefault(g_key, []).append(trk)

    with_preview: List[Track] = []
    bucket_lists = list(genre_buckets.values())
    max_len = max((len(b) for b in bucket_lists), default=0)
    for row_idx in range(max_len):
        for b in bucket_lists:
            if row_idx < len(b):
                with_preview.append(b[row_idx])

    ordered_pool = with_preview + without_preview

    subset = ordered_pool[:max_analyze]
    remainder = ordered_pool[max_analyze:]

    futures = [
        (
            idx,
            trk,
            submit_to_worker(
                get_track_acoustic_profile,
                trk.title,
                trk.artist,
                trk.deezer_id,
                trk.preview_url,
            ),
        )
        for idx, trk in enumerate(subset)
    ]

    anchor_bpm = (anchor_profile or {}).get("bpm", 120.0)
    anchor_speed = (anchor_profile or {}).get("speed_score", 5.5)
    anchor_intens = (anchor_profile or {}).get("intensity_score", 5.5)
    anchor_genre = ((anchor_profile or {}).get("genre") or "").strip().lower()

    scored_subset: List[Tuple[float, int, Track]] = []
    for idx, trk, fut in futures:
        prof = None
        try:
            prof = fut.result(timeout=6)
        except Exception as e:
            logger.debug("Acoustic profile worker failed for %r: %s", trk.title, e)

        if prof:
            trk.bpm = prof.get("bpm")
            trk.gain = prof.get("gain_db")
            trk.speed_score = prof.get("speed_score")
            trk.intensity_score = prof.get("intensity_score")
            if not trk.preview_url:
                trk.preview_url = prof.get("preview_url")
            if not trk.deezer_id:
                trk.deezer_id = prof.get("deezer_id")

            c_bpm = prof.get("bpm", 120.0)
            c_speed = prof["speed_score"]
            c_intens = prof["intensity_score"]
            shift_fit = 0.0
            direction_satisfied = True

            # Tempo direction scoring: progressive ~15 BPM per-notch step so chained 'faster'/'slower'
            # requests climb smoothly (e.g. 120 -> 135 -> 150 -> 165 -> 178 BPM) instead of jumping
            # straight to the 175 BPM ceiling on the first turn.
            if d_tempo > 0:
                delta_s = c_speed - anchor_speed
                delta_bpm = c_bpm - anchor_bpm
                if delta_bpm >= 2.0:
                    target_step = 15.0 if anchor_bpm < 160.0 else 8.0
                    notch_bonus = max(0.0, 10.0 - abs(delta_bpm - target_step) * 0.45)
                    shift_fit += 10.0 + notch_bonus + delta_s * 0.6
                elif anchor_bpm >= 168.0 and c_bpm >= 168.0:
                    # Already at the 168-182 BPM DnB/Hardcore ceiling: strictly order by highest BPM + onset speed
                    shift_fit += 8.0 + delta_bpm * 2.5 + delta_s * 1.0
                else:
                    direction_satisfied = False
                    # Heavy penalty if 'faster' candidate actually has lower/equal BPM than anchor
                    shift_fit -= 20.0 + abs(delta_bpm) * 0.8
            elif d_tempo < 0:
                delta_s = anchor_speed - c_speed
                delta_bpm = anchor_bpm - c_bpm
                if delta_bpm >= 2.0:
                    target_step = 15.0 if anchor_bpm > 90.0 else 8.0
                    notch_bonus = max(0.0, 10.0 - abs(delta_bpm - target_step) * 0.45)
                    shift_fit += 10.0 + notch_bonus + delta_s * 0.6
                elif anchor_bpm <= 76.0 and c_bpm <= 76.0 and delta_bpm >= 0.0:
                    shift_fit += 8.0 + delta_bpm * 2.0 + delta_s * 1.5
                else:
                    direction_satisfied = False
                    # Heavy penalty if 'slower' candidate actually has higher/equal BPM than anchor
                    shift_fit -= 20.0 + abs(delta_bpm) * 0.8
            else:
                # When tempo was NOT asked to change, mildly prefer staying within ±2.5 speed points
                shift_fit -= max(0.0, abs(c_speed - anchor_speed) - 2.0) * 0.6

            # Intensity direction scoring
            if d_intensity > 0:
                delta_i = c_intens - anchor_intens
                if delta_i <= 0:
                    direction_satisfied = False
                shift_fit += (delta_i * 3.5) if delta_i > 0 else (delta_i * 5.0 - 10.0)
            elif d_intensity < 0:
                delta_i = anchor_intens - c_intens
                if delta_i <= 0:
                    direction_satisfied = False
                shift_fit += (delta_i * 3.5) if delta_i > 0 else (delta_i * 5.0 - 10.0)
            else:
                # When intensity was NOT asked to change, mildly prefer staying within ±2.5 intensity points
                shift_fit -= max(0.0, abs(c_intens - anchor_intens) - 2.0) * 0.6

            # Genre continuity bonus: if the candidate satisfies the requested shift direction AND stays in
            # the same genre as the anchor, give a mild +1.5 bonus so we only jump genres once faster/harder
            # tracks in the current genre are exhausted.
            if direction_satisfied and anchor_genre and (trk.genre or "").strip().lower() == anchor_genre:
                shift_fit += 1.5

            scored_subset.append((shift_fit, -idx, trk))
        else:
            # Unresolved tracks sort after all acoustically verified candidates
            scored_subset.append((-35.0, -idx, trk))

    scored_subset.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored_subset] + remainder


def listen_with_gemma_audio(
    mp3_bytes: Optional[bytes] = None,
    preview_url: Optional[str] = None,
    song: str = "",
    artist: str = "",
    model: str = "gemma4:e4b",
    ollama_url: str = "http://localhost:11434/api/chat",
) -> Optional[Dict[str, str]]:
    """
    Use gemma4:e4b's native mel-spectrogram audio encoder (`gemma4a`) in Ollama (`think=False`)
    to listen to the 30-second MP3 preview and describe its instruments, vocals, and mood/vibe.
    """
    audio_bytes = mp3_bytes
    if not audio_bytes and preview_url:
        try:
            r = requests.get(preview_url, timeout=5)
            if r.status_code == 200:
                audio_bytes = r.content
        except Exception as e:
            logger.debug("Gemma audio preview fetch failed for %r: %s", preview_url, e)
            return None

    if not audio_bytes:
        return None

    b64_audio = base64.b64encode(audio_bytes).decode("ascii")
    prompt = (
        "Listen carefully to this 30-second music audio clip. "
        "Reply in 3 concise bullet lines:\n"
        "Instruments: <key instruments, drums, synths, or production style you hear>\n"
        "Vocals: <vocal style/delivery or Instrumental>\n"
        "Vibe: <emotional mood and atmosphere in 4-8 words>"
    )

    try:
        resp = requests.post(
            ollama_url,
            json={
                "model": model,
                "stream": False,
                "think": False,
                "options": {
                    "num_predict": 90,
                    "temperature": 0.2,
                },
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [b64_audio],
                    }
                ],
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        content = ((resp.json() or {}).get("message") or {}).get("content", "").strip()
        if not content:
            return None

        instruments = ""
        vocals = ""
        vibe = ""
        for line in content.splitlines():
            clean_line = re.sub(r"^[\s\*\-\•]+", "", line).strip()
            if clean_line.lower().startswith("instruments:"):
                instruments = clean_line.split(":", 1)[1].strip().strip("*")
            elif clean_line.lower().startswith("vocals:"):
                vocals = clean_line.split(":", 1)[1].strip().strip("*")
            elif clean_line.lower().startswith("vibe:") or clean_line.lower().startswith("mood:"):
                vibe = clean_line.split(":", 1)[1].strip().strip("*")

        return {
            "raw_summary": content,
            "instruments": instruments or content.splitlines()[0].strip(),
            "vocals": vocals or "Detected in mix",
            "vibe": vibe or content.splitlines()[-1].strip(),
        }
    except Exception as e:
        logger.debug("Gemma audio inference failed for %r: %s", song, e)
        return None
