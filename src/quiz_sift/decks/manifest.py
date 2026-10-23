"""デッキ manifest `data/<deck_id>/deck.yaml` の読み取り(R-11 / ADR-011)。

人が手で書くファイルなので、黙って既定値で埋めない。`language` の綴り間違いや
`default_studied` のキー名の打ち間違いは**エラーにする** — 無視して既定へ倒すと、
デッキ単位の既習指定が効いていないことに誰も気付けない。
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# PyYAML は型スタブを持たないので `pyproject.toml` の override で import を許している(AR-23)。
# 返り値は Any になるので、下で isinstance により必ず絞り込む。
import yaml

from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.language import Language
from quiz_sift.domain.studied import StudiedFlag

MANIFEST_FILENAME = "deck.yaml"

DECK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
"""`deck_id` はディレクトリ名 `data/<deck_id>/` と `out/<deck_id>/` になる。

区切り文字や `..` を含む値を通すと、書き出し先が意図した木の外へ出る。
`deck_id` は不変として扱う(D-059)ので、ここで一度厳しく見ておけばよい。
"""

DEFAULT_STUDIED_VALUES = {
    "studied": StudiedFlag.STUDIED,
    "unseen": StudiedFlag.UNSEEN,
}
"""`default_studied` に書ける値。`unknown` は書けない — 未指定がそれを意味する。"""

_KEYS = frozenset({"deck_id", "title", "language", "default_studied"})


class ManifestError(ValueError):
    """manifest が読めない、または値が規定外。"""


@dataclass(frozen=True, slots=True)
class DeckManifest:
    """`deck.yaml` の内容。"""

    deck_id: DeckId
    title: str
    language: Language
    default_studied: StudiedFlag | None
    """未指定は `None`。`resolve_studied` の第2引数になる。"""


def parse_manifest(text: str) -> DeckManifest:
    """YAML 文字列を `DeckManifest` にする。"""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"YAML として読めない: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(f"最上位はマッピングでなければならない: {type(raw).__name__}")
    document: dict[Any, Any] = raw

    unknown = sorted(str(key) for key in document if key not in _KEYS)
    if unknown:
        raise ManifestError(f"未知のキー: {unknown}(綴り間違いを黙って無視しない)")

    deck_id = _require_text(document, "deck_id")
    if not DECK_ID_PATTERN.match(deck_id):
        raise ManifestError(f"deck_id が規定の形式でない: {deck_id!r}(小文字英数と - _ のみ)")

    language_value = _require_text(document, "language")
    try:
        language = Language(language_value)
    except ValueError as exc:
        allowed = sorted(member.value for member in Language)
        raise ManifestError(f"language は {allowed} のいずれかでなければならない: {language_value!r}") from exc

    default_studied_value = document.get("default_studied")
    if default_studied_value is None:
        default_studied = None
    elif isinstance(default_studied_value, str) and default_studied_value in DEFAULT_STUDIED_VALUES:
        default_studied = DEFAULT_STUDIED_VALUES[default_studied_value]
    else:
        allowed = sorted(DEFAULT_STUDIED_VALUES)
        raise ManifestError(
            f"default_studied は {allowed} のいずれか、または未指定でなければならない: {default_studied_value!r}"
        )

    return DeckManifest(
        deck_id=DeckId(deck_id),
        title=_require_text(document, "title"),
        language=language,
        default_studied=default_studied,
    )


def load_manifest(path: Path) -> DeckManifest:
    """`deck.yaml` を読んで `DeckManifest` にする。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"manifest を読めない: {path}: {exc}") from exc
    try:
        return parse_manifest(text)
    except ManifestError as exc:
        raise ManifestError(f"{path}: {exc}") from exc


def _require_text(document: dict[Any, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} は空でない文字列でなければならない: {value!r}")
    return value.strip()
