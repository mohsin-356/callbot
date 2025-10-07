from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from vosk import Model, KaldiRecognizer
    import json
    import wave
except Exception:  # Library may not be available in all environments
    Model = None  # type: ignore
    KaldiRecognizer = None  # type: ignore


class VoskSTT:
    def __init__(self, model_path: str | Path | None = None) -> None:
        # Resolve model path priority:
        # 1) explicit arg
        # 2) VOSK_MODEL_DIR env var
        # 3) bundled model under backend/voskmodels/<first subdir>
        resolved: Path | None = None
        if model_path:
            resolved = Path(model_path)
        else:
            env_dir = os.getenv("VOSK_MODEL_DIR")
            if env_dir:
                resolved = Path(env_dir)
            else:
                # backend/app/services -> parents[2] == backend/
                backend_dir = Path(__file__).resolve().parents[2]
                bundle_root = backend_dir / "voskmodels"
                if bundle_root.exists() and bundle_root.is_dir():
                    # pick the first subdir as model folder
                    subdirs = [p for p in bundle_root.iterdir() if p.is_dir()]
                    if subdirs:
                        resolved = subdirs[0]
                        logger.info(f"Using bundled Vosk model at {resolved}")
        # Fallback to a sensible default if still None (will error later if not present)
        self.model_path = resolved or Path("voskmodels")
        self._model: Optional[Model] = None

    def load(self) -> None:
        if self._model is None:
            if Model is None:
                raise RuntimeError(
                    "Vosk Python package is not installed. Install with: python -m pip install vosk==0.3.44"
                )
            if not self.model_path.exists():
                raise FileNotFoundError(f"Vosk model not found at {self.model_path}")
            logger.info(f"Loading Vosk model from {self.model_path}")
            self._model = Model(str(self.model_path))

    def get_model(self):
        self.load()
        assert self._model is not None
        return self._model

    def transcribe_wav(self, wav_path: str | Path) -> str:
        self.load()
        assert self._model is not None

        with wave.open(str(wav_path), "rb") as wf:
            # Require mono and 16-bit PCM; allow any sample rate
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ValueError(
                    "WAV must be mono 16-bit PCM. Tip: install FFmpeg and we will auto-convert (winget install FFmpeg.FFmpeg)."
                )

            rec = KaldiRecognizer(self._model, wf.getframerate())  # type: ignore[arg-type]
            rec.SetWords(True)
            text = []
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    res = rec.Result()
                    text.append(json.loads(res).get("text", ""))
            final = json.loads(rec.FinalResult()).get("text", "")
            text.append(final)
            out = " ".join(t for t in text if t)
            logger.debug(f"Transcription: {out}")
            return out
