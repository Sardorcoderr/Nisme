"""
translate.py — tarjima moduli.

Tuzatilgan xatolar:
  * "zh" kodi Google Translate'da yo'q (zh-CN kerak) — endi to'g'ri xaritalanadi.
  * Manba til (source_language) endi haqiqatan uzatiladi ('auto' bo'lsa avtomatik).
  * Bitta segment xato bersa butun video yiqilmaydi: qayta urinish + zaxira.
  * Uzun matn endi so'z o'rtasidan emas, gap chegarasidan bo'linadi.
  * Ixtiyoriy: OPENAI_API_KEY bo'lsa — GPT bilan kontekstli tarjima (Google'dan ancha tabiiy,
    ayniqsa o'zbek tilida). Xato bo'lsa avtomatik Google / MyMemory ga qaytadi.
    Majburan o'chirish: TRANSLATION_ENGINE=google
  * Google 429 (Too Many Requests) — sekin so'rovlar, eksponensial backoff,
    translate_batch, MyMemory zaxira yo'li.
"""

from __future__ import annotations

import html

import json
import os
import re
import time

import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator

SUPPORTED_LANGUAGES = {
    "uz": "O'zbek",
    "ru": "Rus",
    "en": "Ingliz",
    "tr": "Turk",
    "kk": "Qozoq",
    "de": "Nemis",
    "fr": "Fransuz",
    "es": "Ispan",
    "zh": "Xitoy",
    "ar": "Arab",
    "ja": "Yapon",
    "ko": "Koreys",
}

_LANG_NAMES_EN = {
    "uz": "Uzbek (Latin script, natural spoken style)", "ru": "Russian", "en": "English",
    "tr": "Turkish", "kk": "Kazakh", "de": "German", "fr": "French", "es": "Spanish",
    "zh": "Simplified Chinese", "ar": "Arabic", "ja": "Japanese", "ko": "Korean",
}

_MAX_CHUNK = 4500  # Google limiti 5000 — zaxira bilan
_MYMEMORY_MAX = 480  # MyMemory ~500 belgi

# Google bepul endpoint: ~5 so'rov/sekund. Amalda IP bloklash tezroq bo'ladi.
# Segmentlar orasida kutish (sekund).
_GOOGLE_DELAY = float(os.getenv("GOOGLE_TRANSLATE_DELAY", "0.35"))
# 429 da qayta urinishlar
_MAX_RETRIES = int(os.getenv("TRANSLATE_RETRIES", "5"))

# Oddiy in-memory cache (bir xil matnni qayta tarjima qilmaslik)
_TRANSLATE_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_MAX = 500


def _cache_get(source: str, target: str, text: str) -> str | None:
    key = (source or "auto", target, text.strip())
    return _TRANSLATE_CACHE.get(key)


def _cache_set(source: str, target: str, text: str, result: str) -> None:
    if len(_TRANSLATE_CACHE) >= _CACHE_MAX:
        # eng eski 100 tasini o'chirish
        for k in list(_TRANSLATE_CACHE.keys())[:100]:
            _TRANSLATE_CACHE.pop(k, None)
    key = (source or "auto", target, text.strip())
    _TRANSLATE_CACHE[key] = result


def _gcode(code: str) -> str:
    """Google Translate kodlari: 'zh' emas 'zh-CN'."""
    if not code or code == "auto":
        return "auto"
    return {"zh": "zh-CN"}.get(code, code)


def _mm_code(code: str) -> str:
    """MyMemory kodlari (to'liq locale kerak: uz → uz-UZ)."""
    if not code or code == "auto":
        return "en-GB"
    mapping = {
        "uz": "uz-UZ",
        "ru": "ru-RU",
        "en": "en-GB",
        "tr": "tr-TR",
        "kk": "kk-KZ",
        "de": "de-DE",
        "fr": "fr-FR",
        "es": "es-ES",
        "zh": "zh-CN",
        "ar": "ar-SA",
        "ja": "ja-JP",
        "ko": "ko-KR",
    }
    return mapping.get(code, code)


def _is_rate_limit(err: BaseException) -> bool:
    s = str(err).lower()
    return any(
        x in s
        for x in (
            "too many requests",
            "429",
            "rate limit",
            "ratelimit",
            "quota",
            "too many",
        )
    )


def _backoff_sleep(attempt: int, rate_limited: bool) -> None:
    """Eksponensial kutish. 429 bo'lsa uzoqroq."""
    if rate_limited:
        # 3, 8, 18, 35, 60 ...
        delay = min(60.0, 3.0 * (2 ** attempt) + attempt)
    else:
        delay = min(15.0, 0.8 * (attempt + 1))
    time.sleep(delay)


def _translate_once_google(translator: GoogleTranslator, text: str, retries: int = _MAX_RETRIES, source: str = "auto", target: str = "en") -> str:
    cached = _cache_get(source, target, text)
    if cached is not None:
        return cached
    last: Exception | None = None
    for attempt in range(retries):
        try:
            out = translator.translate(text)
            out = out or ""
            _cache_set(source, target, text, out)
            return out
        except Exception as e:
            last = e
            _backoff_sleep(attempt, _is_rate_limit(e))
    raise RuntimeError(f"Google Translate javob bermadi: {last}")


def _translate_once_mymemory(source: str, target: str, text: str, retries: int = 3) -> str:
    """MyMemory — Google bloklanganda zaxira (500 belgigacha)."""
    if not text.strip():
        return ""
    last: Exception | None = None
    parts = _split_sentences(text, limit=_MYMEMORY_MAX) if len(text) > _MYMEMORY_MAX else [text]
    results: list[str] = []
    tr = MyMemoryTranslator(source=_mm_code(source), target=_mm_code(target))
    for part in parts:
        ok = False
        for attempt in range(retries):
            try:
                out = tr.translate(part)
                results.append(out or part)
                time.sleep(0.25)
                ok = True
                break
            except Exception as e:
                last = e
                _backoff_sleep(attempt, _is_rate_limit(e))
        if not ok:
            raise RuntimeError(f"MyMemory javob bermadi: {last}")
    return " ".join(results)


def _split_sentences(text: str, limit: int = _MAX_CHUNK) -> list[str]:
    parts = re.split(r"(?<=[.!?…。！？])\s+", text.strip())
    chunks, cur = [], ""
    for p in parts:
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:cut])
            p = p[cut:].lstrip()
        if len(cur) + len(p) + 1 > limit and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def translate_text(text: str, target_language: str, source_language: str = "auto") -> str:
    """Matnni tarjima qiladi (uzun matn gap chegarasidan bo'linadi)."""
    if not text.strip():
        return ""
    try:
        tr = GoogleTranslator(source=_gcode(source_language), target=_gcode(target_language))
        if len(text) <= _MAX_CHUNK:
            return _translate_once_google(tr, text, source=source_language, target=target_language)
        out = []
        for c in _split_sentences(text):
            out.append(_translate_once_google(tr, c, source=source_language, target=target_language))
            time.sleep(_GOOGLE_DELAY)
        return " ".join(out)
    except Exception as e:
        print(f"⚠️  Google translate_text xato, MyMemory: {e}")
        return _translate_once_mymemory(source_language, target_language, text)


# ── GPT (ixtiyoriy) ─────────────────────────────────────────────────────────

def _llm_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip()) and \
        os.getenv("TRANSLATION_ENGINE", "auto").strip().lower() != "google"


def _llm_translate_batch(lines: list[str], target: str, source: str) -> list[str]:
    """Bir nechta qatorni bitta so'rovda tarjima qiladi."""
    model = os.getenv("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini")
    tgt = _LANG_NAMES_EN.get(target, target)
    src = "the source language" if source == "auto" else _LANG_NAMES_EN.get(source, source)
    system = (
        "You translate video subtitles for AI dubbing. "
        f"Translate each numbered line from {src} into {tgt}. "
        "Rules: keep the meaning faithful; use natural spoken language; keep each line about as "
        "short as the original so it can be spoken in the same time; do not merge or split lines; "
        "do not add explanations; keep names and numbers. "
        'Return ONLY JSON: {"translations": ["...", ...]} with exactly the same number of items.'
    )
    user = json.dumps({"lines": lines}, ensure_ascii=False)
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '').strip()}"},
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=90,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    out = json.loads(content)["translations"]
    if not isinstance(out, list) or len(out) != len(lines):
        raise ValueError(f"GPT {len(lines)} qator o'rniga {len(out) if isinstance(out, list) else '?'} qaytardi")
    return [str(x).strip() for x in out]


def _llm_translate_segments(texts: list[str], target: str, source: str, batch: int = 30) -> list[str]:
    result: list[str] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        try:
            result.extend(_llm_translate_batch(chunk, target, source))
        except Exception:
            if len(chunk) == 1:
                raise
            mid = len(chunk) // 2
            result.extend(_llm_translate_segments(chunk[:mid], target, source, batch=batch))
            result.extend(_llm_translate_segments(chunk[mid:], target, source, batch=batch))
    return result


# ── Google + MyMemory (segmentlar) ──────────────────────────────────────────

def _google_translate_segments(texts: list[str], source: str, target: str) -> tuple[list[str], int, str | None]:
    """
    Segmentlarni Google orqali tarjima qiladi.
    Qaytaradi: (translated_list, failed_count, first_error_str)
    """
    tr = GoogleTranslator(source=_gcode(source), target=_gcode(target))
    translated: list[str] = []
    failed = 0
    first_err: str | None = None

    non_empty_idx = [i for i, t in enumerate(texts) if t]
    # Tezlashtirish: batch 8-10, sleep faqat bir marta (multiplier yo'q)
    BATCH_SIZE = int(os.getenv("GOOGLE_TRANSLATE_BATCH", "8"))
    if len(non_empty_idx) >= 2:
        try:
            batch_in = [texts[i] for i in non_empty_idx]
            batch_out: list[str] = []
            for j in range(0, len(batch_in), BATCH_SIZE):
                sub = batch_in[j : j + BATCH_SIZE]
                for attempt in range(_MAX_RETRIES):
                    try:
                        part = tr.translate_batch(sub)
                        if not isinstance(part, list) or len(part) != len(sub):
                            raise RuntimeError("translate_batch uzunligi mos kelmadi")
                        batch_out.extend([p or "" for p in part])
                        # Faqat bitta delay — multiplier olib tashlandi (tezlik uchun)
                        time.sleep(_GOOGLE_DELAY)
                        break
                    except Exception as e:
                        if attempt + 1 >= _MAX_RETRIES:
                            raise
                        _backoff_sleep(attempt, _is_rate_limit(e))
            out = [""] * len(texts)
            for i, t in zip(non_empty_idx, batch_out):
                out[i] = t
            return out, 0, None
        except Exception as e:
            print(f"⚠️  Google translate_batch ishlamadi, segmentma-segment: {e}")
            first_err = str(e)

    for t in texts:
        if not t:
            translated.append("")
            continue
        try:
            translated.append(_translate_once_google(tr, t, source=source, target=target))
            time.sleep(_GOOGLE_DELAY)
        except Exception as e:
            failed += 1
            if first_err is None:
                first_err = str(e)
            translated.append("")
    return translated, failed, first_err


def _mymemory_fill(texts: list[str], partial: list[str], source: str, target: str) -> tuple[list[str], int, str | None]:
    """Bo'sh qolgan segmentlarni MyMemory bilan to'ldiradi."""
    failed = 0
    first_err: str | None = None
    out = list(partial)
    for i, t in enumerate(texts):
        if not t or (out[i] and out[i].strip()):
            continue
        try:
            out[i] = _translate_once_mymemory(source, target, t)
        except Exception as e:
            failed += 1
            if first_err is None:
                first_err = str(e)
            out[i] = t
    return out, failed, first_err


# ── Segmentlar ──────────────────────────────────────────────────────────────


def _clean_translation(text: str) -> str:
    """HTML entity (&#39; → '), ortiqcha bo'shliqlar, g'alati belgilarni tozalash."""
    if not text:
        return ""
    s = html.unescape(str(text))
    # Ba'zi tarjimonlar to'g'ri apostrof o'rniga &#39; yoki &apos; qoldiradi
    s = s.replace("&#39;", "'").replace("&apos;", "'").replace("&#x27;", "'")
    s = s.replace("&quot;", '"').replace("&#34;", '"')
    # O'zbek lotin: g' o' q' kabi birikmalar saqlansin, lekin qo'sh apostrof soddalashtirilsin
    while "''" in s:
        s = s.replace("''", "'")
    s = " ".join(s.split())
    return s.strip()


def translate_segments(segments: list, target_language: str, source_language: str = "auto") -> list:
    """
    Har bir Whisper segmentiga "translated_text" qo'shadi (start/end saqlanadi).
    Tartib: GPT (kalit bo'lsa) → Google (batch + sekin) → MyMemory → asl matn.
    Segmentlarning 50% dan ko'pi tarjima bo'lmasa — aniq xato bilan to'xtaydi.
    """
    texts = [(s.get("text") or "").strip() for s in segments]
    translated: list[str] | None = None
    engine = "google"

    if _llm_enabled() and any(texts):
        try:
            idx = [i for i, t in enumerate(texts) if t]
            part = _llm_translate_segments([texts[i] for i in idx], target_language, source_language)
            translated = [""] * len(texts)
            for i, t in zip(idx, part):
                translated[i] = t
            engine = "openai"
        except Exception as e:
            print(f"⚠️  GPT tarjima ishlamadi, Google'ga o'tilmoqda: {e}")
            translated = None

    failed = 0
    first_err: str | None = None
    mm_err: str | None = None

    if translated is None:
        translated, failed, first_err = _google_translate_segments(
            texts, source_language, target_language
        )
        empty_slots = sum(1 for i, t in enumerate(texts) if t and not (translated[i] or "").strip())
        if empty_slots:
            print(f"⚠️  Google: {empty_slots} segment bo'sh, MyMemory zaxira yo'li...")
            translated, mm_failed, mm_err = _mymemory_fill(
                texts, translated, source_language, target_language
            )
            failed = mm_failed
            if mm_failed == 0:
                engine = "mymemory" if empty_slots == sum(1 for t in texts if t) else "google+mymemory"
            else:
                engine = "partial"

        if first_err or mm_err:
            print(
                f"⚠️  Google tarjima: {failed}/{len(segments)} segment xato. "
                f"Birinchi xato: {first_err or mm_err}"
                + (f" | zaxira yo'l: {mm_err}" if first_err and mm_err else "")
            )

    n_nonempty = sum(1 for t in texts if t)
    if n_nonempty and failed > max(1, int(n_nonempty * 0.5)):
        detail = first_err or mm_err or "noma'lum"
        raise RuntimeError(
            f"Tarjima xizmati javob bermadi ({failed}/{len(segments)} segment). "
            f"Sabab: {detail}. "
            "Biroz kutib qayta urinib ko'ring, yoki OPENAI_API_KEY kreditini to'ldiring "
            "(platform.openai.com → Billing), yoki TRANSLATION_ENGINE=google qilib "
            "GOOGLE_TRANSLATE_DELAY=1.0 qo'ying."
        )

    out = []
    for seg, t in zip(segments, translated):
        final = _clean_translation((t or "").strip() or (seg.get("text") or "").strip())
        out.append({**seg, "translated_text": final, "translation_engine": engine})
    return out
