"""
downloader.py — YouTube / TikTok / Instagram / umumiy video URL yuklab olish (yt-dlp).

YouTube Shorts, Instagram Reels va ba'zi formatlar tez-tez o'zgaradi —
bir necha format strategiyasi + client fallback ketma-ket sinab ko'riladi.
"""

from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlparse

_URL_RE = re.compile(r"^https?://", re.I)

ALLOWED_NETLOCS_HINT = (
    "youtube.com",
    "youtu.be",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "facebook.com",
    "www.facebook.com",
    "fb.watch",
    "vimeo.com",
    "www.vimeo.com",
    "twitter.com",
    "x.com",
    "www.x.com",
)


def is_valid_video_url(url: str) -> bool:
    url = (url or "").strip()
    if not url or not _URL_RE.match(url):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _is_instagram(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "instagram.com" in host


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(x in host for x in ("youtube.com", "youtu.be"))


def _base_opts(outtmpl: str, url: str = "") -> dict:
    opts: dict = {
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,uz;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }

    # YouTube: bir nechta player client (Shorts uchun muhim)
    if _is_youtube(url):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "ios", "web", "mweb"],
                "player_skip": ["webpage", "configs"],
            }
        }

    # Instagram: ba'zan login talab qiladi
    if _is_instagram(url):
        opts["extractor_args"] = {
            "instagram": {
                # ba'zi versiyalarda foydali
            }
        }

    # Cookie (bot tekshiruvi / Instagram login uchun)
    cookies_browser = (os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if cookies_browser:
        browser = cookies_browser.lower()
        if browser in ("chrom", "chrome"):
            browser = "chrome"
        opts["cookiesfrombrowser"] = (browser,)

    cookies_file = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file

    return opts


def _resolve_downloaded_file(info, out_dir: str, ydl) -> str:
    """Yuklab olingan fayl yo'lini topadi."""
    if info is None:
        raise RuntimeError("Video ma'lumotini olish muvaffaqiyatsiz.")

    # requested_downloads yoki filepath
    requested = info.get("requested_downloads") or []
    if requested:
        for rd in requested:
            fp = rd.get("filepath") or rd.get("filename")
            if fp and os.path.isfile(fp) and os.path.getsize(fp) > 0:
                return fp

    filepath = info.get("filepath") or info.get("_filename")
    if filepath and os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
        return filepath

    # outtmpl dan taxmin
    base = os.path.join(out_dir, "download")
    candidates = [
        base + ".mp4",
        base + ".mkv",
        base + ".webm",
        base + ".mov",
        os.path.join(out_dir, "download.mp4"),
        os.path.join(out_dir, "download.mkv"),
        os.path.join(out_dir, "download.webm"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            return path

    files = [
        os.path.join(out_dir, f)
        for f in os.listdir(out_dir)
        if f.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3"))
    ]
    if not files:
        raise RuntimeError("Yuklab olish tugadi, lekin video fayl topilmadi.")
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def download_video(url: str, out_dir: str, max_height: int = 720) -> str:
    """
    URL dan videoni yuklab, out_dir ichiga saqlaydi.
    Qaytaradi: yuklab olingan fayl yo'li.

    Format strategiyalari (ketma-ket, eng qat'iydan yumshoqgacha):
      1) 720p mp4 + audio
      2) 1080p
      3) bestvideo+bestaudio
      4) progressive best
      5) umuman best / worst
    """
    if not is_valid_video_url(url):
        raise ValueError("Noto'g'ri yoki qo'llab-quvvatlanmaydigan URL.")

    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp o'rnatilmagan. Yechim: pip install -U yt-dlp"
        ) from e

    os.makedirs(out_dir, exist_ok=True)
    outtmpl = os.path.join(out_dir, "download.%(ext)s")

    # Ketma-ket uriniladigan formatlar
    format_candidates = [
        # Prefer mp4 progressive / merged under height limit
        f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}][ext=mp4]/bv*[height<={max_height}]+ba/b[height<={max_height}]",
        f"bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
        "bv*+ba/b",
        "best[ext=mp4]/best",
        "bestvideo+bestaudio/best",
        "best",
        "worst",
    ]

    # Instagram ba'zan oddiyroq format bilan yaxshi ishlaydi
    if _is_instagram(url):
        format_candidates = [
            "best[ext=mp4]/best",
            "bestvideo+bestaudio/best",
            "best",
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
            "worst",
        ] + format_candidates

    last_err: Exception | None = None
    tried_without_cookies = False

    for fmt in format_candidates:
        ydl_opts = _base_opts(outtmpl, url)
        ydl_opts["format"] = fmt

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("Video ma'lumotini olish muvaffaqiyatsiz.")
            return _resolve_downloaded_file(info, out_dir, ydl)
        except Exception as e:
            last_err = e
            msg = str(e).lower()

            # Format mavjud emas — keyingi strategiya
            if "requested format is not available" in msg or "format is not available" in msg:
                print(f"⚠️  Format '{fmt[:50]}...' mavjud emas, keyingisi sinab ko'rilmoqda...")
                continue

            # Cookie bilan muammo — cookiesiz qayta urinish (bir marta)
            if not tried_without_cookies and ("cookiesfrombrowser" in ydl_opts or "cookiefile" in ydl_opts):
                tried_without_cookies = True
                fallback = {k: v for k, v in ydl_opts.items() if k not in ("cookiesfrombrowser", "cookiefile")}
                try:
                    with yt_dlp.YoutubeDL(fallback) as ydl:
                        info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise RuntimeError("Video ma'lumotini olish muvaffaqiyatsiz.")
                    return _resolve_downloaded_file(info, out_dir, ydl)
                except Exception as e2:
                    last_err = e2
                    msg2 = str(e2).lower()
                    if "format is not available" in msg2 or "format" in msg2:
                        continue
                    # boshqa xato — davom etamiz

            # Bot / login xabarlari
            if "sign in" in msg or "bot" in msg or "confirm you're not a bot" in msg or "login required" in msg:
                raise RuntimeError(
                    "YouTube/Instagram bot yoki login tekshiruvi. "
                    ".env ga YTDLP_COOKIES_FROM_BROWSER=chrome "
                    "yoki YTDLP_COOKIES_FILE=cookies.txt qo'shing "
                    "(brauzerdan cookies export qiling). "
                    f"Asl xato: {e}"
                ) from e

            # Instagram maxsus
            if _is_instagram(url) and ("rate-limit" in msg or "429" in msg or "login" in msg):
                raise RuntimeError(
                    "Instagram rate-limit yoki login talab qilmoqda. "
                    "YTDLP_COOKIES_FROM_BROWSER=chrome yoki cookies.txt qo'shing. "
                    f"Asl xato: {e}"
                ) from e

            # Boshqa xatolar: format bilan bog'liq bo'lmasa ham keyingi formatni sinab ko'ramiz
            # (Shorts / Instagram uchun foydali)
            print(f"⚠️  Yuklash urinishi muvaffaqiyatsiz ({fmt[:35]}...): {e}")
            continue

    # Hammasi muvaffaqiyatsiz
    platform = "Instagram" if _is_instagram(url) else ("YouTube" if _is_youtube(url) else "Video")
    hint = (
        f"{platform} videoni yuklab bo'lmadi.\n"
        "1) pip install -U yt-dlp\n"
        "2) .env: YTDLP_COOKIES_FROM_BROWSER=chrome  (yoki edge/firefox)\n"
        "3) Yoki YTDLP_COOKIES_FILE=C:\\path\\to\\cookies.txt\n"
        "4) Eng oxirgi chora: videoni brauzerdan yuklab, fayl sifatida yuklang."
    )
    if last_err:
        raise RuntimeError(f"{hint}\nAsl xato: {last_err}") from last_err
    raise RuntimeError(hint)
