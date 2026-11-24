"""
main.py — Nisme AI: FastAPI backend + frontend serving

Imkoniyatlar:
- Background job + WebSocket progress (+ polling fallback)
- Redis yoki in-memory job store
- Fayl yuklash YOKI YouTube/TikTok URL
- Rate limiting, ixtiyoriy API key
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    Request,
    Header,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

NISME_API_KEY = os.getenv("NISME_API_KEY", "").strip()

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
ALLOWED_MODEL_SIZES = {"tiny", "base", "small"}

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))

# ── TTS provayderlari: VoiceLab (o'zbek) + ElevenLabs (boshqa tillar) ──────
import tts_providers
from ffmpeg_utils import ffmpeg_available

from audio import extract_audio
from transcription import transcribe
from translate import translate_text, translate_segments, SUPPORTED_LANGUAGES
from video import mux_audio_to_video
from dubbing import build_synced_dub, ffprobe_duration
from subtitles import segments_to_srt, burn_subtitles
from downloader import download_video, is_valid_video_url
from jobs import (
    Job,
    JobStatus,
    PIPELINE_STEPS,
    create_job_store,
    _clean_err,
)

app = FastAPI(title="Nisme AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Job-Id"],
)

WORK_DIR = os.path.join(tempfile.gettempdir(), "nisme_ai_jobs")
os.makedirs(WORK_DIR, exist_ok=True)

job_store = create_job_store()

# Rate limit
_rate_hits: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _cleanup_job_dir(job_dir: Optional[str]) -> None:
    try:
        if job_dir and os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass


def _set_progress(job: Job, step: str, progress: int, message: str) -> None:
    job.step = step
    try:
        job.step_index = PIPELINE_STEPS.index(step)
    except ValueError:
        job.step_index = 0
    job.progress = max(0, min(100, int(progress)))
    job.message = message
    job.status = JobStatus.RUNNING
    job_store.save(job)


def _validate_video_file(video: UploadFile) -> None:
    filename = (video.filename or "").lower()
    ext = os.path.splitext(filename)[1]
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Ruxsat etilmagan fayl turi: {ext}. Ruxsat: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )


def _check_api_key(x_api_key: Optional[str]) -> None:
    if not NISME_API_KEY:
        return
    if not x_api_key or x_api_key != NISME_API_KEY:
        raise HTTPException(401, "API key talab qilinadi yoki noto'g'ri. Header: X-API-Key")


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _rate_lock:
        q = _rate_hits[ip]
        while q and q[0] < now - RATE_LIMIT_WINDOW_SEC:
            q.popleft()
        if len(q) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(
                429,
                f"Juda ko'p so'rov. {RATE_LIMIT_WINDOW_SEC}s ichida max {RATE_LIMIT_REQUESTS} ta.",
            )
        q.append(now)


def _run_pipeline(
    job: Job,
    video_path: str,
    target_language: str,
    burn_subtitles_flag: bool,
    model_size: str,
    voice: str,
    source_url: Optional[str] = None,
    source_language: str = "auto",
) -> None:
    job_dir = job.job_dir or os.path.dirname(video_path)
    temp_audio = os.path.join(job_dir, "temp_audio.wav")
    dubbed_audio = os.path.join(job_dir, "dubbed_audio.wav")
    dubbed_video = os.path.join(job_dir, "nisme_translated.mp4")
    output_video = dubbed_video

    try:
        # URL bo'lsa avval yuklab olamiz
        if source_url:
            _set_progress(job, "download", 5, "Video URL dan yuklab olinmoqda...")
            video_path = download_video(source_url, job_dir)
            job.meta["downloaded_from"] = source_url
            job_store.save(job)

        _set_progress(job, "extract_audio", 12, "Audioni ajratyapmiz...")
        extract_audio(video_path, temp_audio)

        _set_progress(job, "transcribe", 28, f"Whisper ({model_size}) bilan matnga aylantirilmoqda...")
        result = transcribe(temp_audio, model_size=model_size, language=source_language)
        detected_lang = result["language"]
        segments = result["segments"]

        if not result["text"]:
            raise RuntimeError(
                "Audiodan matn aniqlanmadi. Videoda nutq bormi? Boshqa model bilan urinib ko'ring."
            )

        job.meta["detected_language"] = detected_lang
        job.meta["segments_count"] = len(segments)
        job_store.save(job)

        total_duration = ffprobe_duration(video_path)

        _set_progress(job, "translate", 42, "Matn tarjima qilinmoqda...")
        if segments:
            translated_segments = translate_segments(
                segments, target_language, source_language=source_language
            )
            job.meta["translation_engine"] = translated_segments[0].get("translation_engine", "google")

            _set_progress(job, "tts_dub", 58, "Sinxron dublyaj yaratilmoqda (TTS)...")

            tts_stats: dict = {}

            def _tts_for_segment(text: str, out_path: str) -> str:
                return tts_providers.synthesize(
                    text, out_path, voice_key=voice, language=target_language, stats=tts_stats
                )

            def _tts_progress(done: int, total: int) -> None:
                # 58% → 82% oralig'ida
                _set_progress(job, "tts_dub", 58 + int(24 * done / max(1, total)),
                              f"Dublyaj yaratilmoqda ({done}/{total})...")

            build_synced_dub(
                segments=translated_segments,
                total_duration=total_duration,
                tts_fn=_tts_for_segment,
                job_dir=job_dir,
                output_path=dubbed_audio,
                on_progress=_tts_progress,
            )
            job.meta.update({k: v for k, v in tts_stats.items()})
        else:
            # Whisper segment bermadi — butun matnni bitta bo'lak sifatida dublyaj qilamiz
            translated_text = translate_text(result["text"], target_language, source_language)
            _set_progress(job, "tts_dub", 58, "Dublyaj yaratilmoqda...")
            translated_segments = [
                {"start": 0.0, "end": total_duration, "translated_text": translated_text}
            ]
            build_synced_dub(
                segments=translated_segments,
                total_duration=total_duration,
                tts_fn=lambda t, p: tts_providers.synthesize(t, p, voice_key=voice, language=target_language),
                job_dir=job_dir,
                output_path=dubbed_audio,
            )
            translated_segments = []

        _set_progress(job, "mux", 84, "Video va audio birlashtirilmoqda...")
        mux_audio_to_video(video_path, dubbed_audio, dubbed_video)

        if burn_subtitles_flag and translated_segments:
            _set_progress(job, "subtitles", 93, "Subtitrlar kuydirilmoqda...")
            srt_path = os.path.join(job_dir, "subs.srt")
            subbed_video = os.path.join(job_dir, "nisme_translated_subs.mp4")
            try:
                segments_to_srt(translated_segments, srt_path)
                burn_subtitles(dubbed_video, srt_path, subbed_video)
                output_video = subbed_video
            except RuntimeError:
                output_video = dubbed_video

        job.output_path = output_video
        job.progress = 100
        job.status = JobStatus.DONE
        job.message = "Tayyor! Yuklab olishingiz mumkin."
        job.finished_at = time.time()
        job.meta["target_language"] = target_language
        job.meta["model_size"] = model_size
        job.meta["voice"] = voice
        job_store.save(job)

    except Exception as e:
        job.status = JobStatus.ERROR
        job.error = _clean_err(str(e))
        job.message = f"Xato: {e}"
        job.finished_at = time.time()
        job_store.save(job)


# ── REST API ────────────────────────────────────────────────────────────────

@app.get("/api/languages")
def get_languages():
    return SUPPORTED_LANGUAGES


@app.get("/api/voices")
def get_voices(language: str = "en"):
    """Tanlangan MAQSAD tilga mos ovozlar (uz → VoiceLab, qolganlari → ElevenLabs)."""
    return tts_providers.list_voices(language)


@app.get("/api/models")
def get_models():
    return {
        "tiny": {"label": "Tiny", "speed": "Eng tez", "accuracy": "Past"},
        "base": {"label": "Base", "speed": "Tez", "accuracy": "O'rtacha (tavsiya)"},
        "small": {"label": "Small", "speed": "Sekinroq", "accuracy": "Yuqori"},
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "ffmpeg": ffmpeg_available(),
        "elevenlabs_configured": tts_providers.elevenlabs_configured(),
        "voicelab_configured": tts_providers.voicelab_configured(),
        "api_key_required": bool(NISME_API_KEY),
        "active_jobs": job_store.count_active(),
        "redis": bool(os.getenv("REDIS_URL", "").strip()),
    }


@app.post("/api/translate")
async def translate_video(
    request: Request,
    video: Optional[UploadFile] = File(None),
    video_url: Optional[str] = Form(None),
    target_language: str = Form("uz"),
    source_language: str = Form("auto"),
    burn_subtitles_flag: bool = Form(False, alias="burn_subtitles"),
    model_size: str = Form("base"),
    voice: str = Form(""),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Video fayl YOKI video_url qabul qiladi, background job yaratadi.
    Progress: WebSocket /ws/jobs/{job_id} yoki GET /api/jobs/{job_id}
    """
    _check_api_key(x_api_key)
    _check_rate_limit(request)
    job_store.purge_old()

    if target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Noma'lum til kodi: {target_language}")
    if model_size not in ALLOWED_MODEL_SIZES:
        raise HTTPException(400, f"Noma'lum model: {model_size}")
    if source_language != "auto" and source_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Noma'lum manba til kodi: {source_language}")
    if source_language == target_language:
        raise HTTPException(400, "Manba va maqsad tillar bir xil. Boshqa til tanlang.")
    voice = (voice or "").strip()

    source_url = (video_url or "").strip() or None
    has_file = video is not None and bool(video.filename)

    if not has_file and not source_url:
        raise HTTPException(400, "Video fayl yoki video_url yuboring.")
    if has_file and source_url:
        raise HTTPException(400, "Faqat bittasini yuboring: fayl YOKI URL.")

    if source_url and not is_valid_video_url(source_url):
        raise HTTPException(400, "Noto'g'ri video URL.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    video_path = ""
    if has_file:
        assert video is not None
        try:
            _validate_video_file(video)
            if video.size is not None and video.size > MAX_FILE_SIZE:
                raise HTTPException(413, f"Fayl juda katta. Maksimal: {MAX_FILE_SIZE_MB} MB.")
        except HTTPException:
            _cleanup_job_dir(job_dir)
            raise

        video_path = os.path.join(job_dir, "input_" + (video.filename or "video.mp4"))
        bytes_written = 0
        with open(video_path, "wb") as f:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_FILE_SIZE:
                    _cleanup_job_dir(job_dir)
                    raise HTTPException(413, f"Fayl juda katta. Maksimal: {MAX_FILE_SIZE_MB} MB.")
                f.write(chunk)
        if bytes_written == 0:
            _cleanup_job_dir(job_dir)
            raise HTTPException(400, "Bo'sh fayl yuborildi.")

    job = Job(
        id=job_id,
        status=JobStatus.QUEUED,
        message="Navbatda...",
        job_dir=job_dir,
        meta={
            "filename": video.filename if has_file and video else None,
            "video_url": source_url,
            "target_language": target_language,
            "source_language": source_language,
            "model_size": model_size,
            "voice": voice,
            "burn_subtitles": burn_subtitles_flag,
        },
    )
    job_store.save(job)

    def _worker():
        _run_pipeline(
            job=job,
            video_path=video_path,
            target_language=target_language,
            burn_subtitles_flag=burn_subtitles_flag,
            model_size=model_size,
            voice=voice,
            source_url=source_url,
            source_language=source_language,
        )

    threading.Thread(target=_worker, daemon=True).start()

    return JSONResponse(status_code=202, content=job.to_public(), headers={"X-Job-Id": job_id})


@app.get("/api/jobs/{job_id}")
def get_job_status(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _check_api_key(x_api_key)
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job topilmadi yoki muddati o'tgan.")
    return job.to_public()


@app.get("/api/jobs/{job_id}/download")
def download_job_result(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _check_api_key(x_api_key)
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job topilmadi yoki muddati o'tgan.")
    if job.status != JobStatus.DONE or not job.output_path:
        raise HTTPException(409, "Video hali tayyor emas.")
    if not os.path.isfile(job.output_path):
        raise HTTPException(410, "Natija fayli o'chirilgan.")

    target = job.meta.get("target_language", "uz")
    return FileResponse(
        job.output_path,
        media_type="video/mp4",
        filename=f"translated_{target}.mp4",
    )


# ── WebSocket progress ──────────────────────────────────────────────────────

@app.websocket("/ws/jobs/{job_id}")
async def ws_job_progress(websocket: WebSocket, job_id: str):
    """
    Real-time progress. Mijoz ulanadi, server holat o'zgarishini push qiladi.
    Polling fallback ham ishlayveradi.
    """
    await websocket.accept()
    last_payload = None
    try:
        while True:
            job = job_store.get(job_id)
            if not job:
                await websocket.send_json({"error": "Job topilmadi", "status": "error"})
                break

            payload = job.to_public()
            # Faqat o'zgarganda yuborish (trafik tejash)
            if payload != last_payload:
                await websocket.send_json(payload)
                last_payload = payload

            if job.status in (JobStatus.DONE, JobStatus.ERROR):
                break

            await asyncio.sleep(0.7)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


# ── Frontend ────────────────────────────────────────────────────────────────
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    print("🚀 Nisme AI ishga tushmoqda: http://localhost:8000")
    print(f"   Max fayl: {MAX_FILE_SIZE_MB} MB")
    print(f"   Rate limit: {RATE_LIMIT_REQUESTS}/{RATE_LIMIT_WINDOW_SEC}s")
    print("   API key:", "ha" if NISME_API_KEY else "yo'q")
    print("   ffmpeg:", ffmpeg_available() or "TOPILMADI (pip install imageio-ffmpeg)")
    print("   VoiceLab:", "ulangan" if tts_providers.voicelab_configured() else "yo'q (VOICELAB_API_KEY)")
    print("   ElevenLabs:", "ulangan" if tts_providers.elevenlabs_configured() else "yo'q")
    print("   WebSocket: /ws/jobs/{job_id}")
    print("   URL yuklash: YouTube / TikTok / ... (yt-dlp)")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
