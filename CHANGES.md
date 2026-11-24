# Nisme AI v3 — nima o'zgardi

## 1. "[WinError 2] Не удается найти указанный файл" — tuzatildi
Sabab: `ffmpeg`/`ffprobe` PATH'da yo'q edi, kod esa ularni nomi bilan chaqirardi (whisper, pydub, dubbing.py).
- Yangi `ffmpeg_utils.py`: ffmpeg'ni `FFMPEG_PATH` → PATH → `imageio-ffmpeg` tartibida topadi. **PATH shart emas.**
- `ffprobe` umuman ishlatilmaydi. Whisper'ga audio yo'li emas, ffmpeg o'qigan massiv beriladi.
- `pydub` va `moviepy` olib tashlandi (pydub Python 3.13+/3.14'da `audioop` yo'qligi sababli ishlamaydi).

## 2. Tarjima xatolari
- Frontend "From" tilini umuman yubormasdi → Whisper tilni o'zi taxmin qilardi (o'zbekcha ko'pincha noto'g'ri aniqlanadi). Endi `source_language` yuboriladi va Whisper'ga + tarjimonga uzatiladi. "Auto-detect" ham bor.
- `zh` kodi Google Translate'da yo'q (`zh-CN` kerak) — Xitoy tiliga tarjima yiqilardi.
- Bitta segment xato bersa butun video yiqilardi → qayta urinish + zaxira (30% dan ko'p yiqilsa aniq xato).
- Uzun matn so'z o'rtasidan bo'linardi → gap chegarasidan.
- `OPENAI_API_KEY` bo'lsa GPT bilan kontekstli tarjima (Google xato bersa avtomatik Google'ga qaytadi). O'chirish: `TRANSLATION_ENGINE=google`.
- ElevenLabs `eleven_turbo_v2` faqat inglizcha edi → `eleven_turbo_v2_5` (ko'p tilli).
- "Swap" tugmasi indeks bo'yicha almashtirardi va hech narsa o'zgarmasdi → tuzatildi.

## 3. Sinxron dublyaj
- Tezlashtirish 4x gacha edi (tushunarsiz) → 1.45x; ortiqcha qismi keyingi segmentgacha bo'sh joyga to'kiladi.
- TTS boshi/oxiridagi jimlik kesiladi; segmentlar parallel yaratiladi (`TTS_CONCURRENCY`).

## 4. VoiceLab
- Yangi `tts_providers.py`: maqsad til `VOICELAB_LANGUAGES` (default `uz`) ichida bo'lsa → VoiceLab, aks holda ElevenLabs.
- `GET /api/voices?language=uz` — frontend ovozlar ro'yxatini tilga qarab yuklaydi.
- Job natijasida `tts_chars` va `tts_audio_ms` qaytadi (narx/marja hisoblash uchun).
- `python voicelab_smoke_test.py` — ulashdan oldin tekshirish.

## 5. Dizayn
- Hero va navbar uchun `photo_…23.jpg` (ko'k, 1920×1080) → `frontend/assets/bg-hero.jpg`.
- Qaytarish: `index.html` oxiridagi `<style>` ichidagi "Dark hero" blokini o'chirish kifoya.

## Xavfsizlik
Yuborgan zip'ingizda `backend/.env` bor edi va unda haqiqiy ko'rinishdagi ElevenLabs va OpenAI kalitlari bor. Ularni
**yangisiga almashtiring (rotate)**. Bu zip'da `.env` yo'q.

## 6. Tarjima 429 (Too Many Requests) — tuzatildi (v3.1)

**Sabab:** OpenAI krediti tugaganda (`credit_balance_exhausted`) kod Google Translate
bepul endpointiga o'tadi. 13 ta segmentni ketma-ket, deyarli kechiktirmasdan so'rash
Google tomonidan 429 bilan bloklanadi (limit: ~5 so'rov/sekund, amalda qattiqroq).

**Nima qilindi (`translate.py`):**
- Segmentlar orasida `GOOGLE_TRANSLATE_DELAY` (default 0.45s) kutish
- 429 da eksponensial backoff (3s → 8s → 18s → … → 60s), 5 marta qayta urinish
- Avval `translate_batch` (5 tadan), ishlamasa segmentma-segment
- Google yiqilsa **MyMemory** zaxira yo'li (deep_translator)
- Butun video yiqilish chegarasi 30% → 50% (qisman tarjima ham saqlanadi)
- Aniqroq xato xabari: nima qilish kerakligi yoziladi

**Siz nima qilishingiz mumkin:**
1. Eng yaxshisi: https://platform.openai.com/settings/organization/billing dan kredit to'ldiring
   → GPT tarjima sifatliroq va rate-limit yo'q
2. Yoki `.env` da: `TRANSLATION_ENGINE=google` va `GOOGLE_TRANSLATE_DELAY=1.0`
3. Bir necha daqiqa kutib qayta urinib ko'ring (IP blok vaqtincha)

## 7. v4.1 — UI + tarjima + VoiceLab

- Marquee: "BREAK BARRIERS" → **NISME GROUP · NISME AI**
- Oq fonli bo'limlar: `assets/bg-dots.png` (nuqtali grid) body background
- MyMemory: `uz` → `uz-UZ` (va boshqa tillar to'liq locale) — "No support for language" tuzatildi
- Google default delay 0.45 → 0.8
- VoiceLab 502 / server xatosida avtomatik **ElevenLabs** ga fallback
