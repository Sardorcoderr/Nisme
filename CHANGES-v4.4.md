# Nisme AI v4.4 — YouTube/Instagram + tarjima tezligi

## 1. YouTube + Instagram linklari mustahkamlandi (`downloader.py`)
- YouTube Shorts uchun ko'proq player client: android / ios / web / mweb
- Format fallback zanjiri kengaytirildi (720p → 1080p → best → worst)
- Instagram Reels/Post alohida format strategiyasi
- Cookie (browser yoki file) bilan ishlash yaxshilandi
- Aniqroq xato xabarlari (bot tekshiruvi, login required, rate-limit)
- Tavsiya: `pip install -U yt-dlp` va kerak bo'lsa `.env` da:
  ```
  YTDLP_COOKIES_FROM_BROWSER=chrome
  # yoki
  YTDLP_COOKIES_FILE=C:\path\to\cookies.txt
  ```

## 2. Tarjima tezligi oshirildi (`translate.py`)
- Google `translate_batch` batch o'lchami: 5 → **8** (sozlanadi: `GOOGLE_TRANSLATE_BATCH`)
- Batch orasidagi kutish multiplier olib tashlandi → sezilarli tezlashish
- Default delay: 0.8 → **0.35** sekund
- Oddiy in-memory cache (bir xil matn qayta tarjima qilinmaydi)
- OpenAI batch: 25 → 30 segment
- `.env` misoli yangilandi

## Tezlik bo'yicha maslahat
- Eng tez: `OPENAI_API_KEY` qo'ying → GPT batch tarjima (sifat ham yaxshi)
- Faqat Google: `GOOGLE_TRANSLATE_DELAY=0.35` va `GOOGLE_TRANSLATE_BATCH=8`
- 429 ko'p chiqsa: delay ni 0.6–1.0 ga oshiring

## O'rnatish
```bash
cd backend
pip install -U yt-dlp
# .env ni yangilang (GOOGLE_TRANSLATE_DELAY=0.35)
python main.py
```
