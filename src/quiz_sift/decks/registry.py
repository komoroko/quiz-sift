"""デッキの所在(R-11)。

`data/<deck_id>/` と `out/<deck_id>/` の組み立てをここ1箇所に閉じる。各所で
`root / deck_id` を書くと、`deck_id` の検証を通っていない文字列からパスが
作られる経路が増える。パスは `DeckManifest.deck_id` からしか作らない。
"""

from collections.abc import Iterator
from pathlib import Path

from quiz_sift.decks.manifest import MANIFEST_FILENAME, DeckManifest, ManifestError, load_manifest
from quiz_sift.domain.ids import DeckId


def deck_data_dir(data_root: Path, deck_id: DeckId) -> Path:
    """そのデッキの入力が置かれるディレクトリ。"""
    return data_root / deck_id


def deck_out_dir(out_root: Path, deck_id: DeckId) -> Path:
    """そのデッキの成果物を書き出すディレクトリ。"""
    return out_root / deck_id


def manifest_path(data_root: Path, deck_id: DeckId) -> Path:
    """そのデッキの `deck.yaml` の位置。"""
    return deck_data_dir(data_root, deck_id) / MANIFEST_FILENAME


def load_deck_manifest(data_root: Path, deck_id: DeckId) -> DeckManifest:
    """ディレクトリ名からデッキを引く。

    manifest の `deck_id` がディレクトリ名と食い違っていたらエラーにする。
    食い違ったまま通すと、`data/a/` の問題が `out/b/` へ書かれる。
    """
    manifest = load_manifest(manifest_path(data_root, deck_id))
    if manifest.deck_id != deck_id:
        raise ManifestError(
            f"deck_id がディレクトリ名と食い違う: ディレクトリ={deck_id!r} manifest={manifest.deck_id!r}"
        )
    return manifest


def discover_decks(data_root: Path) -> list[DeckManifest]:
    """`data_root` 直下の manifest を持つディレクトリを `deck_id` 昇順で列挙する。"""
    return sorted(_iter_decks(data_root), key=lambda manifest: manifest.deck_id)


def _iter_decks(data_root: Path) -> Iterator[DeckManifest]:
    if not data_root.is_dir():
        return
    for entry in sorted(data_root.iterdir()):
        if (entry / MANIFEST_FILENAME).is_file():
            yield load_deck_manifest(data_root, DeckId(entry.name))
