"""段階的な再実行性(NFR-4)。

各ステージの成果物を `out/<deck>/<stage>/<inputs_digest>` に置くコンテンツアドレス方式の
キャッシュ。同じ入力の2回目は外部呼び出しをせずに成果物を返すので、途中の段階から
再実行しても前段の LLM / TTS 呼び出しは 0 回になる。

`inputs_digest` は **前段成果物の内容 + プロンプトテンプレートの digest + モデル ID +
生成パラメータ** から作り、ステージ実装コードは入れない(AR-21)。プロンプトを入れないと
直したのに旧成果物が返り、実装コードを入れると無関係な編集で全ステージが再実行される。

実行の列(`--fresh-run` / `--resume`)の実行は `run_id` も digest に入れる(D-137)。列ごとに成果物の名前が
分かれ、その列が作った成果物にだけヒットし、列より前の成果物や他の列の成果物にはヒットしない。
通常の実行(`run_id` なし)の digest は変わらない。

成果物は一時パスへ書いてから `os.replace` で digest 名に移す。途中で落ちた実行の書きかけが
digest 名で残ると、次の実行がそれをキャッシュヒットとして読んでしまう。
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from quiz_sift.domain.ids import DeckId

type Param = str | int | float | bool


@dataclass(frozen=True, slots=True)
class Stage:
    """ステージ1つの、成果物を左右する設定。外部を呼ばないステージは `prompt_template` と
    `model` を空にする。"""

    name: str
    prompt_template: str = ""
    model: str = ""
    params: Mapping[str, Param] = field(default_factory=dict)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inputs_digest(stage: Stage, upstream: bytes, run_id: str | None = None) -> str:
    """`upstream`(前段成果物の内容)と `stage` の設定から、成果物の名前になる digest を作る。

    `run_id` は実行の列の ID で、`None`(通常の実行)なら digest の構成要素に入らない。
    """
    material: dict[str, object] = {
        "upstream": _sha256(upstream),
        "prompt_template": _sha256(stage.prompt_template.encode("utf-8")),
        "model": stage.model,
        "params": dict(stage.params),
    }
    if run_id is not None:
        material["run_id"] = run_id
    return _sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8"))


class StageCache:
    """1デッキ分の成果物キャッシュ。`root` は `out/` にあたる。`run_id` は実行の列の ID(通常の実行は `None`)。"""

    def __init__(self, root: Path, deck: DeckId, run_id: str | None = None) -> None:
        self._dir = root / deck
        self._run_id = run_id

    def run(self, stage: Stage, upstream: bytes, produce: Callable[[BinaryIO], None]) -> Path:
        """成果物のパスを返す。キャッシュに無いときだけ `produce` に書かせる。

        `produce` が例外で抜けたときは書きかけを消して例外をそのまま伝える。プロセスごと
        落ちた場合に残る一時ファイルは digest 名を持たないので、ヒットとして読まれない。
        """
        stage_dir = self._dir / stage.name
        target = stage_dir / inputs_digest(stage, upstream, self._run_id)
        if target.is_file():
            return target
        stage_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=stage_dir, prefix=".", suffix=".partial")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                produce(out)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return target
