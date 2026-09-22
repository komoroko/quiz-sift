"""採点(R-8)。

採点は `chosen == correct_option_id` だけで決まる。表示順はここに入ってこないので、
シャッフル後の位置とシャッフル前の正解インデックスを突き合わせる経路がコード上に無い。
"""

from quiz_sift.domain.ids import OptionId
from quiz_sift.domain.question import Question


def grade(question: Question, chosen: OptionId) -> bool:
    """`chosen` が `question` の正解なら `True`。この問題の選択肢でない ID は拒む。"""
    if not isinstance(chosen, OptionId):
        raise TypeError(f"回答は OptionId で与える: {type(chosen).__name__}")
    if chosen not in {option.option_id for option in question.options}:
        raise ValueError("この問題の選択肢ではない ID が回答された")
    return chosen == question.correct_option_id
