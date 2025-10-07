from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import threading
import queue

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
        # Resolve ffmpeg path from settings/env/PATH
        ffmpeg_path = settings.FFMPEG_BIN or os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
        # If FFMPEG_BIN is a directory, append the executable name
        if ffmpeg_path and os.path.isdir(ffmpeg_path):
            candidate = os.path.join(ffmpeg_path, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if os.path.isfile(candidate):
                ffmpeg_path = candidate
        # If configured path is not a file, try PATH
        if ffmpeg_path and not os.path.isfile(ffmpeg_path):
            path_guess = shutil.which("ffmpeg")
            if path_guess:
                ffmpeg_path = path_guess
        logger.info(f"FFmpeg resolved path: '{ffmpeg_path}' exists={bool(ffmpeg_path and os.path.exists(ffmpeg_path))} isfile={bool(ffmpeg_path and os.path.isfile(ffmpeg_path))}")
        if not ffmpeg_path:
            logger.warning("ffmpeg not found (FFMPEG_BIN/Path); only raw PCM s16le will work. Install FFmpeg for broader format support.")

        # Persistent ffmpeg pipeline
        input_mime: str | None = None
        ffmpeg_proc: subprocess.Popen | None = None
        pcm_queue: "queue.Queue[bytes]" = queue.Queue()
        stop_reader = threading.Event()

        def start_ffmpeg(mime: str) -> subprocess.Popen:
            fmt = "webm" if "webm" in mime else ("ogg" if "ogg" in mime else "webm")
            cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                fmt,
                "-i",
                "pipe:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "pipe:1",
            ]
            logger.info(f"Launching ffmpeg streaming pipeline: {' '.join(cmd)}")
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

        def reader_thread(proc: subprocess.Popen):
            try:
                assert proc.stdout is not None
                while not stop_reader.is_set():
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    pcm_queue.put(chunk)
            except Exception as e:
                logger.debug(f"ffmpeg reader thread terminated: {e}")

        reader: threading.Thread | None = None

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

            # Client can send control text messages or JSON init
            if message.get("text") is not None:
                raw_text = str(message["text"]).strip()
                # Try JSON init
                try:
                    msg = json.loads(raw_text)
                    if isinstance(msg, dict) and msg.get("type") == "init":
                        input_mime = str(msg.get("mimeType") or "")
                        logger.info(f"Client init mimeType='{input_mime}'")
                        # Start ffmpeg now if available and not started
                        if ffmpeg_path and ffmpeg_proc is None:
                            try:
                                ffmpeg_proc = start_ffmpeg(input_mime)
                                reader = threading.Thread(target=reader_thread, args=(ffmpeg_proc,), daemon=True)
                                reader.start()
                            except Exception as e:
                                logger.exception("Failed to start ffmpeg pipeline")
                                await websocket.send_json({
                                    "type": "error",
                                    "message": f"Failed to start FFmpeg: {e}"
                                })
                        continue
                except Exception:
                    # Not JSON, treat as control
                    pass

                text_msg = raw_text.lower()
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

            # If ffmpeg not available, cannot decode compressed formats reliably
            if not ffmpeg_path:
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "FFmpeg not found. Install it or set FFMPEG_BIN to decode browser audio."
                    })
                except Exception:
                    pass
                continue

            # Ensure ffmpeg pipeline started after receiving init
            if ffmpeg_proc is None:
                if input_mime:
                    try:
                        ffmpeg_proc = start_ffmpeg(input_mime)
                        reader = threading.Thread(target=reader_thread, args=(ffmpeg_proc,), daemon=True)
                        reader.start()
                    except Exception as e:
                        logger.exception("Failed to start ffmpeg pipeline")
                        await websocket.send_json({"type": "error", "message": f"Failed to start FFmpeg: {e}"})
                        continue
                else:
                    # Ask client to send init first
                    try:
                        await websocket.send_json({"type": "error", "message": "Send init with mimeType before audio"})
                    except Exception:
                        pass
                    continue

            # Write compressed chunk to ffmpeg stdin
            try:
                assert ffmpeg_proc and ffmpeg_proc.stdin
                ffmpeg_proc.stdin.write(chunk)
            except Exception as e:
                logger.warning(f"ffmpeg stdin write failed: {e}")
                continue

            # Drain available PCM from queue and feed recognizer
            try:
                drained = False
                while not pcm_queue.empty():
                    drained = True
                    pcm = pcm_queue.get()
                    if rec.AcceptWaveform(pcm):
                        result = json.loads(rec.Result())
                        await websocket.send_json({"type": "result", "final": True, "result": result})
                    else:
                        partial = json.loads(rec.PartialResult())
                        await websocket.send_json({"type": "result", "final": False, "result": partial})
                if not drained:
                    # No PCM yet; continue receiving
                    pass
            except Exception as e:
                logger.warning(f"Recognizer error: {e}")

    except Exception:
        logger.exception("WebSocket STT error")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        # Cleanup ffmpeg resources
        try:
            stop_reader.set()
        except Exception:
            pass
        try:
            if ffmpeg_proc and ffmpeg_proc.stdin:
                ffmpeg_proc.stdin.close()
        except Exception:
            pass
        try:
            if ffmpeg_proc and ffmpeg_proc.poll() is None:
                ffmpeg_proc.terminate()
        except Exception:
            pass
