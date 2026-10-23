"""取り込みテストの共通部品。`data/<deck_id>/` を tmp_path に組み立てる。"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from quiz_sift.domain.ids import DeckId

FIXTURES = Path(__file__).parent / "fixtures"

DECK = DeckId("aws-saa")

type WriteDeck = Callable[..., Path]


def question_row(raw_id: str, **overrides: Any) -> dict[str, Any]:
    """妥当な1問。`overrides` で壊したいキーだけ差し替える。値 `...` はキーを消す。"""
    row: dict[str, Any] = {
        "id": raw_id,
        "stem": f"問題 {raw_id} の問題文",
        "options": [{"id": label, "text": f"{raw_id} の選択肢 {label}"} for label in "ABCD"],
        "answers": ["B"],
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not ...}


@pytest.fixture
def write_deck(tmp_path: Path) -> WriteDeck:
    """manifest と JSONL を書いて data_root を返す。`files` は ファイル名 → 行(dict か生の文字列)。"""

    def write(files: Mapping[str, Sequence[Mapping[str, Any] | str]], default_studied: str | None = None) -> Path:
        deck_dir = tmp_path / DECK
        deck_dir.mkdir(parents=True, exist_ok=True)
        manifest = f"deck_id: {DECK}\ntitle: AWS SAA 模試\nlanguage: ja\n"
        if default_studied is not None:
            manifest += f"default_studied: {default_studied}\n"
        (deck_dir / "deck.yaml").write_text(manifest, encoding="utf-8")
        for name, rows in files.items():
            lines = [row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) for row in rows]
            (deck_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return tmp_path

    return write
