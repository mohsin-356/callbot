from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])  # /api/health
async def health_check() -> dict:
    return {"status": "ok"}
