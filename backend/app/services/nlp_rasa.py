from __future__ import annotations

from typing import Any, Dict, List

import httpx
from loguru import logger

from ..core.config import settings


class RasaClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url or settings.RASA_URL.rstrip("/")
        self.timeout = timeout

    async def send_message(self, sender_id: str, message: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/webhooks/rest/webhook"
        payload = {"sender": sender_id, "message": message}
        logger.debug(f"Rasa request: {payload}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"Rasa response: {data}")
            return data  # Typically list of messages: {text, image, buttons, ...}
