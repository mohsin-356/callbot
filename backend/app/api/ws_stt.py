from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import threading
import queue
import math

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
        raw_mode: bool = False
        raw_sr: int = 16000
        ffmpeg_proc: subprocess.Popen | None = None
        pcm_queue: "queue.Queue[bytes]" = queue.Queue()
        stop_reader = threading.Event()

        # VAD helpers
        def _rms_int16(frame_bytes: bytes) -> float:
            if not frame_bytes:
                return 0.0
            n = len(frame_bytes) // 2
            if n == 0:
                return 0.0
            # Interpret as little-endian signed int16
            total = 0.0
            for i in range(0, len(frame_bytes), 2):
                s = int.from_bytes(frame_bytes[i:i+2], 'little', signed=True)
                total += (s / 32768.0) ** 2
            return math.sqrt(total / n)

        class VADGate:
            def __init__(self, sample_rate: int) -> None:
                self.sr = sample_rate
                self.frame_ms = 20  # 10/20/30 allowed
                self.bytes_per_sample = 2
                self.frame_bytes = int(self.sr * self.frame_ms / 1000) * self.bytes_per_sample
                self.hangover = max(0, int(settings.VAD_HANGOVER_FRAMES or 0))
                self.countdown = 0
                self.buf = bytearray()
                self.vad = None
                if settings.VAD_ENABLED:
                    try:
                        import webrtcvad  # type: ignore
                        self.vad = webrtcvad.Vad(int(settings.VAD_AGGRESSIVENESS))
                        logger.info(f"VAD enabled (webrtcvad, aggressiveness={int(settings.VAD_AGGRESSIVENESS)}) sr={sample_rate}")
                    except Exception:
                        self.vad = None
                        logger.info("VAD fallback to RMS threshold (webrtcvad unavailable)")

            def process(self, pcm_bytes: bytes) -> list[bytes]:
                out: list[bytes] = []
                if not settings.VAD_ENABLED:
                    # pass-through
                    out.append(pcm_bytes)
                    return out
                self.buf.extend(pcm_bytes)
                thr = float(settings.VAD_RMS_THRESHOLD or 0.015)
                while len(self.buf) >= self.frame_bytes:
                    frame = bytes(self.buf[: self.frame_bytes])
                    del self.buf[: self.frame_bytes]
                    is_speech = False
                    if self.vad is not None:
                        try:
                            is_speech = self.vad.is_speech(frame, self.sr)
                        except Exception:
                            is_speech = _rms_int16(frame) >= thr
                    else:
                        is_speech = _rms_int16(frame) >= thr
                    if is_speech:
                        self.countdown = self.hangover
                        out.append(frame)
                    elif self.countdown > 0:
                        self.countdown -= 1
                        out.append(frame)
                    else:
                        # drop non-speech
                        pass
                return out

        def start_ffmpeg(mime: str) -> subprocess.Popen:
            # Use matroska demuxer for webm streams; ogg for OGG/Opus
            fmt = "matroska" if "webm" in mime else ("ogg" if "ogg" in mime else "matroska")
            cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags", "+genpts",
                "-analyzeduration", "0",
                "-probesize", "32k",
                "-flags", "low_delay",
                "-fflags", "+nobuffer",
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

        def stderr_thread(proc: subprocess.Popen):
            try:
                assert proc.stderr is not None
                while not stop_reader.is_set():
                    line = proc.stderr.readline()
                    if not line:
                        break
                    logger.info(f"ffmpeg: {line.decode(errors='ignore').strip()}")
            except Exception:
                pass

        reader: threading.Thread | None = None
        err_reader: threading.Thread | None = None

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
                        # raw PCM path
                        mode = str(msg.get("mode") or "").lower()
                        if mode == "pcm":
                            raw_mode = True
                            try:
                                raw_sr = int(msg.get("sampleRate") or 16000)
                            except Exception:
                                raw_sr = 16000
                            # Recreate recognizer with requested sample rate
                            try:
                                rec = KaldiRecognizer(model, raw_sr)
                                rec.SetWords(True)
                            except Exception as e:
                                logger.exception("Failed to set recognizer sample rate")
                            logger.info(f"Client init raw PCM mode sampleRate={raw_sr}")
                            # Initialize VAD gate for raw stream
                            vad_gate = VADGate(raw_sr)
                            continue

                        input_mime = str(msg.get("mimeType") or "")
                        logger.info(f"Client init mimeType='{input_mime}'")
                        # Start ffmpeg now if available and not started
                        if ffmpeg_path and ffmpeg_proc is None:
                            try:
                                ffmpeg_proc = start_ffmpeg(input_mime)
                                reader = threading.Thread(target=reader_thread, args=(ffmpeg_proc,), daemon=True)
                                reader.start()
                                err_reader = threading.Thread(target=stderr_thread, args=(ffmpeg_proc,), daemon=True)
                                err_reader.start()
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

            # If raw mode, feed bytes directly as PCM16LE with VAD gating
            if raw_mode:
                try:
                    # Gate with VAD
                    gated_frames = vad_gate.process(chunk) if settings.VAD_ENABLED else [chunk]
                    for frame in gated_frames:
                        if not frame:
                            continue
                        if rec.AcceptWaveform(frame):
                            result = json.loads(rec.Result())
                            await websocket.send_json({"type": "result", "final": True, "result": result})
                        else:
                            partial = json.loads(rec.PartialResult())
                            await websocket.send_json({"type": "result", "final": False, "result": partial})
                except Exception as e:
                    logger.warning(f"Recognizer error (raw): {e}")
                continue

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
                        err_reader = threading.Thread(target=stderr_thread, args=(ffmpeg_proc,), daemon=True)
                        err_reader.start()
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
                # Optionally log size of compressed chunk (first few only)
                try:
                    if getattr(ws_stt, "_logged_chunks", 0) < 5:
                        logger.info(f"WS chunk bytes={len(chunk)}")
                        setattr(ws_stt, "_logged_chunks", getattr(ws_stt, "_logged_chunks", 0) + 1)
                except Exception:
                    pass
                ffmpeg_proc.stdin.write(chunk)
                try:
                    ffmpeg_proc.stdin.flush()
                except Exception:
                    pass
                # Detect unexpected ffmpeg termination early
                if ffmpeg_proc.poll() is not None:
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": "FFmpeg exited unexpectedly. Check logs and mimeType compatibility."
                        })
                    except Exception:
                        pass
                    break
            except Exception as e:
                logger.warning(f"ffmpeg stdin write failed: {e}")
                continue

            # Drain available PCM from queue and feed recognizer
            try:
                drained = False
                while not pcm_queue.empty():
                    drained = True
                    pcm = pcm_queue.get()
                    # Initialize VAD for ffmpeg path at 16k
                    if 'vad_gate_ff' not in locals():
                        vad_gate_ff = VADGate(16000)
                    gated_frames = vad_gate_ff.process(pcm) if settings.VAD_ENABLED else [pcm]
                    for frame in gated_frames:
                        if not frame:
                            continue
                        if rec.AcceptWaveform(frame):
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
