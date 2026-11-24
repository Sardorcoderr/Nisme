"""
transcription.py — Whisper yordamida audio → matn moduli

⚠️  Fayl whisper.py emas, transcription.py deb atalgan (aks holda `import whisper`
    lokal faylni topib ImportError beradi).

O'zgarishlar:
  * source_language beriladi — Whisper tilni "taxmin qilmaydi" (o'zbek tili ko'pincha
    qozoq/turk deb noto'g'ri aniqlanardi va natijada tarjima buzilardi).
  * Audio ffmpeg_utils orqali o'qiladi (Windows'da PATH kerak emas).
  * CPU'da fp16 o'chiriladi (ogohlantirish va sekinlashuvsiz).
"""

from ffmpeg_utils import load_audio_np

_models: dict = {}

ALLOWED_MODEL_SIZES = ("tiny", "base", "small")


def get_model(model_size: str = "base"):
    import whisper  # kech import: server whisper'siz ham ishga tushaversin (health tekshiruvi uchun)

    if model_size not in ALLOWED_MODEL_SIZES:
        model_size = "base"
    if model_size not in _models:
        print(f"⏳ Whisper '{model_size}' model yuklanmoqda...")
        _models[model_size] = whisper.load_model(model_size)
        print(f"✅ Model '{model_size}' tayyor!")
    return _models[model_size]


def transcribe(audio_path: str, model_size: str = "base", language: str = "auto") -> dict:
    """
    Returns:
        {"text": str, "language": str, "segments": [{"start","end","text"}, ...]}
    """
    import torch

    model = get_model(model_size)
    audio = load_audio_np(audio_path)

    lang = None if (not language or language == "auto") else language
    result = model.transcribe(
        audio,
        language=lang,
        fp16=torch.cuda.is_available(),
        condition_on_previous_text=False,  # takrorlanuvchi "hallutsinatsiya" kamayadi
    )

    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result.get("segments", [])
        if s["text"].strip()
    ]
    return {
        "text": result["text"].strip(),
        "language": lang or result.get("language", "unknown"),
        "segments": segments,
    }
