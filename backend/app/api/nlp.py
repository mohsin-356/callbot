from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from ..services.nlp_rasa import RasaClient

router = APIRouter()


class NLPIn(BaseModel):
    sender_id: str
    message: str


@router.post("/nlp", tags=["nlp"])  # /api/nlp
async def nlp_message(payload: NLPIn) -> List[Dict[str, Any]]:
    try:
        client = RasaClient()
        return await client.send_message(sender_id=payload.sender_id, message=payload.message)
    except Exception as e:
        logger.exception("NLP (Rasa) call failed")
        raise HTTPException(status_code=500, detail=str(e))
