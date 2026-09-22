"""事前/事後テストのレポート(R-8)。

正答率は既習フラグの値ごと(`studied` / `unseen` / `unknown`)に分けて出す(AR-10)。
`unknown` 一群だけのレポートでは、D-015 が分けたかった既習と初見の差が見えない。
出力には限界開示文 `LIMITS_DISCLOSURE` を必ず含める。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, SessionId
from quiz_sift.domain.studied import StudiedFlag

LIMITS_DISCLOSURE = (
    "この数値の限界: hold-out 分割を行っていないため、この正答率は教材に使った問題の記憶の定着を示すもので、"
    "未見問題への転移は示さない。また、事前テストを受けたこと自体が学習になる(テスト効果)ため、"
    "事前と事後の差分には音声学習の効果とテスト効果が分離不能な形で混ざっている。"
)
"""レポートに必ず含める限界開示文。hold-out 不在とテスト効果の両方に触れる(R-8)。"""


@dataclass(frozen=True, slots=True)
class Rate:
    """正答数と回答数。"""

    correct: int
    answered: int

    def __post_init__(self) -> None:
        if not 0 <= self.correct <= self.answered:
            raise ValueError(f"正答数が回答数と合わない: {self.correct}/{self.answered}")

    @property
    def ratio(self) -> float | None:
        """正答率。回答が無ければ `None`(0% とは区別する)。"""
        return None if self.answered == 0 else self.correct / self.answered


@dataclass(frozen=True, slots=True)
class PrePost:
    """事前と事後の正答率の組。"""

    pre: Rate
    post: Rate

    @property
    def delta(self) -> float | None:
        """事後 − 事前。どちらかに回答が無ければ `None`。"""
        if self.pre.ratio is None or self.post.ratio is None:
            return None
        return self.post.ratio - self.pre.ratio


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """1デッキ分の事前/事後レポート。`by_studied` は3値すべてをキーに持つ。"""

    deck_id: DeckId
    pre_session: SessionId
    post_session: SessionId
    overall: PrePost
    by_studied: dict[StudiedFlag, PrePost]
    limits: str = field(default=LIMITS_DISCLOSURE, init=False)


def _rate(attempts: Iterable[AttemptRecord]) -> Rate:
    marks = [attempt.correct for attempt in attempts]
    return Rate(correct=sum(marks), answered=len(marks))


def build_report(deck: Deck, attempts: Iterable[AttemptRecord], pre: SessionId, post: SessionId) -> ScoreReport:
    """`attempts` のうち `pre` / `post` セッションの回答から、事前/事後の正答率と差分を集計する。

    別デッキの回答やデッキに無い問題への回答は、集計に混ぜずに拒む。
    """
    if pre == post:
        raise ValueError("事前と事後に同じセッションが指定されている")
    for session in (pre, post):
        if session.deck_id != deck.deck_id:
            raise ValueError(f"別デッキのセッション: {session.deck_id}")
    studied = {question.question_id: question.studied for question in deck.questions}
    by_session: dict[SessionId, list[AttemptRecord]] = {pre: [], post: []}
    for attempt in attempts:
        if attempt.question_id not in studied:
            raise ValueError(f"このデッキに無い問題への回答: {attempt.question_id.deck_id}")
        if attempt.session_id in by_session:
            by_session[attempt.session_id].append(attempt)

    def pre_post(flag: StudiedFlag | None) -> PrePost:
        def select(session: SessionId) -> Rate:
            return _rate(a for a in by_session[session] if flag is None or studied[a.question_id] is flag)

        return PrePost(pre=select(pre), post=select(post))

    return ScoreReport(
        deck_id=deck.deck_id,
        pre_session=pre,
        post_session=post,
        overall=pre_post(None),
        by_studied={flag: pre_post(flag) for flag in StudiedFlag},
    )


def _percent(ratio: float | None) -> str:
    return "—" if ratio is None else f"{ratio * 100:.1f}%"


def _points(delta: float | None) -> str:
    return "—" if delta is None else f"{delta * 100:+.1f}pt"


def _row(label: str, pair: PrePost) -> str:
    return (
        f"| {label} | {_percent(pair.pre.ratio)} ({pair.pre.correct}/{pair.pre.answered})"
        f" | {_percent(pair.post.ratio)} ({pair.post.correct}/{pair.post.answered}) | {_points(pair.delta)} |"
    )


def render_report(report: ScoreReport) -> str:
    """Markdown のレポート本文。末尾に限界開示文を必ず付ける。"""
    lines = [
        f"# 事前/事後テストのレポート: {report.deck_id}",
        "",
        f"事前: `{report.pre_session.local_id}` / 事後: `{report.post_session.local_id}`",
        "",
        "| 群 | 事前 | 事後 | 差分 |",
        "|---|---|---|---|",
        _row("全体", report.overall),
        *(_row(flag.value, report.by_studied[flag]) for flag in StudiedFlag),
        "",
        report.limits,
        "",
    ]
    return "\n".join(lines)
