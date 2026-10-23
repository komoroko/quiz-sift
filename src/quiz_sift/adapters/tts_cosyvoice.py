"""`ports.tts.Tts` の CosyVoice 実装(ADR-004)。

CosyVoice は pip で入らず(git clone + サブモジュール + torch)、サンドボックスイメージにも入れない
(ADR-012)。そのため静的には import せず、構築時に `importlib` で読み込む。ホストに導入された
リポジトリを `PYTHONPATH` に通して使う。`voice_id` は CosyVoice-300M-SFT の組み込み話者名
(`audio.voices.VOICE_TABLE`)。

日英混在のテキストは分割せずそのまま渡す。モデルが文内で言語を切り替える(D-060)。
"""

import importlib
from typing import Any

from quiz_sift.ports.tts import PcmAudio

MODEL_MODULE = "cosyvoice.cli.cosyvoice"


class CosyVoiceTts:
    def __init__(self, model_dir: str) -> None:
        self._torch: Any = importlib.import_module("torch")
        module: Any = importlib.import_module(MODEL_MODULE)
        self._model: Any = module.CosyVoice(model_dir)
        self._sample_rate = int(self._model.sample_rate)

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        # 出力は float の [-1, 1] で shape (1, n) のテンソルが、文単位に分かれて返る。
        chunks = [out["tts_speech"] for out in self._model.inference_sft(text, voice_id, stream=False)]
        samples = b"".join(
            bytes(chunk.clamp(-1.0, 1.0).mul(32767).to(self._torch.int16).numpy().astype("<i2").tobytes())
            for chunk in chunks
        )
        return PcmAudio(samples, self._sample_rate)
