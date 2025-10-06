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

        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                logger.info("WebSocket STT client disconnected")
                break

            # Client can send control text messages
            if "text" in message and message["text"] is not None:
                text_msg = message["text"].strip().lower()
                if text_msg in {"close", "stop", "final"}:
                    final = json.loads(rec.FinalResult())
                    await websocket.send_json({"type": "final", "result": final})
                    await websocket.close()
                    break
                # Ignore other control messages
                continue

            if "bytes" not in message or message["bytes"] is None:
                # No audio payload in this frame
                continue

            chunk: bytes = message["bytes"]

            pcm: bytes | None = None
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
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
                    logger.warning(f"ffmpeg failed on chunk, using raw bytes: {ce.stderr.decode(errors='ignore')}")
                    pcm = chunk
                finally:
                    try:
                        src_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                # ffmpeg not available; assume raw PCM
                pcm = chunk

            if not pcm:
                continue

            if rec.AcceptWaveform(pcm):
                result = json.loads(rec.Result())
                await websocket.send_json({"type": "result", "final": True, "result": result})
            else:
                partial = json.loads(rec.PartialResult())
                await websocket.send_json({"type": "result", "final": False, "result": partial})

        # Send final result on close
        final = json.loads(rec.FinalResult())
        await websocket.send_json({"type": "final", "result": final})
    except Exception:
        logger.exception("WebSocket STT error")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
