"""選択肢ラベルの列挙判定(C-006 / R-6 / A-2)。"""

from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt, ScriptSegment
from quiz_sift.script.generate import LabelEnumerationFound, generate_scripts
from quiz_sift.script.label_guard import find_enumerations

from .conftest import GOOD, StubChecker, Writer, draft, recall_set


def _track(prompt: str, answer: str, discrimination: str) -> list[ScriptSegment]:
    return [Prompt(prompt), Pause(3.3), Answer(answer), Discrimination(discrimination)]


def _series(segments: list[ScriptSegment], language: Language) -> set[str]:
    return {e.series for e in find_enumerations(segments, language)}


def test_cross_segment_enumeration_goes_red(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    # セグメントを跨いで4件が並ぶ形。どのセグメントも単独では列挙を含まない。
    crossing = {
        "kana": (
            Language.JA,
            _track("アの場合とイの場合を思い出してください", "ウが正解です", "エは別のサービスです"),
        ),
        "letter": (Language.JA, _track("AとBを比べてください", "Cが正解です", "Dは別のサービスです")),
        "number": (Language.JA, _track("1番と2番を比べてください", "③が正解です", "(4)は別のサービスです")),
        "en-chain": (Language.EN, _track("Compare options A and B", "C", "or D")),
        "en-markers": (Language.EN, _track("A) S3 B) EBS", "C) EFS is right", "D) is not durable")),
    }
    for name, (language, segments) in crossing.items():
        assert _series(segments, language), name
        for segment in segments:
            assert not find_enumerations([segment], language), (name, segment)
    assert _series(crossing["kana"][1], Language.JA) == {"kana"}
    assert _series(crossing["letter"][1], Language.JA) == {"letter"}
    assert _series(crossing["number"][1], Language.JA) == {"number"}

    # 1セグメント内の列挙も同じ単位の部分集合として落ちる。
    assert _series([Answer("A, B, C, D のうち C が正解")], Language.JA) == {"letter"}
    assert _series([Answer("The answer is among A, B, C, and D.")], Language.EN) == {"letter"}
    assert _series([Answer("Pick 1, 2, 3 or 4")], Language.EN) == {"number"}

    # 正当な出現は落とさない: 英語の冠詞 a、Availability Zone A、単独ラベル、語の一部としての記号・数字。
    legitimate = {
        Language.EN: [
            _track(
                "A company needs a database in Availability Zone A and a standby in Availability Zone B.",
                "Use a Multi-AZ deployment with a standby in Availability Zone C.",
                "A read replica is not a standby; it serves reads, and EC2 or S3 or IAM does not fail over.",
            ),
            _track("Which is a valid choice for a 3-tier app?", "Answer A", "It takes 1 to 2 minutes, not 4."),
        ],
        Language.JA: [
            _track(
                "アクセス頻度、即座、ストレージクラス。この組み合わせで選ぶものは?",
                "S3 標準-IA",
                "マルチ AZ 配置は EC2 や S3 と違い、1つの AZ 障害でもイベントを止めない",
            ),
            _track("A の場合を思い出してください", "アが正解", "1番は別"),
        ],
    }
    for language, tracks in legitimate.items():
        for segments in tracks:
            assert not find_enumerations(segments, language), segments

    # 生成器も同じ単位で判定し、列挙を含むトラックがあれば含意判定より前に、何も書かずに非0で止める。
    enumerating = {
        **GOOD,
        "q03": draft("q03", ("アの場合", "イの場合"), "ウが正解", "エは別の結果になる"),
    }
    checker = StubChecker()
    out = tmp_path / "script.tsv"
    with pytest.raises(LabelEnumerationFound) as exc:
        generate_scripts(checker, deck, clusters, recall_set(enumerating), Writer(out))
    assert exc.value.code
    assert [(t.cluster_id.local_id, t.part) for t in exc.value.tracks] == [("c002", 0)]
    assert checker.checked == []
    assert not out.exists()
