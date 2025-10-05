from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from redis.asyncio import Redis

from ..core.config import settings


_redis: Optional[Redis] = None
_lock = asyncio.Lock()


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        async with _lock:
            if _redis is None:
                logger.info(f"Connecting to Redis at {settings.REDIS_URL}")
                _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    assert _redis is not None
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
