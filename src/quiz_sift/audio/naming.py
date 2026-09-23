"""連番命名(R-7)。ファイル名の昇順ソートが意図した再生順になるようにする。

端末の音楽アプリはファイル名昇順で並べる。連番をゼロ埋めしないと `10_` が `2_` の前に来る。
出力ディレクトリ・連番・識別子を引数に取り、クラスタ音声(`out/<deck>/audio/`)以外の系列
(グロッサリ・復習 — D-049 / AR-24)も同じ関数で出せるようにする。
"""

from pathlib import Path
from string import ascii_lowercase

SEQUENCE_WIDTH = 3
"""連番の桁数。これを超える連番はソート順が壊れるので作らない。"""


def part_suffix(part: int, parts: int) -> str:
    """1系列内の通し番号 `part`(0 始まり)に付ける記号。1本だけなら付けない(`012_`、割れば `012a_`)。"""
    if not 0 <= part < parts:
        raise ValueError(f"part={part} が 0..{parts - 1} の範囲外")
    if parts == 1:
        return ""
    if parts > len(ascii_lowercase):
        raise ValueError(f"パート数 {parts} は記号 a-z で表せない")
    return ascii_lowercase[part]


def track_path(out_dir: Path, seq: int, identifier: str, *, suffix: str = "", extension: str = "mp3") -> Path:
    """`out_dir/<ゼロ埋め連番><suffix>_<identifier>.<extension>`。"""
    if not 0 <= seq < 10**SEQUENCE_WIDTH:
        raise ValueError(f"連番 {seq} は {SEQUENCE_WIDTH} 桁のゼロ埋めに収まらない")
    if not identifier or "/" in identifier:
        raise ValueError(f"識別子がファイル名に使えない: {identifier!r}")
    return out_dir / f"{seq:0{SEQUENCE_WIDTH}d}{suffix}_{identifier}.{extension}"
