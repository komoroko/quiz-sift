"""CLI の成果物の読み取り専用配信(R-12)。

`out/` は Next.js の `public/` の外にあるので、API が配信エンドポイントを1本だけ持つ。配信できるのは
`out/` の下にある実ファイルで、しかも `gitignore_guard` が書き出し先として認めるものだけ。
`..` やシンボリックリンクで `out/` の外へ出る経路と、追跡対象になりうるファイルは配信しない。
学習記録ファイルは全デッキ分を1ファイルに持つので、API を迂回して丸ごと読ませない。
"""

from pathlib import Path

from quiz_sift.safety.gitignore_guard import NotIgnoredError, ensure_ignored
from quiz_sift.store.location import store_path


class NotServableError(LookupError):
    """配信対象ではない。理由は問わず 404 にする(存在の有無を外へ漏らさない)。"""


def resolve_artifact(root: Path, relative: str) -> Path:
    """`out/` からの相対パス `relative` を、配信してよい実ファイルの絶対パスにする。"""
    out_root = (root / "out").resolve()
    candidate = (out_root / relative).resolve()
    if not candidate.is_relative_to(out_root) or not candidate.is_file():
        raise NotServableError(relative)
    store = store_path(root).resolve()
    if candidate.parent == store.parent and candidate.name.startswith(store.name):
        raise NotServableError(relative)
    try:
        return ensure_ignored(candidate, root)
    except NotIgnoredError as exc:
        raise NotServableError(relative) from exc
