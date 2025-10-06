from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from ..db.mongo import get_database

router = APIRouter()


@router.get("/db/ping", tags=["db"])  # /api/db/ping
async def db_ping(request: Request) -> dict:
    try:
        db = get_database(request.app)
        res = await db.command("ping")
        logger.info(f"Mongo ping result: {res}")
        ok = res.get("ok", 0) == 1
        return {"ok": ok, "raw": res}
    except Exception as e:
        logger.exception("Mongo ping failed")
        raise HTTPException(status_code=500, detail=str(e))
