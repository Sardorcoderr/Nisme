"""
dubbing.py — VideoLingo (github.com/Huanshere/VideoLingo, Apache-2.0) g'oyasidan
ilhomlangan segment-asosli dublyaj moduli.

G'oya (o'zgarmagan):
    1. Whisper'ning har bir segmenti ALOHIDA tarjima qilinadi
    2. Har bir segment uchun alohida TTS yaratiladi
    3. Har bir TTS bo'lagi original segment davomiyligiga moslanadi
    4. Hammasi o'z start vaqtiga joylashtirilib, video uzunligiga teng audio yig'iladi

Bu versiyada tuzatilganlar:
    - pydub va ffprobe KERAK EMAS (Windows'dagi WinError 2 va Python 3.13+/3.14'dagi
      `audioop` muammosi shundan). Hammasi ffmpeg + numpy bilan.
    - Tezlashtirish endi 4x emas, ~1.45x bilan cheklangan (4x nutq tushunarsiz bo'ladi);
      ortiqcha qismi keyingi segmentgacha bo'lgan bo'sh joyga "to'kiladi".
    - Sekinlashtirish ham cheklangan (>=0.9x); qisqa bo'lak o'rniga jimlik qoldiriladi.
    - TTS boshi/oxiridagi jimlik kesiladi (sinxron aniqroq bo'ladi).
    - TTS parallel (TTS_CONCURRENCY, default 3) yaratiladi — video tezroq tayyor bo'ladi.
"""

from __future__ import annotations

import os
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from ffmpeg_utils import media_duration, run_ffmpeg

SAMPLE_RATE = 24000          # ichki ishchi chastota (VoiceLab ham 24 kHz beradi)
MAX_SPEEDUP = 1.45           # qulay tezlashtirish chegarasi
HARD_MAX_SPEEDUP = 1.9       # faqat boshqa iloj bo'lmaganda (keyingi segment yaqin)
MIN_SLOWDOWN = 0.9           # bundan sekin qilinmaydi (tabiiy eshitilmaydi)
SILENCE_THRESHOLD = 0.012    # trim uchun amplituda chegarasi (0..1)


def ffprobe_duration(path: str) -> float:
    """Media davomiyligi (soniya). Nomi eski, lekin ffprobe ishlatmaydi."""
    return media_duration(path)


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise RuntimeError("Kutilgan 16-bit WAV emas")
        frames = w.readframes(w.getnframes())
        ch = w.getnchannels()
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data


def _write_wav(path: str, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def _trim_silence(x: np.ndarray, keep_ms: int = 25) -> np.ndarray:
    """Bo'lak boshi va oxiridagi jimlikni kesadi (kichik zaxira qoldirib)."""
    if x.size == 0:
        return x
    loud = np.flatnonzero(np.abs(x) > SILENCE_THRESHOLD)
    if loud.size == 0:
        return x
    keep = int(SAMPLE_RATE * keep_ms / 1000)
    start = max(0, int(loud[0]) - keep)
    end = min(x.size, int(loud[-1]) + keep)
    return x[start:end]


def _decode(src: str, dst: str, tempo: float = 1.0) -> str:
    """Istalgan formatdagi TTS faylini mono 24 kHz WAV'ga o'giradi (+ ixtiyoriy atempo)."""
    args = ["-y", "-i", src]
    if abs(tempo - 1.0) > 1e-3:
        tempo = max(0.5, min(2.0, tempo))
        args += ["-filter:a", f"atempo={tempo:.5f}"]
    args += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", dst]
    run_ffmpeg(args, what="TTS audioni qayta ishlash")
    return dst


def _choose_tempo(natural_dur: float, window: float, max_len: float) -> float:
    """
    natural_dur — TTS tabiiy tezlikdagi davomiyligi
    window      — segmentning original davomiyligi (start→end)
    max_len     — start'dan keyingi segmentgacha bo'lgan maksimal joy (window'dan katta bo'lishi mumkin)
    Qaytaradi: tempo (>1 tezroq, <1 sekinroq)
    """
    if natural_dur <= 0:
        return 1.0
    needed = natural_dur / window

    if needed <= 1.0:
        return max(needed, MIN_SLOWDOWN)
    if needed <= MAX_SPEEDUP:
        return needed
    if natural_dur / MAX_SPEEDUP <= max_len:
        return MAX_SPEEDUP
    return min(natural_dur / max_len, HARD_MAX_SPEEDUP)


def build_synced_dub(
    segments: list,
    total_duration: float,
    tts_fn: Callable[[str, str], str],
    job_dir: str,
    output_path: str,
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """
    Har bir segment uchun TTS yaratib, video vaqt chizig'iga sinxronlab,
    yagona dublyaj audio trek (WAV) yig'adi.

    segments: [{"start": float, "end": float, "translated_text": str}, ...]
    tts_fn:   (text, output_path) -> output_path  (har qanday audio format bo'lishi mumkin)
    """
    if max_workers is None:
        try:
            max_workers = max(1, int(os.getenv("TTS_CONCURRENCY", "3")))
        except ValueError:
            max_workers = 3

    items = [
        (i, seg) for i, seg in enumerate(segments)
        if (seg.get("translated_text") or "").strip()
    ]

    # 1) TTS — parallel
    raw_paths: dict[int, str] = {}

    def _do_tts(item):
        i, seg = item
        raw = os.path.join(job_dir, f"seg_{i:04d}_raw.tts")
        tts_fn(seg["translated_text"].strip(), raw)
        return i, raw

    if items:
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for i, raw in pool.map(_do_tts, items):
                raw_paths[i] = raw
                done += 1
                if on_progress:
                    on_progress(done, len(items))

    # 2) Moslashtirish + joylashtirish
    master = np.zeros(int((total_duration + 0.5) * SAMPLE_RATE), dtype=np.float32)

    for pos, (i, seg) in enumerate(items):
        start = float(seg["start"])
        window = max(0.3, float(seg["end"]) - start)
        next_start = float(items[pos + 1][1]["start"]) if pos + 1 < len(items) else total_duration
        max_len = max(window, next_start - start - 0.08)

        base_wav = os.path.join(job_dir, f"seg_{i:04d}_base.wav")
        _decode(raw_paths[i], base_wav, tempo=1.0)
        clip = _trim_silence(_read_wav(base_wav))
        natural = clip.size / SAMPLE_RATE
        if natural <= 0.05:
            continue

        tempo = _choose_tempo(natural, window, max_len)
        if abs(tempo - 1.0) > 0.02:
            trimmed = os.path.join(job_dir, f"seg_{i:04d}_trim.wav")
            fit = os.path.join(job_dir, f"seg_{i:04d}_fit.wav")
            _write_wav(trimmed, clip)
            _decode(trimmed, fit, tempo=tempo)
            clip = _read_wav(fit)

        limit = int(max_len * SAMPLE_RATE)
        if clip.size > limit:  # keyingi segmentni bosib ketmaslik uchun
            clip = clip[:limit].copy()
            fade = min(int(0.03 * SAMPLE_RATE), clip.size)
            if fade > 0:
                clip[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

        f = min(int(0.008 * SAMPLE_RATE), clip.size // 2)  # click bo'lmasligi uchun
        if f > 0:
            clip = clip.copy()
            clip[:f] *= np.linspace(0.0, 1.0, f, dtype=np.float32)
            clip[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)

        s = int(start * SAMPLE_RATE)
        e = min(master.size, s + clip.size)
        if s < master.size and e > s:
            master[s:e] += clip[: e - s]

    master = np.clip(master, -1.0, 1.0)[: int(total_duration * SAMPLE_RATE)]
    _write_wav(output_path, master)
    return output_path
