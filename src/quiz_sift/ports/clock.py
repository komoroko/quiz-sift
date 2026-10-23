"""時計(R-8 の所要時間 / NFR-7 の経過時間)。

テストでは固定 clock を注入し、所要時間を決定的にする。
"""

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """タイムゾーン付きの現在時刻。記録のタイムスタンプに使う。"""
        ...

    def monotonic(self) -> float:
        """単調増加する秒数。差だけに意味がある。所要時間の計測に使う。"""
        ...


class SystemClock:
    """実時計。標準ライブラリだけで書けるので adapters には置かない。"""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()
