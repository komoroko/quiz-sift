"""文面がほぼ同一の重複問題の検出(R-1)。

比較の鍵は問題文と選択肢の文面を `normalize_text` に通したもの。選択肢は並べ替えてから繋ぐ —
選択肢の順序を入れ替えただけの問題は同じ問題である。問題文だけを比べると、資格試験に多い
「正しいものはどれか」のような定型の問題文どうしが重複に数えられる。

鍵の完全一致を `exact`、`difflib.SequenceMatcher` の ratio が `NEAR_DUPLICATE_RATIO` 以上を
`near` とする。検出して件数を報告するだけで、除外はしない — どちらを残すかは人が決める。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import QuestionId
from quiz_sift.domain.question import Question
from quiz_sift.ingest.normalize import normalize_text

NEAR_DUPLICATE_RATIO = 0.95


@dataclass(frozen=True, slots=True)
class DuplicatePair:
    """重複と判定した2問。`first` が入力で先に現れた方。"""

    first: QuestionId
    second: QuestionId
    exact: bool
    """正規化後の完全一致なら `True`、ratio による「ほぼ同一」なら `False`。"""


@dataclass(frozen=True, slots=True)
class DedupReport:
    """重複検出の結果。"""

    pairs: tuple[DuplicatePair, ...]

    @property
    def exact_count(self) -> int:
        return sum(1 for pair in self.pairs if pair.exact)

    @property
    def near_count(self) -> int:
        return sum(1 for pair in self.pairs if not pair.exact)


def dedup_key(question: Question) -> str:
    """重複判定に使う、正規化済みの文面。"""
    options = sorted(normalize_text(option.text) for option in question.options)
    return "\n".join([normalize_text(question.stem), *options])


def find_duplicates(deck: Deck) -> DedupReport:
    """デッキ内の全ての組を比べる。100〜200問なら組は2万程度で、安い上限判定で大半を落とせる。"""
    keyed = [(question.question_id, dedup_key(question)) for question in deck.questions]
    pairs = []
    for i, (first_id, first_key) in enumerate(keyed):
        matcher = SequenceMatcher(autojunk=False)
        matcher.set_seq2(first_key)
        for second_id, second_key in keyed[i + 1 :]:
            if first_key == second_key:
                pairs.append(DuplicatePair(first_id, second_id, exact=True))
                continue
            matcher.set_seq1(second_key)
            if (
                matcher.real_quick_ratio() >= NEAR_DUPLICATE_RATIO
                and matcher.quick_ratio() >= NEAR_DUPLICATE_RATIO
                and matcher.ratio() >= NEAR_DUPLICATE_RATIO
            ):
                pairs.append(DuplicatePair(first_id, second_id, exact=False))
    return DedupReport(tuple(pairs))
