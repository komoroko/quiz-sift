"""デッキの所在(R-11 のディレクトリ分離)。"""

from pathlib import Path

import pytest

from quiz_sift.decks.manifest import ManifestError
from quiz_sift.decks.registry import (
    deck_data_dir,
    deck_out_dir,
    discover_decks,
    load_deck_manifest,
    manifest_path,
)
from quiz_sift.domain.ids import DeckId


def write_deck(data_root: Path, directory: str, deck_id: str, title: str = "T") -> None:
    path = data_root / directory
    path.mkdir(parents=True)
    (path / "deck.yaml").write_text(f"deck_id: {deck_id}\ntitle: {title}\nlanguage: ja\n", encoding="utf-8")


def test_directories_are_separated_per_deck(tmp_path: Path) -> None:
    deck_a, deck_b = DeckId("deck-a"), DeckId("deck-b")
    assert deck_data_dir(tmp_path / "data", deck_a) != deck_data_dir(tmp_path / "data", deck_b)
    assert deck_out_dir(tmp_path / "out", deck_a) == tmp_path / "out" / "deck-a"
    assert manifest_path(tmp_path / "data", deck_a) == tmp_path / "data" / "deck-a" / "deck.yaml"


def test_a_manifest_that_names_another_deck_is_refused(tmp_path: Path) -> None:
    """食い違ったまま通すと、data/a/ の問題が out/b/ へ書かれる。"""
    write_deck(tmp_path, "deck-a", "deck-b")
    with pytest.raises(ManifestError, match="食い違う"):
        load_deck_manifest(tmp_path, DeckId("deck-a"))


def test_discover_lists_decks_in_a_deterministic_order(tmp_path: Path) -> None:
    write_deck(tmp_path, "deck-b", "deck-b")
    write_deck(tmp_path, "deck-a", "deck-a")
    (tmp_path / "not-a-deck").mkdir()
    assert [manifest.deck_id for manifest in discover_decks(tmp_path)] == ["deck-a", "deck-b"]


def test_discover_on_a_missing_root_is_empty(tmp_path: Path) -> None:
    assert discover_decks(tmp_path / "absent") == []
