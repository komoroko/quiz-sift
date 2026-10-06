"""3分を超えた台本の分割(C-006 / R-6 / A-4 / A-5)。

日本語は 7.0 文字/秒なので、`"あ" * 7 * n` がちょうど n 秒になる。
"""

from pathlib import Path

import pytest

from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Pause, Prompt
from quiz_sift.script.duration import estimate_seconds
from quiz_sift.script.generate import ScriptTooLong, generate_scripts
from quiz_sift.script.segments import QuestionScript, Track, parse_questions
from quiz_sift.script.split import TRACK_LIMIT_SECONDS, TrackPlan, plan_tracks

from .conftest import DECK, GOOD, StubChecker, Writer, answered, discriminated, qscript, recall_set


def _text(seconds: int) -> str:
    return "あ" * 7 * seconds


def _assert_whole(plan: TrackPlan, clusters: list[tuple[ClusterId, list[QuestionScript]]]) -> None:
    """どのパートも Prompt で始まり Pause を含み、1問分の文法に合い、入力のセグメントを順序どおりすべて持つ。"""
    for track in plan.tracks:
        assert isinstance(track.segments[0], Prompt)
        assert any(isinstance(s, Pause) for s in track.segments)
        assert len(parse_questions(track.segments)) == len(track.questions)
    for cluster_id, questions in clusters:
        parts = [t for t in plan.tracks if t.cluster_id == cluster_id]
        assert [t.part for t in parts] == list(range(len(parts)))
        assert [s for t in parts for s in t.segments] == [s for q in questions for s in q.segments]
        assert [q for t in parts for q in t.questions] == questions


def test_split_only_at_question_boundaries() -> None:
    long_id, short_id = ClusterId(DECK, "c001"), ClusterId(DECK, "c002")
    # q1 は 105.3 秒。q2 は識別ポイントを除けば 64.3 秒で q1 と同じトラックに収まる(169.6 秒)が、
    # 識別ポイントまで入れると 94.3 秒で収まらない(199.6 秒)。末尾の Discrimination を切り離す実装は
    # ここで Discrimination だけのパートを作り、長さで割る実装は q2 の途中で割る。
    long = [
        qscript("q01", _text(1), _text(1), _text(100)),
        qscript("q02", _text(1), _text(60), _text(30)),
        qscript("q03", _text(10), _text(10), _text(10)),
        qscript("q04", _text(50), _text(50)),
    ]
    short = [qscript("q05", _text(5), _text(5), _text(5))]
    assert estimate_seconds([*long[0].segments, *long[1].segments[:3]], Language.JA) <= TRACK_LIMIT_SECONDS
    assert estimate_seconds([*long[0].segments, *long[1].segments], Language.JA) > TRACK_LIMIT_SECONDS

    clusters = [(long_id, long), (short_id, short)]
    plan = plan_tracks(clusters, Language.JA)
    _assert_whole(plan, clusters)
    assert [(t.cluster_id.local_id, [q.question_id.local_id for q in t.questions]) for t in plan.tracks] == [
        ("c001", ["q01"]),
        ("c001", ["q02", "q03"]),
        ("c001", ["q04"]),
        ("c002", ["q05"]),
    ]
    assert all(estimate_seconds(t.segments, Language.JA) <= TRACK_LIMIT_SECONDS for t in plan.tracks)
    # 超過して割ったクラスタだけが列挙される。収まるクラスタは1トラックのまま。
    assert plan.split == (long_id,)
    assert plan.unsplittable == ()

    # トラックも1問分より小さい単位からは作れない。
    with pytest.raises(ValueError):
        Track(long_id, 0, ())


def test_overlong_clusters_are_listed_not_truncated(deck: Deck, clusters: tuple[Cluster, ...], tmp_path: Path) -> None:
    # 1問分だけで上限を超える問題を持つクラスタは、割らずに列挙される。どのセグメントも捨てられない。
    c1, c2, c3 = (ClusterId(DECK, c) for c in ("c001", "c002", "c003"))
    over = [
        (c1, [qscript("q01", _text(5), _text(200), _text(5)), qscript("q02", _text(5), _text(5))]),
        (c2, [qscript("q03", _text(5), _text(5))]),
        (c3, [qscript("q04", _text(90), _text(60)), qscript("q05", _text(250), _text(1))]),
    ]
    plan = plan_tracks(over, Language.JA)
    _assert_whole(plan, over)
    assert plan.unsplittable == (c1, c3)
    assert plan.split == (c1, c3)

    # 生成器は割れないクラスタを全件挙げて、含意判定より前に、何も書かずに非0で止める。
    huge = "拒否される" * 300
    drafts = {
        **GOOD,
        "q01": discriminated(GOOD["q01"], GOOD["q01"].discrimination.text * 40),
        "q03": answered(GOOD["q03"], huge),
    }
    checker = StubChecker()
    out = tmp_path / "script.tsv"
    with pytest.raises(ScriptTooLong) as exc:
        generate_scripts(checker, deck, clusters, recall_set(drafts), Writer(out))
    assert exc.value.code
    assert [c.local_id for c in exc.value.cluster_ids] == ["c001", "c002"]
    assert checker.checked == []
    assert not out.exists()

    # 割れば収まるクラスタは、割ったうえで成果物に列挙される。
    drafts = {
        **GOOD,
        "q01": discriminated(GOOD["q01"], GOOD["q01"].discrimination.text * 15),
        "q02": discriminated(GOOD["q02"], GOOD["q02"].discrimination.text * 15),
    }
    writer = Writer(tmp_path / "split.tsv")
    generate_scripts(StubChecker(), deck, clusters, recall_set(drafts), writer)
    assert writer.result is not None
    assert [c.local_id for c in writer.result.split] == ["c001"]
    assert [(t.cluster_id.local_id, t.part) for t in writer.result.tracks] == [("c001", 0), ("c001", 1), ("c002", 0)]
