from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from ..services.stt_vosk import VoskSTT

router = APIRouter()


@router.post("/stt", tags=["stt"])  # /api/stt
async def stt_transcribe(file: UploadFile = File(...)) -> dict:
    if file.content_type not in {"audio/wav", "audio/x-wav"}:
        raise HTTPException(status_code=400, detail="Only WAV files are supported (audio/wav)")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp_path = Path(tmp.name)
            data = await file.read()
            tmp.write(data)

        stt = VoskSTT()
        text = stt.transcribe_wav(tmp_path)
        return {"text": text}
    except Exception as e:
        logger.exception("STT transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if 'tmp_path' in locals() and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
