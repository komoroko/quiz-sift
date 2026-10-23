"""子プロセスの起動(ADR-014 の境界の2分割)。

ゲートで差し替えるのは spawn だけで、env の構築・argv の組み立て・空 cwd の作成・最小ホームの検証は
実コードのまま走る。この Protocol はその spawn 1回の形を決める。実装は
`quiz_sift.adapters.claude_process.SubprocessRunner`。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT_SEC = 600.0
"""子プロセス1本あたりのタイムアウト(10 分)。"""


@dataclass(frozen=True, slots=True, repr=False)
class CommandSpec:
    """spawn 1回ぶんの指定。`stdin` が教材本文を持つので repr を出さない(AR-17)。"""

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    stdin: str
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("argv が空になっている")
        if self.timeout_sec <= 0:
            raise ValueError("タイムアウトは正でなければならない")


class Outcome(Enum):
    """子プロセスがどう終わったか。失敗の種別(`FailureKind`)への写像は呼び出し側が行う。"""

    EXITED = "exited"
    TIMEOUT = "timeout"
    SPAWN = "spawn"


@dataclass(frozen=True, slots=True, repr=False)
class CommandResult:
    """spawn の結果。`stdout` はタイムアウトでも途中までを持つ(overage の走査に使う)。

    `returncode` は `EXITED` のときだけ値を持つ。`SPAWN` のときは起動に失敗した理由を `stderr` に入れる。
    """

    outcome: Outcome
    returncode: int | None
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    def run(self, spec: CommandSpec) -> CommandResult:
        """`spec` のとおりに子プロセスを1本走らせ、終わるまで待つ。"""
        ...
