"""無音構造判定(C-007 / R-7 / AR-9 / A-2)。

判定は「3.0 秒以上の Silent Run があり、その前後いずれにも Voiced Run がある」。末尾に無音を足した
だけの音声は赤、先頭 / 末尾に短い無音を含む正しい音声は緑。
"""

from quiz_sift.audio.silence import detect_structure, has_pause_structure

from .conftest import RATE, quiet, tone


def _shape(frames: bytes) -> list[tuple[bool, float]]:
    return [(r.voiced, round(r.seconds, 2)) for r in detect_structure(frames, RATE)]


def test_trailing_padding_goes_red_and_leading_silence_stays_green() -> None:
    # 末尾に 4 秒の無音を足しただけ: 無音の後ろに有音が無い。
    padded = tone(2.0) + quiet(4.0)
    runs = detect_structure(padded, RATE)
    assert [r.voiced for r in runs] == [True, False]
    assert not has_pause_structure(runs)

    # 先頭にだけ長い無音があっても同じく赤。
    assert not has_pause_structure(detect_structure(quiet(4.0) + tone(2.0), RATE))

    # 先頭 30 ms・末尾 50 ms の短い無音は Silent Run として出ず、前後の Voiced に吸収される。
    good = quiet(0.03) + tone(1.0) + quiet(3.3) + tone(1.0) + quiet(0.05)
    assert _shape(good) == [(True, 1.03), (False, 3.3), (True, 1.05)]
    assert has_pause_structure(detect_structure(good, RATE))

    # 有音の途中の短い息継ぎも吸収され、区間は割れない。
    assert _shape(tone(1.0) + quiet(0.1) + tone(1.0) + quiet(3.3) + tone(1.0)) == [
        (True, 2.1),
        (False, 3.3),
        (True, 1.0),
    ]


def test_threshold_is_three_seconds() -> None:
    assert not has_pause_structure(detect_structure(tone(1.0) + quiet(2.9) + tone(1.0), RATE))
    assert has_pause_structure(detect_structure(tone(1.0) + quiet(3.0) + tone(1.0), RATE))


def test_all_silence_is_one_silent_run() -> None:
    runs = detect_structure(quiet(0.1), RATE)
    assert [(r.voiced, round(r.seconds, 2)) for r in runs] == [(False, 0.1)]
    assert not has_pause_structure(runs)
