"""無音構造判定(R-7 / AR-9)。デコード済み PCM フレーム列 → 区間の列、の純粋関数。

判定式は要件の文言に揃える: **3.0 秒以上の `Silent` Run が存在し、その前後いずれにも `Voiced` Run が
ある**ものが1つ以上。「ファイルが有音で始まり有音で終わる」は要件に無い条件で、mp3 のエンコーダ遅延や
TTS 出力の余韻で落ちるので課さない。末尾に無音を足しただけの音声は、その無音の後ろに `Voiced` が
無いので通らない。
"""

import math
from array import array
from dataclasses import dataclass

PAUSE_THRESHOLD_SECONDS = 3.0
"""判定のしきい値。挿入側は 3.3 秒(mp3 で 3.000 秒が 2.992 秒になった実測による。D-053)。"""
SILENCE_DBFS = -45.0
"""窓の RMS がこれ未満なら無音とみなす。"""
MIN_SILENCE_SECONDS = 0.3
"""これ未満の無音は `Silent` Run として出さず、前後の `Voiced` に吸収する。"""
WINDOW_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class Run:
    """有音または無音が続く1区間。"""

    voiced: bool
    start: float
    seconds: float


def _window_is_voiced(samples: array[int], silence_rms: float) -> bool:
    return math.sqrt(math.sumprod(samples, samples) / len(samples)) >= silence_rms


def detect_structure(
    frames: bytes,
    sample_rate: int,
    *,
    silence_dbfs: float = SILENCE_DBFS,
    min_silence_sec: float = MIN_SILENCE_SECONDS,
) -> list[Run]:
    """符号付き 16bit リトルエンディアン・モノラルの PCM を、有音 / 無音の区間列にする。

    `min_silence_sec` 未満の無音区間は、有音区間が1つでもあれば有音として扱い隣と結合する
    (未定義だと先頭 30 ms の無音で判定が反転する)。有音が1つも無ければ全体が1つの無音区間になる。
    """
    if sample_rate <= 0:
        raise ValueError("サンプルレートは正でなければならない")
    if len(frames) % 2:
        raise ValueError("16bit PCM のバイト列が奇数長になっている")
    samples = array("h")
    samples.frombytes(frames)
    window = max(1, round(sample_rate * WINDOW_SECONDS))
    silence_rms = 32768 * 10 ** (silence_dbfs / 20)

    # (voiced, 開始サンプル, サンプル数)
    raw: list[tuple[bool, int, int]] = []
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window]
        voiced = _window_is_voiced(chunk, silence_rms)
        if raw and raw[-1][0] == voiced:
            raw[-1] = (voiced, raw[-1][1], raw[-1][2] + len(chunk))
        else:
            raw.append((voiced, start, len(chunk)))

    if any(voiced for voiced, _, _ in raw):
        min_samples = min_silence_sec * sample_rate
        raw = [(voiced or length < min_samples, start, length) for voiced, start, length in raw]

    merged: list[tuple[bool, int, int]] = []
    for voiced, start, length in raw:
        if merged and merged[-1][0] == voiced:
            merged[-1] = (voiced, merged[-1][1], merged[-1][2] + length)
        else:
            merged.append((voiced, start, length))
    return [Run(voiced, start / sample_rate, length / sample_rate) for voiced, start, length in merged]


def has_pause_structure(runs: list[Run], min_seconds: float = PAUSE_THRESHOLD_SECONDS) -> bool:
    """`min_seconds` 以上の `Silent` Run で、前後いずれにも `Voiced` Run があるものが1つ以上あるか。"""
    return any(
        not run.voiced
        and run.seconds >= min_seconds
        and any(r.voiced for r in runs[:i])
        and any(r.voiced for r in runs[i + 1 :])
        for i, run in enumerate(runs)
    )
