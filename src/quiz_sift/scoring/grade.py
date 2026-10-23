"""採点(R-8)。

採点は `chosen == frozenset(correct_option_ids)` だけで決まる。表示順はここに入ってこないので、
シャッフル後の位置とシャッフル前の正解インデックスを突き合わせる経路がコード上に無い。
"""

from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.question import Question


def grade(question: Question, chosen: frozenset[OptionId]) -> bool:
    """選んだ集合 `chosen` が `question` の正解の集合と完全に一致すれば `True`。

    集合でない回答は TypeError、この問題の選択肢でない ID を含むものや件数が正解の数 N と違うものは
    ValueError で拒む。N 件でない回答を誤答として記録すると、選び足りない操作と誤答が区別できなくなる。
    """
    if not isinstance(chosen, frozenset) or not all(isinstance(option, OptionId) for option in chosen):
        raise TypeError(f"回答は OptionId の frozenset で与える: {type(chosen).__name__}")
    if not chosen <= {option.option_id for option in question.options}:
        raise ValueError("この問題の選択肢ではない ID が回答された")
    if len(chosen) != question.answer_count:
        raise ValueError(f"選択肢は {question.answer_count} 個選ぶ: {len(chosen)} 個")
    return chosen == frozenset(question.correct_option_ids)
