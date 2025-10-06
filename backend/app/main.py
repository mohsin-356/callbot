from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .db.mongo import connect_to_mongo, close_mongo_connection
from .api.health import router as health_router
from .api.db import router as db_router
from .api.stt import router as stt_router
from .api.tts import router as tts_router
from .api.nlp import router as nlp_router
from .api.ws_stt import router as ws_stt_router

app = FastAPI(title="Callbot Backend", version="0.1.0")

# CORS
if settings.ALLOW_CORS_ANY:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Routers
app.include_router(health_router, prefix="/api")
app.include_router(db_router, prefix="/api")
app.include_router(stt_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(nlp_router, prefix="/api")
app.include_router(ws_stt_router)


@app.on_event("startup")
async def on_startup() -> None:
    await connect_to_mongo(app)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await close_mongo_connection(app)
