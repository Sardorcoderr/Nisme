"""
audio.py — Videodan audio ajratib olish moduli.

Endi MoviePy kerak emas: to'g'ridan-to'g'ri ffmpeg ishlatiladi (ffmpeg_utils orqali),
shuning uchun Windows'da PATH / MoviePy versiyasi muammolari yo'q.
"""

from ffmpeg_utils import extract_audio_wav


def extract_audio(video_path: str, output_path: str = "temp_audio.wav") -> str:
    return extract_audio_wav(video_path, output_path, sample_rate=16000)
