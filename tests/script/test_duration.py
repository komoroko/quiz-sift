"""推定再生時間の言語別レート(C-006 / R-6 / A-3)。"""

import pytest

from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt, ScriptSegment
from quiz_sift.script.duration import RATES, estimate_seconds


def test_language_specific_rates() -> None:
    assert RATES == {Language.JA: 7.0, Language.EN: 2.5}

    # 日本語は空白を除いた文字数を 7.0 文字/秒で読む。14文字 → 2秒、21文字 → 3秒。
    ja: list[ScriptSegment] = [
        Prompt("アクセス頻度 と 即座の組み合せ"),
        Pause(3.3),
        Answer("あいうえおかきくけこさしすせそたちつてとな"),
    ]
    assert estimate_seconds(ja, Language.JA) == pytest.approx(2.0 + 3.3 + 3.0)

    # 英語は空白区切りの語数を 2.5 語/秒で読む。5語 → 2秒、10語 → 4秒。
    en: list[ScriptSegment] = [
        Prompt("Which storage class fits this?"),
        Pause(4.0),
        Answer("S3 Standard-IA"),
        Discrimination("it is for infrequent access but retrieves in milliseconds"),
    ]
    assert estimate_seconds(en, Language.EN) == pytest.approx(5 / 2.5 + 4.0 + 2 / 2.5 + 9 / 2.5)

    # 同じテキストでも言語で推定が変わる。単一レートの実装はここで赤になる。
    text = [Answer("one two three four five")]
    assert estimate_seconds(text, Language.EN) == pytest.approx(2.0)
    assert estimate_seconds(text, Language.JA) == pytest.approx(19 / 7.0)

    # Pause だけなら言語に関わらずその秒数。
    assert estimate_seconds([Pause(3.3)], Language.JA) == estimate_seconds([Pause(3.3)], Language.EN) == 3.3
    assert estimate_seconds([], Language.JA) == 0.0
