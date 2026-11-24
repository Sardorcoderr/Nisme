"""
tts_providers.py — Matndan nutqqa: VoiceLab (o'zbek) + ElevenLabs (boshqa tillar).

Marshrutlash (TTS_PROVIDER=auto, default):
    * Maqsad til VOICELAB_LANGUAGES ro'yxatida (default: "uz") va VOICELAB_API_KEY bor  → VoiceLab
    * Aks holda                                                                       → ElevenLabs
    TTS_PROVIDER=voicelab | elevenlabs — hamma tilni bitta provayderga majburlash.

Ovoz kalitlari (frontend ↔ backend):
    "vl:<voice_id>"   — VoiceLab ovozi
    "el:rachel"       — ElevenLabs ovozi  (eski "rachel" ham ishlaydi)

VoiceLab API (docs.voicelab.uz):
    POST /v1/tts  {text, language, voice_id, speed}   Authorization: Bearer <key>
    Idempotency-Key sarlavhasi har bir YANGI generatsiya uchun noyob bo'lishi kerak.
    200 → WAV (24 kHz mono); 202 → status_url bo'yicha kutish.
    Javob sarlavhalari: X-VoiceLab-Characters-Used, X-VoiceLab-Audio-Duration-Ms
    1 belgi = 1 kredit.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Optional

import requests


class TTSError(RuntimeError):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# ElevenLabs
# ═════════════════════════════════════════════════════════════════════════════

ELEVEN_VOICES = {
    "rachel": {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "female", "description": "Calm, clear American English"},
    "adam":   {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam",   "gender": "male",   "description": "Deep, narrative American English"},
    "bella":  {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella",  "gender": "female", "description": "Soft, conversational"},
    "antoni": {"id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "gender": "male",   "description": "Warm, friendly"},
    "elli":   {"id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli",   "gender": "female", "description": "Young, energetic"},
    "josh":   {"id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh",   "gender": "male",   "description": "Deep, young American"},
}
ELEVEN_DEFAULT = "rachel"

_el_client = None
_el_lock = threading.Lock()


def elevenlabs_configured() -> bool:
    k = os.getenv("ELEVENLABS_API_KEY", "").strip()
    return bool(k) and not k.startswith("your_")


def _eleven_client():
    global _el_client
    with _el_lock:
        if _el_client is None:
            from elevenlabs import ElevenLabs  # type: ignore
            _el_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY", "").strip())
        return _el_client


def _eleven_tts(text: str, out_path: str, voice_name: str, stats: Optional[dict]) -> str:
    if not elevenlabs_configured():
        raise TTSError("ElevenLabs API kaliti sozlanmagan (backend/.env → ELEVENLABS_API_KEY).")
    voice = ELEVEN_VOICES.get(voice_name, ELEVEN_VOICES[ELEVEN_DEFAULT])
    # eleven_turbo_v2 — faqat ingliz tili; v2.5 ko'p tilli. Kerak bo'lsa .env'da o'zgartiring.
    model_id = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    safe = text[:2000]
    try:
        stream = _eleven_client().text_to_speech.convert(voice_id=voice["id"], model_id=model_id, text=safe)
        with open(out_path, "wb") as f:
            for chunk in stream:
                f.write(chunk)
    except Exception as e:
        msg = str(e).lower()
        if "quota" in msg or "limit" in msg or "402" in msg:
            raise TTSError("ElevenLabs limiti tugagan yoki kredit yetarli emas.") from e
        if "unauthorized" in msg or "401" in msg or "invalid" in msg:
            raise TTSError("ElevenLabs API kaliti noto'g'ri.") from e
        raise TTSError(f"ElevenLabs xatosi: {e}") from e
    if stats is not None:
        stats["tts_chars"] = stats.get("tts_chars", 0) + len(safe)
    return out_path


# ═════════════════════════════════════════════════════════════════════════════
# VoiceLab
# ═════════════════════════════════════════════════════════════════════════════

def _vl_base() -> str:
    return os.getenv("VOICELAB_API_BASE", "https://api.voicelab.uz").rstrip("/")


def voicelab_configured() -> bool:
    return bool(os.getenv("VOICELAB_API_KEY", "").strip())


def _vl_headers(extra: Optional[dict] = None) -> dict:
    h = {"Authorization": f"Bearer {os.getenv('VOICELAB_API_KEY', '').strip()}"}
    if extra:
        h.update(extra)
    return h


def _vl_langs_enabled() -> set[str]:
    raw = os.getenv("VOICELAB_LANGUAGES", "uz")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 600


def _cached(key: str, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def _as_list(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "voices", "items", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def voicelab_voices(language: Optional[str] = None) -> dict:
    """
    VoiceLab ovozlari: {"vl:<id>": {...}}. Javob shakli turli bo'lishi mumkin — erkin o'qiladi.
    Ro'yxat olinmasa, .env'dagi VOICELAB_VOICE_<LANG> (masalan VOICELAB_VOICE_UZ) ishlatiladi.
    """
    if not voicelab_configured():
        return {}

    def _fetch():
        try:
            r = requests.get(f"{_vl_base()}/v1/voices", headers=_vl_headers(), timeout=20)
            r.raise_for_status()
            return _as_list(r.json())
        except Exception as e:
            print(f"⚠️  VoiceLab /v1/voices olinmadi: {e}")
            return []

    voices: dict = {}
    for v in _cached("vl_voices", _fetch):
        if not isinstance(v, dict):
            continue
        vid = v.get("voice_id") or v.get("id")
        if not vid:
            continue
        labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
        langs = v.get("languages") or v.get("language") or labels.get("language") or []
        if isinstance(langs, str):
            langs = [langs]
        langs = [str(x).lower()[:2] for x in langs]
        if language and langs and language not in langs:
            continue
        voices[f"vl:{vid}"] = {
            "name": v.get("name") or str(vid),
            "gender": (v.get("gender") or labels.get("gender") or "").lower(),
            "description": v.get("description") or labels.get("description") or "",
            "provider": "voicelab",
        }

    if not voices and language:
        env_id = os.getenv(f"VOICELAB_VOICE_{language.upper()}", "").strip()
        if env_id:
            voices[f"vl:{env_id}"] = {"name": f"VoiceLab ({language})", "gender": "", "description": "", "provider": "voicelab"}
    return voices


def _vl_error(r: requests.Response) -> TTSError:
    try:
        body = r.json()
        detail = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("detail") or detail
    except Exception:
        detail = r.text[:200]
    code = r.status_code
    if code == 401:
        return TTSError("VoiceLab API kaliti noto'g'ri yoki o'chirilgan (VOICELAB_API_KEY).")
    if code == 402:
        return TTSError("VoiceLab kreditlari tugagan. Tarifni yangilang yoki keyingi oyni kuting.")
    if code == 403:
        return TTSError("VoiceLab API kaliti ruxsati yetarli emas (tts:write kerak).")
    if code in (400, 404, 422):
        return TTSError(f"VoiceLab so'rovi qabul qilinmadi ({code}): {detail}")
    return TTSError(f"VoiceLab xatosi ({code}): {detail}")


def _voicelab_tts(text: str, out_path: str, voice_id: str, language: str,
                  speed: float, stats: Optional[dict]) -> str:
    if not voicelab_configured():
        raise TTSError("VoiceLab API kaliti sozlanmagan (backend/.env → VOICELAB_API_KEY).")

    payload = {"text": text[:5000], "language": language, "voice_id": voice_id}
    if abs(speed - 1.0) > 1e-3:
        payload["speed"] = max(0.5, min(2.0, speed))

    idem = uuid.uuid4().hex  # BIR generatsiya uchun bitta kalit — qayta urinishda ham o'sha
    last_err: Optional[Exception] = None

    for attempt in range(4):
        try:
            r = requests.post(
                f"{_vl_base()}/v1/tts",
                headers=_vl_headers({"Idempotency-Key": idem}),
                json=payload,
                timeout=120,
            )
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
            continue

        if r.status_code in (429, 500, 502, 503, 504):
            last_err = _vl_error(r)
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 1.5 * (attempt + 1)
            time.sleep(min(wait, 20))
            continue

        if r.status_code == 202:
            r = _vl_poll(r)

        if r.status_code != 200:
            raise _vl_error(r)

        audio = r.content
        if not audio:
            raise TTSError("VoiceLab bo'sh audio qaytardi.")
        with open(out_path, "wb") as f:
            f.write(audio)

        if stats is not None:
            chars = r.headers.get("X-VoiceLab-Characters-Used")
            ms = r.headers.get("X-VoiceLab-Audio-Duration-Ms")
            with _cache_lock:
                stats["tts_chars"] = stats.get("tts_chars", 0) + (int(chars) if chars and chars.isdigit() else len(text))
                if ms and ms.isdigit():
                    stats["tts_audio_ms"] = stats.get("tts_audio_ms", 0) + int(ms)
        return out_path

    raise TTSError(f"VoiceLab bilan bog'lanib bo'lmadi: {last_err}")


def _vl_poll(r202: requests.Response, timeout_s: int = 180) -> requests.Response:
    """202 Accepted: status_url bo'yicha tayyor bo'lguncha kutadi, so'ng audioni yuklaydi."""
    try:
        body = r202.json()
    except Exception:
        raise TTSError("VoiceLab 202 javobini o'qib bo'lmadi.")
    status_url = body.get("status_url") or r202.headers.get("Location") or ""
    if status_url.startswith("/"):
        status_url = _vl_base() + status_url
    if not status_url:
        raise TTSError("VoiceLab status_url bermadi.")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(1.0)
        s = requests.get(status_url, headers=_vl_headers(), timeout=30)
        if s.status_code != 200:
            raise _vl_error(s)
        data = s.json()
        st = str(data.get("status", "")).lower()
        if st in ("completed", "succeeded", "done"):
            url = data.get("audio_url")
            if not url:
                raise TTSError("VoiceLab audio_url bermadi.")
            if url.startswith("/"):
                url = _vl_base() + url
            a = requests.get(url, timeout=60)  # imzolangan URL — Authorization kerak emas
            if a.status_code == 200 and "X-VoiceLab-Characters-Used" not in a.headers and data.get("characters_used") is not None:
                a.headers["X-VoiceLab-Characters-Used"] = str(data["characters_used"])
            return a
        if st in ("failed", "error", "cancelled", "canceled"):
            raise TTSError(f"VoiceLab generatsiyasi muvaffaqiyatsiz: {data.get('error') or st}")
    raise TTSError("VoiceLab generatsiyasi juda uzoq davom etdi (timeout).")


# ═════════════════════════════════════════════════════════════════════════════
# Marshrutlash
# ═════════════════════════════════════════════════════════════════════════════

def provider_for_language(language: str) -> str:
    forced = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    if forced in ("voicelab", "elevenlabs"):
        return forced
    if voicelab_configured() and language in _vl_langs_enabled():
        return "voicelab"
    return "elevenlabs"


def list_voices(language: str) -> dict:
    """Frontend uchun: shu tilga mos provayderning ovozlari."""
    if provider_for_language(language) == "voicelab":
        v = voicelab_voices(language)
        if v:
            return v
    return {
        f"el:{k}": {"name": x["name"], "gender": x["gender"], "description": x["description"], "provider": "elevenlabs"}
        for k, x in ELEVEN_VOICES.items()
    }


def resolve_voice(voice_key: str, language: str) -> tuple[str, str]:
    """(provider, voice_id_or_name). Noto'g'ri provayder ovozi kelsa — standartiga almashtiriladi."""
    provider = provider_for_language(language)
    key = (voice_key or "").strip()

    if provider == "voicelab":
        if key.startswith("vl:") and len(key) > 3:
            return "voicelab", key[3:]
        voices = voicelab_voices(language)
        if voices:
            return "voicelab", next(iter(voices))[3:]
        # ro'yxat olinmadi va .env'da ham yo'q — ElevenLabs'ga tushamiz
        if elevenlabs_configured():
            provider = "elevenlabs"
        else:
            raise TTSError(
                "VoiceLab ovozi tanlanmadi: /v1/voices bo'sh va .env'da VOICELAB_VOICE_UZ=<voice_id> yo'q."
            )

    name = key[3:] if key.startswith("el:") else key
    if name not in ELEVEN_VOICES:
        name = ELEVEN_DEFAULT
    return "elevenlabs", name


def synthesize(text: str, out_path: str, voice_key: str, language: str,
               stats: Optional[dict] = None, speed: float = 1.0) -> str:
    """Bitta matn bo'lagini ovozga aylantiradi. Qaytaradi: out_path (format provayderga bog'liq).
    VoiceLab 5xx / tarmoq xatosida avtomatik ElevenLabs ga o'tadi (kalit bo'lsa).
    """
    provider, voice = resolve_voice(voice_key, language)
    if provider == "voicelab":
        if stats is not None:
            stats["tts_provider"] = "voicelab"
        try:
            return _voicelab_tts(text, out_path, voice, language, speed, stats)
        except TTSError as e:
            if not elevenlabs_configured():
                raise
            print(f"⚠️  VoiceLab ishlamadi, ElevenLabs ga o'tilmoqda: {e}")
            if stats is not None:
                stats["tts_provider"] = "elevenlabs"
                stats["tts_fallback"] = "voicelab->elevenlabs"
            # Frontend "Jasur — Noir" kabi VoiceLab ovozini tanlagan bo'lishi mumkin —
            # ElevenLabs uchun default Rachel ishlatamiz.
            return _eleven_tts(text, out_path, "Rachel", stats)
    if stats is not None:
        stats.setdefault("tts_provider", "elevenlabs")
    return _eleven_tts(text, out_path, voice, stats)
