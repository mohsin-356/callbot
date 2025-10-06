from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from ..services.stt_vosk import VoskSTT
from ..core.config import settings

router = APIRouter()


@router.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket STT connection accepted")

    # Prepare recognizer (16k expected if we normalize; otherwise best-effort)
    stt = VoskSTT()
    model = stt.get_model()

    try:
        # Use 16kHz target when we normalize with ffmpeg
        try:
            from vosk import KaldiRecognizer  # type: ignore
        except Exception as e:  # pragma: no cover
            await websocket.close(code=1011)
            logger.exception("Vosk not available")
            return

        rec = KaldiRecognizer(model, 16000)
        rec.SetWords(True)
        ffmpeg_path = settings.FFMPEG_BIN or os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.warning("ffmpeg not found (FFMPEG_BIN/Path); only raw PCM s16le will work. Install FFmpeg for broader format support.")

        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                logger.info("WebSocket STT client disconnected")
                break

            # Starlette messages include a type
            if message.get("type") == "websocket.disconnect":
                logger.info("WebSocket disconnect received")
                break

            # Client can send control text messages
            if message.get("text") is not None:
                text_msg = str(message["text"]).strip().lower()
                if text_msg in {"close", "stop", "final"}:
                    try:
                        final = json.loads(rec.FinalResult())
                        await websocket.send_json({"type": "final", "result": final})
                    except Exception:
                        pass
                    finally:
                        try:
                            await websocket.close()
                        except Exception:
                            pass
                    break
                # Ignore other control messages
                continue

            if message.get("bytes") is None:
                # No audio payload in this frame
                continue

            chunk: bytes = message["bytes"]

            # If ffmpeg is not present, we cannot decode compressed formats reliably
            if not ffmpeg_path:
                # Optionally, attempt to treat as raw PCM (often won't work from MediaRecorder)
                # Send a one-time hint
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "FFmpeg not found. Install it (winget install FFmpeg.FFmpeg) or set FFMPEG_BIN to ffmpeg.exe to decode browser audio chunks."
                    })
                except Exception:
                    pass
                continue

            # Normalize this chunk to 16k mono s16le using ffmpeg
            with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as src:
                src_path = Path(src.name)
                src.write(chunk)
            try:
                cmd = [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(src_path),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "pipe:1",
                ]
                proc = subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                pcm = proc.stdout
            except subprocess.CalledProcessError as ce:
                logger.warning(f"ffmpeg failed on chunk, skipping: {ce.stderr.decode(errors='ignore')}")
                continue
            finally:
                try:
                    src_path.unlink(missing_ok=True)
                except Exception:
                    pass

            if not pcm:
                continue

            # Feed to recognizer
            try:
                if rec.AcceptWaveform(pcm):
                    result = json.loads(rec.Result())
                    await websocket.send_json({"type": "result", "final": True, "result": result})
                else:
                    partial = json.loads(rec.PartialResult())
                    await websocket.send_json({"type": "result", "final": False, "result": partial})
            except Exception as e:
                logger.warning(f"Recognizer error: {e}")

    except Exception:
        logger.exception("WebSocket STT error")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
