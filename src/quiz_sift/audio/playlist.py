"""m3u 再生リスト(R-7)。

エントリは再生リストと同じディレクトリのファイル名で書く(相対パス)。端末へはディレクトリごと
転送するので(D-016)、ホストの絶対パスを書くと端末で解決できない。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    path: Path
    seconds: float


def write_playlist(out_dir: Path, name: str, entries: Sequence[PlaylistEntry]) -> Path:
    """`out_dir/<name>.m3u` に拡張 m3u を書く。エントリはすべて `out_dir` 直下のファイルでなければならない。"""
    outside = [e.path for e in entries if e.path.parent != out_dir]
    if outside:
        raise ValueError(f"再生リストと別のディレクトリにあるファイル: {outside}")
    lines = ["#EXTM3U"]
    for entry in entries:
        lines.append(f"#EXTINF:{round(entry.seconds)},{entry.path.stem}")
        lines.append(entry.path.name)
    path = out_dir / f"{name}.m3u"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
