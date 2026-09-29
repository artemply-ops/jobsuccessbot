"""Распознавание речи. Голос содержит ПДн, поэтому только Yandex SpeechKit (РФ).

MVP: синхронный API v1, ограничение 30 секунд и 1 МБ на фрагмент.
Голосовые Telegram приходят в OGG/Opus, это родной формат SpeechKit.
Длинные записи (больше 30 с) требуют асинхронного API через Object Storage, это следующий шаг.
"""

import httpx

from app.config import get_settings

YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
MAX_BYTES = 1_000_000


class STTError(RuntimeError):
    pass


STUB = "Распознавание голоса пока не подключено. Напишите то же самое текстом, я всё сохраню."


def available() -> bool:
    s = get_settings()
    return s.stt_provider == "yandex" and bool(s.yandex_api_key)


def to_ogg_opus(data: bytes) -> tuple[bytes, float]:
    """Любой аудиоформат браузера (webm, mp4, ogg) -> OGG/Opus 48 кГц моно. Возвращает (байты, длительность)."""
    import io

    import av

    src = av.open(io.BytesIO(data))
    out_buf = io.BytesIO()
    dst = av.open(out_buf, mode="w", format="ogg")
    stream = dst.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    duration = 0.0
    for frame in src.decode(audio=0):
        duration += frame.samples / frame.sample_rate
        for rf in resampler.resample(frame):
            for pkt in stream.encode(rf):
                dst.mux(pkt)
    for pkt in stream.encode(None):
        dst.mux(pkt)
    dst.close()
    src.close()
    return out_buf.getvalue(), duration


async def transcribe_any(data: bytes) -> str:
    ogg, duration = to_ogg_opus(data)
    return await transcribe_ogg(ogg, int(duration) + 1)


async def transcribe_ogg(data: bytes, duration_sec: int | None = None) -> str:
    s = get_settings()
    if not available():
        raise STTError(STUB)
    if len(data) > MAX_BYTES or (duration_sec and duration_sec > 30):
        raise STTError("Голосовое длиннее 30 секунд. Разбейте на несколько коротких.")
    params = {"lang": "ru-RU", "format": "oggopus"}
    if s.yandex_folder_id:
        params["folderId"] = s.yandex_folder_id
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(YANDEX_STT_URL, params=params, content=data,
                         headers={"Authorization": f"Api-Key {s.yandex_api_key}"})
    if r.status_code != 200:
        raise STTError(f"SpeechKit вернул {r.status_code}")
    return r.json().get("result", "")
