from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

try:
    # Coqui TTS synthesizer
    from TTS.api import TTS  # type: ignore
except Exception:
    TTS = None  # type: ignore


class CoquiTTS:
    def __init__(self, model_name: str = "tts_models/en/ljspeech/tacotron2-DDC", out_dir: str | Path = "/data/tts") -> None:
        self.model_name = model_name
        self.out_dir = Path(out_dir)
        self._tts: Optional[TTS] = None
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        if self._tts is None:
            if TTS is None:
                raise RuntimeError("Coqui TTS is not available in the environment")
            logger.info(f"Loading Coqui TTS model: {self.model_name}")
            self._tts = TTS(self.model_name)

    def synthesize_to_file(self, text: str, filename: str = "out.wav") -> Path:
        self.load()
        assert self._tts is not None
        out_path = self.out_dir / filename
        logger.debug(f"Synthesizing TTS to {out_path}")
        self._tts.tts_to_file(text=text, file_path=str(out_path))
        return out_path
