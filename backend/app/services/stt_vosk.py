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
        # Allow override via env var for easier local (non-Docker) setup
        self.model_path = Path(model_path or os.getenv("VOSK_MODEL_DIR", "/models/vosk/en-us"))
        self._model: Optional[Model] = None

    def load(self) -> None:
        if self._model is None:
            if Model is None:
                raise RuntimeError("vosk is not available in the environment")
            if not self.model_path.exists():
                raise FileNotFoundError(f"Vosk model not found at {self.model_path}")
            logger.info(f"Loading Vosk model from {self.model_path}")
            self._model = Model(str(self.model_path))

    def transcribe_wav(self, wav_path: str | Path) -> str:
        self.load()
        assert self._model is not None

        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() not in [8000, 16000, 32000, 44100, 48000]:
                raise ValueError("WAV must be mono PCM 16-bit. Consider resampling before transcription.")

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
