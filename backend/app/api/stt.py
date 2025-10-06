from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from ..services.stt_vosk import VoskSTT
from ..core.config import settings

router = APIRouter()


@router.post("/stt", tags=["stt"])  # /api/stt
async def stt_transcribe(file: UploadFile = File(...)) -> dict:
    try:
        # 1) Persist upload to a temp file (preserve original extension if present)
        orig_suffix = Path(file.filename or "audio").suffix or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=orig_suffix) as src_tmp:
            src_path = Path(src_tmp.name)
            src_tmp.write(await file.read())

        # 2) If ffmpeg is available, normalize to mono 16k s16 WAV
        dst_path: Path | None = None
        ffmpeg_path = settings.FFMPEG_BIN or os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
        # If FFMPEG_BIN is a directory, append the executable name
        if ffmpeg_path and os.path.isdir(ffmpeg_path):
            candidate = os.path.join(ffmpeg_path, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if os.path.isfile(candidate):
                ffmpeg_path = candidate
        if ffmpeg_path:
            fd, out_name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            dst_path = Path(out_name)
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                str(src_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(dst_path),
            ]
            try:
                logger.info(f"Normalizing audio via ffmpeg: {' '.join(cmd)}")
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                input_path = dst_path
            except subprocess.CalledProcessError as ce:
                logger.warning(f"ffmpeg normalization failed, proceeding with original file: {ce}")
                # Fallback to original
                input_path = src_path
        else:
            logger.info("ffmpeg not found (FFMPEG_BIN/Path); attempting to process original file as WAV")
            input_path = src_path

        # 3) Transcribe using Vosk
        stt = VoskSTT()
        text = stt.transcribe_wav(input_path)
        return {"text": text}
    except ValueError as e:
        # Invalid WAV after best-effort. If ffmpeg isn't installed, guide user.
        msg = str(e)
        if not (os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")):
            msg += " | Tip: install FFmpeg (winget install FFmpeg.FFmpeg) or set FFMPEG_BIN to ffmpeg.exe"
        logger.exception("STT validation error (invalid audio/WAV format)")
        raise HTTPException(status_code=400, detail=msg)
    except Exception as e:
        logger.exception("STT transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if 'src_path' in locals() and src_path.exists():
                src_path.unlink(missing_ok=True)
            if 'dst_path' in locals() and dst_path and dst_path.exists():
                dst_path.unlink(missing_ok=True)
        except Exception:
            pass
