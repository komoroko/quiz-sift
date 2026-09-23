"""3分を超えた台本の分割(R-6 / ADR-008)。

クラスタは割らず、台本だけを複数トラックに割る。割るのは1問分(`QuestionScript`)の境界だけで、
先頭から詰めて上限に収まる限り同じトラックに入れる。長さで割ったり末尾の `Discrimination` を
切り離したりすると `Pause` を含まないパートや `Prompt` で始まらないパートができ、R-7 の無音構造
基準と R-6 の順序基準を落とす(AR-8 / S-3)。

1問分だけで上限を超える場合は割らず、再生成側へ倒すためにそのクラスタを `unsplittable` に挙げる。
どちらの場合も打ち切りはしない — 入力のセグメントはすべて、順序を保ってどこかのトラックに入る。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from quiz_sift.domain.ids import ClusterId
from quiz_sift.domain.language import Language
from quiz_sift.script.duration import estimate_seconds
from quiz_sift.script.segments import QuestionScript, Track

TRACK_LIMIT_SECONDS = 180.0
"""1トラックの推定再生時間の上限。初期値3分で、実測後に見直す(D-010)。"""


@dataclass(frozen=True, slots=True)
class TrackPlan:
    """全クラスタの分割結果。"""

    tracks: tuple[Track, ...]
    split: tuple[ClusterId, ...]
    """上限を超えたため複数トラックに割ったクラスタ。"""
    unsplittable: tuple[ClusterId, ...]
    """1問分だけで上限を超え、割っても収まらないクラスタ。再生成が要る。"""


def split_cluster(
    cluster_id: ClusterId,
    questions: Sequence[QuestionScript],
    language: Language,
    limit: float = TRACK_LIMIT_SECONDS,
) -> tuple[Track, ...]:
    """1クラスタ分の問題を、問の境界で上限以内のトラックに詰める。

    1問分で上限を超える問題は、それだけで1トラックにする(判定は `plan_tracks` が行う)。
    """
    parts: list[list[QuestionScript]] = []
    elapsed = 0.0
    for question in questions:
        seconds = estimate_seconds(question.segments, language)
        if parts and elapsed + seconds <= limit:
            parts[-1].append(question)
            elapsed += seconds
        else:
            parts.append([question])
            elapsed = seconds
    return tuple(Track(cluster_id, i, tuple(part)) for i, part in enumerate(parts))


def plan_tracks(
    clusters: Sequence[tuple[ClusterId, Sequence[QuestionScript]]],
    language: Language,
    limit: float = TRACK_LIMIT_SECONDS,
) -> TrackPlan:
    """各クラスタをトラックに割り、超過したクラスタと割れないクラスタを列挙する。"""
    tracks: list[Track] = []
    split: list[ClusterId] = []
    unsplittable: list[ClusterId] = []
    for cluster_id, questions in clusters:
        parts = split_cluster(cluster_id, questions, language, limit)
        tracks.extend(parts)
        if len(parts) > 1:
            split.append(cluster_id)
        if any(estimate_seconds(q.segments, language) > limit for q in questions):
            unsplittable.append(cluster_id)
    return TrackPlan(tuple(tracks), tuple(split), tuple(unsplittable))
