"""
ffmpeg_utils.py — ffmpeg'ni PATH'ga BOG'LIQ BO'LMAGAN holda topish va ishlatish.

Nega kerak:
    Windows'da "[WinError 2] Не удается найти указанный файл" xatosi — bu
    subprocess "ffmpeg" yoki "ffprobe" nomli dasturni topa olmaganini bildiradi
    (ular PATH'da yo'q). Avval whisper, pydub va dubbing.py shu nomlarni
    to'g'ridan-to'g'ri chaqirardi.

Yechim:
    1) FFMPEG_PATH (.env) bo'lsa — o'sha ishlatiladi
    2) PATH'dagi ffmpeg
    3) imageio-ffmpeg paketi ichidagi ffmpeg (pip bilan o'rnatiladi, PATH shart emas)

    ffprobe umuman ishlatilmaydi: davomiylik `ffmpeg -i` chiqishidan olinadi.
    Whisper'ga audio yo'li emas, ffmpeg'imiz o'qigan massiv beriladi.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache

import numpy as np

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows'da konsol oynasi chiqmasin


@lru_cache(maxsize=1)
def get_ffmpeg() -> str:
    """ffmpeg bajariladigan faylining to'liq yo'lini qaytaradi."""
    env_path = os.getenv("FFMPEG_PATH", "").strip().strip('"')
    if env_path and os.path.isfile(env_path):
        return env_path

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg  # moviepy/imageio bilan birga keladi

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    raise FileNotFoundError(
        "ffmpeg topilmadi. Yechim: `pip install imageio-ffmpeg` "
        "yoki ffmpeg'ni o'rnatib PATH'ga qo'shing, yoki .env'da FFMPEG_PATH=C:\\...\\ffmpeg.exe deb yozing."
    )


def ffmpeg_available() -> str | None:
    try:
        return get_ffmpeg()
    except FileNotFoundError:
        return None


def run_ffmpeg(args: list[str], what: str = "ffmpeg") -> subprocess.CompletedProcess:
    """ffmpeg'ni ishga tushiradi; xato bo'lsa tushunarli RuntimeError beradi."""
    cmd = [get_ffmpeg(), "-hide_banner", "-nostdin", *args]
    r = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW
    )
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-8:])
        raise RuntimeError(f"{what} xatosi (kod {r.returncode}):\n{tail}")
    return r


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def media_duration(path: str) -> float:
    """Media davomiyligi (soniya). ffprobe SHART EMAS."""
    cmd = [get_ffmpeg(), "-hide_banner", "-nostdin", "-i", path]
    r = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW
    )
    m = _DUR_RE.search(r.stderr or "")
    if not m:
        raise RuntimeError(f"Davomiylikni aniqlab bo'lmadi: {path}")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + s


def extract_audio_wav(video_path: str, out_path: str, sample_rate: int = 16000) -> str:
    """Videodan mono WAV ajratadi (Whisper uchun 16 kHz)."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video topilmadi: {video_path}")
    try:
        run_ffmpeg(
            ["-y", "-i", video_path, "-vn", "-ac", "1", "-ar", str(sample_rate),
             "-c:a", "pcm_s16le", out_path],
            what="Audio ajratish",
        )
    except RuntimeError as e:
        low = str(e).lower()
        if "does not contain any stream" in low or "output file is empty" in low or "matches no streams" in low:
            raise ValueError("Videoda audio yo'q!") from e
        raise
    return out_path


def load_audio_np(path: str, sample_rate: int = 16000) -> np.ndarray:
    """
    Whisper uchun audioni float32 massiv sifatida o'qiydi (ffmpeg'ni o'zimiz chaqiramiz,
    shuning uchun whisper ichki 'ffmpeg' chaqiruvi va PATH muammosi aylanib o'tiladi).
    """
    cmd = [get_ffmpeg(), "-hide_banner", "-nostdin", "-threads", "0", "-i", path,
           "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("Audioni o'qib bo'lmadi: " + r.stderr.decode(errors="replace")[-400:])
    return np.frombuffer(r.stdout, np.int16).flatten().astype(np.float32) / 32768.0
