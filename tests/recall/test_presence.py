"""想起キーの語句と、識別ポイントが参照する誤答選択肢・答えの選択肢 ID の検査(C-004 / R-4)。"""

from dataclasses import replace

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.recall.verify import answer_mismatch, missing_key_terms, missing_options

from .conftest import GOOD, option, question


def test_recall_key_terms_exist_in_the_stem(deck: Deck) -> None:
    for qid, draft in GOOD.items():
        assert missing_key_terms(draft.key, question(deck, qid)) == (), qid
    q01 = question(deck, "q01")
    # 元の問題に無い語。別クラスタの問題にある語(IAM)も、この問題に無ければ実在しない。
    assert missing_key_terms(RecallKey(("ストレージクラス", "Lambda")), q01) == ("Lambda",)
    assert missing_key_terms(RecallKey(("IAM", "アクセス頻度")), q01) == ("IAM",)
    # 英数字の語は語境界で照合する。「S3」は「S30」に実在しない。
    assert missing_key_terms(RecallKey(("S30",)), q01) == ("S30",)
    # 選択肢(Glacier)と解説(ミリ秒)にだけある語は、問題文に無いので実在しない。
    assert missing_key_terms(RecallKey(("Glacier", "ミリ秒", "S3")), q01) == ("Glacier", "ミリ秒")
    # 照合先を渡せば、その部分を見る。
    assert missing_key_terms(RecallKey(("Glacier", "ミリ秒")), q01, ("stem", "options", "explanation")) == ()


def test_answers_must_match_the_correct_set_by_id(deck: Deck) -> None:
    q01 = question(deck, "q01")
    good = GOOD["q01"]
    assert not answer_mismatch(good, q01)
    (correct,) = good.answers
    wrong = AnswerElement(option("q01", "C"), "S3 Glacier Deep Archive")
    other_question = AnswerElement(option("q02", "B"), "S3 標準-IA")
    for answers in ((), (correct, correct), (correct, wrong), (wrong,), (other_question,)):
        assert answer_mismatch(replace(good, answers=answers), q01)


def test_discrimination_references_distractors_by_id(deck: Deck) -> None:
    q01 = question(deck, "q01")
    good = GOOD["q01"]
    for qid, draft in GOOD.items():
        assert not missing_options(draft, question(deck, qid)), qid
    # 本文の括弧は参照とみなさない: どの選択肢にも無い語句を括っても、ID が誤答なら通る。
    quoted = DiscriminationPoint(
        "即座に取り出すなら標準-IA で、「S3 Intelligent-Tiering」は自動で階層を移す", (option("q01", "C"),)
    )
    assert not missing_options(replace(good, discrimination=quoted), q01)
    # ID が無い・正解を指す・別の問題の選択肢を指す参照は落ちる。
    for ids in ((), (option("q01", "B"),), (option("q01", "C"), option("q01", "B")), (option("q02", "C"),)):
        point = DiscriminationPoint(good.discrimination.text, ids)
        assert missing_options(replace(good, discrimination=point), q01), ids
