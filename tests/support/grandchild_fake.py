"""stdout / stderr の pipe を継承した孫を残して眠る fake の子(T-056、C-021)。

`command(pid_file)` が子の argv を返す。子は孫を起動して孫の pid を `pid_file` に書き、部分出力を
1行出してから眠る。孫は子の stdout / stderr をそのまま持って眠るので、子だけを kill しても pipe の
書き込み側が開いたまま残る。`leave_session=True` の変種では孫が `setsid` で新しいセッションに抜け、
子のプロセスグループへの killpg が届かない。孫の寿命は `GRANDCHILD_LIFETIME_SEC` で打ち切る。
"""

import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

GRANDCHILD_LIFETIME_SEC = 60
PARTIAL = b"partial\n"


def command(pid_file: Path, *, leave_session: bool = False) -> tuple[str, ...]:
    """子の argv。"""
    return (sys.executable, __file__, str(pid_file), *(["--leave-session"] if leave_session else []))


def grandchild_pid(pid_file: Path) -> int:
    return int(pid_file.read_text(encoding="ascii"))


def alive(pid: int) -> bool:
    """`pid` が生きているか。init に引き取られたゾンビは止まったものとして扱う。"""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def wait_until_dead(pid: int, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def main(argv: Sequence[str]) -> None:
    pid_file, *flags = argv
    sleeper = (sys.executable, "-c", f"import time; time.sleep({GRANDCHILD_LIFETIME_SEC})")
    grandchild = subprocess.Popen(sleeper, start_new_session="--leave-session" in flags)
    tmp = Path(pid_file + ".partial")
    tmp.write_text(str(grandchild.pid), encoding="ascii")
    tmp.replace(pid_file)
    sys.stdout.buffer.write(PARTIAL)
    sys.stdout.flush()
    time.sleep(GRANDCHILD_LIFETIME_SEC)


if __name__ == "__main__":
    main(sys.argv[1:])
