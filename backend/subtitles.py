"""
subtitles.py — VideoLingo (github.com/Huanshere/VideoLingo, Apache-2.0) arxitekturasidagi
_5_split_sub.py / _6_gen_sub.py / _7_sub_into_vid.py bosqichlaridan ilhomlangan:
tarjima qilingan segmentlardan .srt subtitr fayli yaratish va xohlasa videoga
"burn-in" (qattiq, o'chmaydigan) subtitr sifatida kuydirish.
"""

import os
import shutil

from ffmpeg_utils import run_ffmpeg


def _srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list, srt_path: str) -> str:
    """Tarjima qilingan segmentlardan standart .srt fayl yaratadi."""
    lines = []
    counter = 1
    for seg in segments:
        text = (seg.get("translated_text") or "").strip()
        if not text:
            continue
        lines.append(str(counter))
        lines.append(f"{_srt_timestamp(seg['start'])} --> {_srt_timestamp(seg['end'])}")
        lines.append(text)
        lines.append("")
        counter += 1

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> str:
    """
    ffmpeg 'subtitles' filtri orqali subtitrni videoga qattiq kuydiradi.

    Eslatma: bu ffmpeg 'libass' bilan build qilingan bo'lishini talab qiladi
    (ko'pchilik standart ffmpeg buildlarida mavjud). Agar mavjud bo'lmasa,
    RuntimeError chiqadi — bu holda main.py subtitrsiz natijaga qaytadi.
    """
    # srt'ni oddiy (ASCII) nomli faylga ko'chiramiz — nomida bo'shliq/maxsus belgi
    # bo'lsa ffmpeg filtri buzilmasligi uchun. Windows yo'li (C:\) esa quyida escape qilinadi.
    work_dir = os.path.dirname(os.path.abspath(output_path))
    local_srt = os.path.join(work_dir, "burn_subs.srt")
    if os.path.abspath(srt_path) != os.path.abspath(local_srt):
        shutil.copyfile(srt_path, local_srt)

    escaped = local_srt.replace("\\", "/").replace(":", "\\:")
    style = "FontSize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3"

    run_ffmpeg(
        [
            "-y",
            "-i", video_path,
            "-vf", f"subtitles='{escaped}':force_style='{style}'",
            "-c:a", "copy",
            output_path,
        ],
        what="Subtitr kuydirish",
    )
    return output_path
