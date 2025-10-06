from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from loguru import logger

from ..services.tts_coqui import CoquiTTS

router = APIRouter()


class TTSIn(BaseModel):
    text: str
    filename: Optional[str] = "tts.wav"


@router.post("/tts", tags=["tts"], response_class=FileResponse)  # /api/tts
async def tts_synthesize(payload: TTSIn):
    try:
        tts = CoquiTTS()
        out_path: Path = tts.synthesize_to_file(payload.text, filename=payload.filename or "tts.wav")
        if not out_path.exists():
            raise HTTPException(status_code=500, detail="TTS synthesis failed")
        return FileResponse(path=str(out_path), media_type="audio/wav", filename=out_path.name)
    except Exception as e:
        logger.exception("TTS synthesis error")
        raise HTTPException(status_code=500, detail=str(e))
