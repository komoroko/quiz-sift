"""JSONL の読み取り(R-1 / ADR-011)。

1行1問。スキーマは次のとおりで、`explanation` と `studied` 以外は必須:

```json
{"id": "q001", "stem": "問題文", "options": [{"id": "A", "text": "選択肢"}, ...],
 "answers": ["A"], "explanation": "公式解説", "studied": "studied"}
```

`answers` は正解の選択肢 ID の配列。正解を単一の値で持つ旧キー `answer` は未知のキーとして弾き、
読み替えない(ADR-020)。

ここで弾くのは**形そのものが壊れた行**(JSON でない、必須キーが無い、型が違う、未知のキー)で、
例外で取り込み全体を止める。黙って読み飛ばすと保存則の `input_total` から消え、壊れた行が
あったこと自体が見えなくなる。選択肢数・正解・ID 重複は形としては読めるので、ここでは
通して `validate.py` が理由つきで個別に除外する。

エラーメッセージには行番号とキー名だけを載せ、教材本文は載せない(NFR-2)。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiz_sift.domain.studied import StudiedFlag

STUDIED_VALUES = {
    "studied": StudiedFlag.STUDIED,
    "unseen": StudiedFlag.UNSEEN,
}
"""`studied` に書ける値。`unknown` は書けない — 未指定がそれを意味する(manifest と同じ)。"""

_REQUIRED_KEYS = frozenset({"id", "stem", "options", "answers"})
_KEYS = _REQUIRED_KEYS | {"explanation", "studied"}
_OPTION_KEYS = frozenset({"id", "text"})


class IngestFormatError(ValueError):
    """入力ファイルの行が規定の形をしていない。"""


@dataclass(frozen=True, slots=True, repr=False)
class RawOption:
    """入力上の選択肢1件。"""

    option_id: str
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class RawRecord:
    """入力1行を型だけ揃えたもの。選択肢数や正解の妥当性はまだ見ていない。"""

    raw_id: str
    stem: str
    options: tuple[RawOption, ...]
    answers: tuple[str, ...]
    """正解の選択肢 ID。入力の並びのまま。空・重複・未知 ID は `validate.py` が `ANSWER_UNRESOLVED` にする。"""
    explanation: str | None
    studied: StudiedFlag | None
    """個別の既習指定。未指定は `None`(`resolve_studied` の第1引数になる)。"""


def parse_jsonl(text: str, source: str) -> list[RawRecord]:
    """JSONL 文字列を行ごとの `RawRecord` にする。空行は入力件数に数えない。"""
    records = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(_parse_line(line))
        except IngestFormatError as exc:
            raise IngestFormatError(f"{source}:{line_no}: {exc}") from None
    return records


def read_jsonl(path: Path) -> list[RawRecord]:
    """JSONL ファイルを読んで `RawRecord` の列にする。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IngestFormatError(f"入力を読めない: {path}: {type(exc).__name__}") from exc
    return parse_jsonl(text, str(path))


def _parse_line(line: str) -> RawRecord:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        # exc の文字列は該当箇所の本文を含まないが、位置だけ出せば足りる。
        raise IngestFormatError(f"JSON として読めない(列 {exc.colno})") from None
    if not isinstance(raw, dict):
        raise IngestFormatError(f"1行はオブジェクトでなければならない: {type(raw).__name__}")
    row: dict[str, Any] = raw

    unknown = sorted(key for key in row if key not in _KEYS)
    if unknown:
        raise IngestFormatError(f"未知のキー: {unknown}(綴り間違いを黙って無視しない)")
    missing = sorted(_REQUIRED_KEYS - row.keys())
    if missing:
        raise IngestFormatError(f"必須のキーが無い: {missing}")

    options_value = row["options"]
    if not isinstance(options_value, list):
        raise IngestFormatError("options は配列でなければならない")
    options = tuple(_parse_option(value, index) for index, value in enumerate(options_value))
    option_ids = [option.option_id for option in options]
    if len(set(option_ids)) != len(option_ids):
        raise IngestFormatError("options の id が重複している")

    answers_value = row["answers"]
    if not isinstance(answers_value, list) or not all(isinstance(value, str) for value in answers_value):
        raise IngestFormatError("answers は文字列の配列でなければならない")

    return RawRecord(
        raw_id=_require_text(row, "id"),
        stem=_require_text(row, "stem"),
        options=options,
        answers=tuple(value.strip() for value in answers_value),
        explanation=_optional_text(row, "explanation"),
        studied=_parse_studied(row.get("studied")),
    )


def _parse_option(value: object, index: int) -> RawOption:
    if not isinstance(value, dict):
        raise IngestFormatError(f"options[{index}] はオブジェクトでなければならない")
    option: dict[str, Any] = value
    if option.keys() != _OPTION_KEYS:
        raise IngestFormatError(f"options[{index}] のキーは {sorted(_OPTION_KEYS)} でなければならない")
    return RawOption(
        option_id=_require_text(option, "id", f"options[{index}].id"),
        text=_require_text(option, "text", f"options[{index}].text"),
    )


def _parse_studied(value: object) -> StudiedFlag | None:
    if value is None:
        return None
    if isinstance(value, str) and value in STUDIED_VALUES:
        return STUDIED_VALUES[value]
    raise IngestFormatError(f"studied は {sorted(STUDIED_VALUES)} のいずれか、または未指定でなければならない")


def _require_text(row: dict[str, Any], key: str, label: str | None = None) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IngestFormatError(f"{label or key} は空でない文字列でなければならない")
    return value.strip()


def _optional_text(row: dict[str, Any], key: str) -> str | None:
    """空白だけの解説は「解説が無い」と同じに扱う。解説ありの件数を水増ししない。"""
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise IngestFormatError(f"{key} は文字列または null でなければならない")
    return value.strip() or None
