"""グロッサリの合成フィクスチャ(R-5)。合成なので NFR-2 の対象外でコミットできる。"""

import pytest

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag

DECK = DeckId("aws-saa")

ROWS = (
    (
        "q01",
        "アクセス頻度は低いが即座に取り出す必要があるデータに適した S3 のストレージクラスはどれか。",
        ("S3 標準", "S3 標準-IA", "S3 Glacier Deep Archive", "S3 One Zone-IA"),
        "S3 標準-IA はアクセス頻度が低いデータ向けのストレージクラスで、ミリ秒単位で取り出せる。",
    ),
    (
        "q02",
        "EC2 インスタンスに S3 へのアクセス権限を与える方法はどれか。",
        ("アクセスキーを埋め込む", "IAM ロールを付与する", "バケットを公開する", "何もしない"),
        "IAM ロールをインスタンスプロファイルとして EC2 に付与すると、一時的な認証情報が使われる。",
    ),
)


def _deck(with_explanations: bool) -> Deck:
    questions = []
    for local_id, stem, texts, explanation in ROWS:
        qid = QuestionId(DECK, local_id)
        options = tuple(Option(OptionId(qid, label), text) for label, text in zip("ABCD", texts, strict=True))
        questions.append(
            Question(
                qid,
                stem,
                options,
                (options[1].option_id,),
                explanation if with_explanations else None,
                StudiedFlag.UNKNOWN,
            )
        )
    return Deck(DECK, Language.JA, None, tuple(questions))


@pytest.fixture
def deck() -> Deck:
    return _deck(with_explanations=True)


@pytest.fixture
def deck_without_explanations() -> Deck:
    return _deck(with_explanations=False)
