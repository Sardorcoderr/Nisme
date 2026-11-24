# Nisme AI — Full-Stack Video Translation

Video → Whisper transkripsiya → tarjima → ElevenLabs sinxron dublyaj → (ixtiyoriy) subtitr.

**Frontend** (`frontend/index.html`) + **FastAPI backend** birga ishlaydi.

## Imkoniyatlar

- Segment-asosli **sinxron dublyaj** (VideoLingo g‘oyasi)
- Fayl yuklash **yoki** YouTube / TikTok / boshqa URL (`yt-dlp`)
- Whisper model: `tiny` / `base` / `small`
- 6 ta ElevenLabs ovoz
- **WebSocket** progress (`/ws/jobs/{id}`) + polling fallback
- Job store: **Redis** (ixtiyoriy) yoki in-memory
- Rate limiting + ixtiyoriy `X-API-Key`
- Docker / Railway / Render deploy

## Tezkor start (lokal)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env ga ELEVENLABS_API_KEY yozing

# tizimda ffmpeg bo'lishi shart
python main.py
```

Ochiladi: **http://localhost:8000**

### Muhim
- Birinchi marta Whisper modeli yuklanadi (internet kerak)
- `.env` ni hech qachon commit qilmang

## API qisqacha

| Method | Path | Izoh |
|--------|------|------|
| POST | `/api/translate` | `video` **yoki** `video_url` + tillar/model/ovoz → `job_id` (202) |
| GET | `/api/jobs/{id}` | Progress / status (polling) |
| WS | `/ws/jobs/{id}` | Real-time progress |
| GET | `/api/jobs/{id}/download` | Tayyor MP4 |
| GET | `/api/health` | Health + active jobs |
| GET | `/api/languages` `/api/voices` `/api/models` | Meta |

Form maydonlari: `target_language`, `burn_subtitles`, `model_size`, `voice`, `video_url`.

Header (ixtiyoriy): `X-API-Key` — agar `NISME_API_KEY` o‘rnatilgan bo‘lsa.

## Redis (multi-worker)

```bash
# .env
REDIS_URL=redis://localhost:6379/0
```

`REDIS_URL` bo‘lmasa avtomatik in-memory store ishlatiladi (bitta worker uchun yetarli).

## Deploy

### Docker (lokal)

```bash
docker build -t nisme-ai .
docker run --rm -p 8000:8000 \
  -e ELEVENLABS_API_KEY=sk_xxx \
  nisme-ai
```

### Railway

1. [railway.app](https://railway.app) → New Project → GitHub repo
2. Root = loyiha papkasi (`Dockerfile` bor)
3. Variables:
   - `ELEVENLABS_API_KEY` (majburiy)
   - `NISME_API_KEY` (ixtiyoriy)
   - `REDIS_URL` (Railway Redis plugin bo‘lsa)
4. Deploy → `/api/health` tekshiring

`railway.toml` tayyor.

### Render

1. [render.com](https://render.com) → New → Blueprint
2. `render.yaml` ni tanlang yoki Docker Web Service qo‘shing
3. Environment variables xuddi yuqoridagidek
4. Free planda sovuq start sekin bo‘lishi mumkin; Whisper model birinchi so‘rovda yuklanadi

### Eslatmalar (cloud)

- Whisper CPU da sekin — qisqa videolar bilan boshlang (`tiny` / `base`)
- ElevenLabs kreditini kuzating (har segment = TTS chaqiruvi)
- Ephemeral disk: job natijalari TTL dan keyin yo‘qoladi
- Productionda `NISME_API_KEY` va Redis yoqing

## Loyiha tuzilishi

```
nisme-ai-fullstack/
├── Dockerfile
├── render.yaml
├── railway.toml
├── .dockerignore
├── .gitignore
├── README.md
├── backend/
│   ├── main.py              # FastAPI + WS + jobs
│   ├── jobs.py              # Memory / Redis store
│   ├── downloader.py        # yt-dlp URL yuklash
│   ├── audio.py
│   ├── transcription.py
│   ├── translate.py
│   ├── dubbing.py
│   ├── subtitles.py
│   ├── video.py
│   ├── requirements.txt
│   ├── .env.example
│   └── VIDEOLINGO_INTEGRATION.md
└── frontend/
    └── index.html
```

## Bosqichlar tarixi

1. Xavfsizlik, fayl tozalash, hajm limiti  
2. Model + ovoz tanlash, yaxshi xatolar  
3. Background job, rate limit, API key  
4. WebSocket, Redis store, URL yuklash, Docker/Railway/Render  

## Litsenziya / manba

Segment-dublyaj arxitekturasi [VideoLingo](https://github.com/Huanshere/VideoLingo) (Apache-2.0) dan ilhomlangan; kod qayta yozilgan.
