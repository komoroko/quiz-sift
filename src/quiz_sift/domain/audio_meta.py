"""音声の sidecar メタデータ(R-7)。"""

from dataclasses import dataclass

from quiz_sift.domain.language import Language


@dataclass(frozen=True, slots=True)
class AudioMeta:
    """生成した音声1ファイルに添える情報。

    言語とボイスの一致は、これを sidecar JSON に書いたうえでコード内の定数表と
    突き合わせて判定する。1ファイル内に日英が混在しても「使用したボイス」は
    単数のままなので(D-060)、`language` も単数でよい。
    """

    language: Language
    voice_id: str

    def __post_init__(self) -> None:
        if not self.voice_id:
            raise ValueError("voice_id が空")
