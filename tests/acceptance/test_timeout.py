"""呼び出し1回のタイムアウトの受入テスト(C-021)。

孫が pipe を持ち続けても、タイムアウトから 10 秒以内に `Outcome.TIMEOUT` が返ることを確かめる。`run` は
別スレッドで走らせて `join(15)` の期限を持つので、`communicate()` が戻らない実装でも無限には待たず赤になる。
"""

import contextlib
import os
import signal
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from support.grandchild_fake import PARTIAL, alive, command, grandchild_pid, wait_until_dead

from quiz_sift.adapters.claude_process import SubprocessRunner, Workspace
from quiz_sift.ports.process import CommandResult, CommandSpec, Outcome

TIMEOUT_SEC = 1
RETURN_WITHIN_SEC = 11
"""タイムアウト(1 秒)から 10 秒以内。"""
JOIN_SEC = 15


@pytest.fixture
def pid_file(tmp_path: Path) -> Iterator[Path]:
    """孫の pid の置き場。テストの後で孫が残っていれば止める(新しいセッションに抜けた孫は runner が止めない)。"""
    path = tmp_path / "grandchild.pid"
    yield path
    if path.exists():
        pid = grandchild_pid(path)
        if alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def _run_bounded(tmp_path: Path, argv: tuple[str, ...]) -> tuple[CommandResult, float]:
    spec = CommandSpec(argv, Workspace(tmp_path / "home", tmp_path).child_env(), tmp_path, "", timeout_sec=TIMEOUT_SEC)
    results: list[CommandResult] = []
    worker = threading.Thread(target=lambda: results.append(SubprocessRunner().run(spec)), daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(JOIN_SEC)
    elapsed = time.monotonic() - started
    assert not worker.is_alive(), f"run が {JOIN_SEC} 秒で戻らない"
    (result,) = results
    return result, elapsed


def test_c021_timeout_returns_while_grandchild_holds_pipes(tmp_path: Path, pid_file: Path) -> None:
    result, elapsed = _run_bounded(tmp_path, command(pid_file))

    assert elapsed < RETURN_WITHIN_SEC
    assert result.outcome is Outcome.TIMEOUT
    assert result.returncode is None
    assert result.stdout == PARTIAL
    # 孫は子と同じプロセスグループにいて、グループごと止まる。
    assert wait_until_dead(grandchild_pid(pid_file), 5)


def test_c021_timeout_returns_when_grandchild_left_the_session(tmp_path: Path, pid_file: Path) -> None:
    result, elapsed = _run_bounded(tmp_path, command(pid_file, leave_session=True))

    assert elapsed < RETURN_WITHIN_SEC
    assert result.outcome is Outcome.TIMEOUT
    assert result.stdout == PARTIAL
    # 孫の終了は求めない。この変種が本当に killpg の届かない孫を作ったことだけを確かめる。
    assert alive(grandchild_pid(pid_file))
