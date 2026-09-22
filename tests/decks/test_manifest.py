"""デッキ manifest の読み取り(受入基準 A-2)。"""

import re
from pathlib import Path

import pytest

from quiz_sift.decks.manifest import DeckManifest, ManifestError, load_manifest, parse_manifest
from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.language import Language
from quiz_sift.domain.studied import StudiedFlag

VALID = """
deck_id: aws-saa
title: AWS SAA 模試
language: ja
default_studied: unseen
"""


def test_manifest_rejects_unknown_language() -> None:
    """受入基準 A-2: deck_id / title / language / default_studied を読み、language は ja|en のみ。"""
    manifest = parse_manifest(VALID)
    assert manifest == DeckManifest(
        deck_id=DeckId("aws-saa"),
        title="AWS SAA 模試",
        language=Language.JA,
        default_studied=StudiedFlag.UNSEEN,
    )

    # en も通る。
    assert parse_manifest("deck_id: d\ntitle: T\nlanguage: en\n").language is Language.EN

    # ja|en 以外はエラー。既定へ倒さない。
    for bad in ("fr", "JA", "japanese", "ja-JP", ""):
        with pytest.raises(ManifestError, match="language"):
            parse_manifest(f"deck_id: d\ntitle: T\nlanguage: {bad!r}\n")

    # language の欠落もエラー。
    with pytest.raises(ManifestError, match="language"):
        parse_manifest("deck_id: d\ntitle: T\n")


def test_default_studied_is_optional_but_not_free_form() -> None:
    assert parse_manifest("deck_id: d\ntitle: T\nlanguage: ja\n").default_studied is None
    assert parse_manifest("deck_id: d\ntitle: T\nlanguage: ja\ndefault_studied:\n").default_studied is None
    assert (
        parse_manifest("deck_id: d\ntitle: T\nlanguage: ja\ndefault_studied: studied\n").default_studied
        is StudiedFlag.STUDIED
    )
    # unknown は書けない。未指定がそれを意味する。
    for bad in ("unknown", "yes", "true"):
        with pytest.raises(ManifestError, match="default_studied"):
            parse_manifest(f"deck_id: d\ntitle: T\nlanguage: ja\ndefault_studied: {bad!r}\n")


def test_a_misspelled_key_is_an_error_not_a_silent_default() -> None:
    """人が手で書くファイルなので、綴り間違いを黙って無視すると誰も気付けない。"""
    with pytest.raises(ManifestError, match="未知のキー"):
        parse_manifest("deck_id: d\ntitle: T\nlanguage: ja\ndefault_study: studied\n")


def test_deck_id_must_be_a_safe_directory_name() -> None:
    """deck_id は data/<deck_id>/ と out/<deck_id>/ の名前になる(D-059)。"""
    for bad in ("../escape", "a/b", "Deck", "-lead", ""):
        with pytest.raises(ManifestError, match="deck_id"):
            parse_manifest(f"deck_id: {bad!r}\ntitle: T\nlanguage: ja\n")


def test_malformed_documents_are_rejected() -> None:
    with pytest.raises(ManifestError, match="マッピング"):
        parse_manifest("- a\n- b\n")
    with pytest.raises(ManifestError, match="マッピング"):
        parse_manifest("")
    with pytest.raises(ManifestError, match="YAML"):
        parse_manifest("deck_id: [unclosed\n")
    with pytest.raises(ManifestError, match="title"):
        parse_manifest("deck_id: d\ntitle: '  '\nlanguage: ja\n")


def test_load_manifest_names_the_file_it_failed_on(tmp_path: Path) -> None:
    path = tmp_path / "deck.yaml"
    path.write_text("deck_id: d\ntitle: T\nlanguage: fr\n", encoding="utf-8")
    with pytest.raises(ManifestError, match=re.escape(str(path))):
        load_manifest(path)

    with pytest.raises(ManifestError, match="読めない"):
        load_manifest(tmp_path / "missing.yaml")
