"""1デッキ分の取り込み(R-1)。`data/<deck_id>/deck.yaml` と `*.jsonl` を読み、検証と重複検出を通す。"""

from dataclasses import dataclass
from pathlib import Path

from quiz_sift.decks.registry import deck_data_dir, load_deck_manifest
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.report import IngestOk, IngestReport
from quiz_sift.ingest.dedup import DedupReport, find_duplicates
from quiz_sift.ingest.reader import read_jsonl
from quiz_sift.ingest.validate import build_report, validate


@dataclass(frozen=True, slots=True, repr=False)
class Ingested:
    """取り込みの結果。"""

    deck: Deck
    report: IngestReport
    duplicates: DedupReport


def ingest_deck(data_root: Path, deck_id: DeckId) -> Ingested:
    """デッキの全 `*.jsonl` をファイル名順に読む。問題 ID の重複はファイルをまたいで判定する。"""
    manifest = load_deck_manifest(data_root, deck_id)
    records = [
        record for path in sorted(deck_data_dir(data_root, deck_id).glob("*.jsonl")) for record in read_jsonl(path)
    ]
    outcomes = validate(records, manifest.deck_id, manifest.default_studied)
    deck = Deck(
        deck_id=manifest.deck_id,
        language=manifest.language,
        default_studied=manifest.default_studied,
        questions=tuple(outcome.question for outcome in outcomes if isinstance(outcome, IngestOk)),
    )
    return Ingested(deck=deck, report=build_report(outcomes), duplicates=find_duplicates(deck))
