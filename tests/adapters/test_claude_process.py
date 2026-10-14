"""子プロセス境界と起動前検査(NFR-8 (b) / R-3、ADR-014 / ADR-017、T-039)。

`Workspace` は差し替えず `tmp_path` を根にして実物を走らせる(AR-72)。期待値は要件と設計から
リテラルで書き写し、製品の定数を読まない(AR-30)。
"""

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType
from typing import Any

import pytest

from quiz_sift.adapters import claude_process
from quiz_sift.adapters.claude_process import PreflightError, SubprocessRunner, Workspace, default_preflight
from quiz_sift.ports.process import CommandSpec, Outcome

REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_ENV = {"HOME", "PATH", "LANG", "TZ"}
"""NFR-8 (b) の許可集合。"""

METERED_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUD_ML_REGION",
)
"""NFR-8 が列挙した 13 個の metered 認証変数。"""

ALLOWED_SETTINGS_KEYS = {"theme"}
"""NFR-8 (b) 2 の許可キー集合。"""

REJECTED_SETTINGS_KEYS = ("env", "apiKeyHelper", "hooks", "permissions", "enabledPlugins")
"""NFR-8 (b) 2 が拒否の例に挙げたキー。"""

Snapshot = set[tuple[str, str, int]]


def make_measured_home(home: Path) -> Path:
    """T-029 の実測形の最小ホーム。"""
    claude = home / ".claude"
    (claude / "skills" / "synced" / "example").mkdir(parents=True)
    (claude / "plugins" / "synced" / "builtin").mkdir(parents=True)
    (claude / "projects" / "-tmp-quiz-sift-cwd-x").mkdir(parents=True)
    (home / ".claude.json").write_text("{}")
    (claude / "settings.json").write_text(json.dumps({"theme": "dark"}))
    (claude / "skills" / "synced" / "example" / "SKILL.md").write_text("# example\n")
    (claude / "plugins" / "synced" / "builtin" / "plugin.json").write_text("{}")
    (claude / "projects" / "-tmp-quiz-sift-cwd-x" / "session.jsonl").write_text("{}\n")
    (claude / ".credentials.json").write_text('{"token": "dummy"}')
    return home


def fake_bin(root: Path, *, with_claude: bool) -> Path:
    """子の PATH に置くディレクトリ。`claude` は検査が解決できるだけの実行ファイルで、走らせない。

    品質ゲートの /tmp は noexec なので、そこに書いたスクリプトは `shutil.which` に実行不能と見なされる。
    実行可能なマウント上の既存バイナリ(`true`)への symlink にする。
    """
    bin_dir = root / ("bin-with-claude" if with_claude else "bin-empty")
    bin_dir.mkdir()
    if with_claude:
        true_bin = shutil.which("true", path=os.defpath)
        assert true_bin is not None
        (bin_dir / "claude").symlink_to(true_bin)
    return bin_dir


def snapshot(root: Path) -> Snapshot:
    """(相対パス, sha256, mtime_ns) の集合。ディレクトリも mtime で見る(作って消した痕跡を拾う)。"""
    entries: Snapshot = set()
    for path in [root, *root.rglob("*")]:
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else "-"
        entries.add((path.relative_to(root).as_posix(), digest, info.st_mtime_ns))
    return entries


@pytest.fixture
def claude_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = fake_bin(tmp_path, with_claude=True)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


@pytest.fixture
def lock_holder() -> Iterator[Callable[[Path], None]]:
    """別プロセスに最小ホームの flock を持たせる。"""
    procs: list[subprocess.Popen[str]] = []
    script = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "print('locked', flush=True)\n"
        "sys.stdin.read()\n"
    )

    def hold(home: Path) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        procs.append(proc)
        assert proc.stdout is not None
        assert proc.stdout.readline() == "locked\n"

    yield hold
    for proc in procs:
        proc.communicate("")


def test_child_env_is_exactly_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_on_path: Path
) -> None:
    for name in METERED_VARS:
        monkeypatch.setenv(name, "metered-value")
    monkeypatch.setenv("QUIZ_SIFT_UNRELATED", "x")
    parent_path = os.environ["PATH"]
    home = tmp_path / "home"
    home.mkdir()
    workspace = Workspace(home, tmp_path / "cwd-root")
    (tmp_path / "cwd-root").mkdir()
    env_bin = shutil.which("env", path=os.defpath)
    assert env_bin is not None

    with workspace.new_cwd() as cwd:
        result = SubprocessRunner().run(CommandSpec((env_bin, "-0"), workspace.child_env(), cwd, ""))

    assert result.outcome is Outcome.EXITED and result.returncode == 0
    child_env = dict(item.split("=", 1) for item in result.stdout.decode().split("\0") if item)
    assert set(child_env) == ALLOWED_ENV
    assert child_env["HOME"] == str(home)
    assert child_env["PATH"] == parent_path
    assert not set(METERED_VARS) & set(child_env)


def test_runner_refuses_an_env_that_is_not_the_allowlist(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": os.defpath, "LANG": "C.UTF-8", "TZ": "UTC"}
    runner = SubprocessRunner()
    for bad in ({**env, "ANTHROPIC_API_KEY": "k"}, {k: v for k, v in env.items() if k != "TZ"}):
        with pytest.raises(ValueError):
            runner.run(CommandSpec(("true",), bad, tmp_path, ""))


def test_runner_allows_thinking_env_only_for_generate_recall(tmp_path: Path) -> None:
    """T-083 A-1: MAX_THINKING_TOKENS=0 は generate_recall のモデル(Haiku 4.5)の子にだけ許す(NFR-8 (b2)、ADR-029)。

    runner はメソッドを知らないので、argv の `--model` から許す追加の環境を決める。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env_bin = shutil.which("env", path=os.defpath)
    sh = shutil.which("sh", path=os.defpath)
    assert env_bin is not None and sh is not None
    # 子の PATH で解決される `claude` は、引数を無視して受け取った環境を出すだけの実行ファイル。
    claude = bin_dir / "claude"
    claude.write_text(f"#!{sh}\nexec {env_bin} -0\n")
    claude.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": str(bin_dir), "LANG": "C.UTF-8", "TZ": "UTC"}
    thinking = {**env, "MAX_THINKING_TOKENS": "0"}
    runner = SubprocessRunner()

    def argv(model: str) -> tuple[str, ...]:
        return ("claude", "--model", model)

    result = runner.run(CommandSpec(argv("claude-haiku-4-5"), thinking, tmp_path, ""))
    assert result.outcome is Outcome.EXITED and result.returncode == 0, result.stderr
    child_env = dict(item.split("=", 1) for item in result.stdout.decode().split("\0") if item)
    child_env.pop("PWD")  # fake の `claude` を走らせる sh が足す
    assert child_env == thinking

    refused = [
        # 他のメソッド(Opus 5.5・Sonnet 5.5)の子に思考を止める鍵が来た。
        (argv("claude-opus-5-5"), thinking),
        (argv("claude-sonnet-5-5"), thinking),
        (("claude",), thinking),
        # generate_recall の子でも、値が違うか鍵が欠けていれば表と一致しない。
        (argv("claude-haiku-4-5"), {**env, "MAX_THINKING_TOKENS": "1024"}),
        (argv("claude-haiku-4-5"), env),
    ]
    marker = tmp_path / "spawned"
    for args, bad in refused:
        # 起動されれば marker を作る argv。検査が起動の前に落とすので作られない。
        with pytest.raises(ValueError, match="^子プロセスの環境は"):
            runner.run(CommandSpec((sh, "-c", f"touch {marker}", *args[1:]), bad, tmp_path, ""))
        with pytest.raises(ValueError, match="^子プロセスの環境は"):
            runner.run(CommandSpec(args, bad, tmp_path, ""))
    assert not marker.exists(), "起動の前に落ちていない"
    # 対照: Opus と Sonnet の子は基本の4つだけで起動する。
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        result = runner.run(CommandSpec(argv(model), env, tmp_path, ""))
        assert result.outcome is Outcome.EXITED and result.returncode == 0, result.stderr


def test_workspace_makes_an_empty_cwd_outside_the_repo_and_cleans_it(tmp_path: Path) -> None:
    home = make_measured_home(tmp_path / "home")
    cwd_root = tmp_path / "cwd-root"
    cwd_root.mkdir()
    workspace = Workspace(home, cwd_root)
    before = snapshot(home)

    with workspace.new_cwd() as cwd:
        assert cwd.is_dir()
        assert list(cwd.iterdir()) == []
        assert cwd.parent == cwd_root
        assert not cwd.resolve().is_relative_to(REPO_ROOT)
        (cwd / "written-by-child.txt").write_text("x")
    assert not cwd.exists()
    assert list(cwd_root.iterdir()) == []

    with workspace.new_cwd() as second:
        assert second != cwd
    assert not second.exists()
    assert snapshot(home) == before


def test_second_holder_fails_fast_without_a_lock_file(
    tmp_path: Path, claude_on_path: Path, lock_holder: Callable[[Path], None]
) -> None:
    home = make_measured_home(tmp_path / "home")
    before = snapshot(home)
    lock_holder(home)

    started = time.monotonic()
    with pytest.raises(PreflightError) as caught:
        Workspace(home, tmp_path).preflight()
    assert time.monotonic() - started < 5
    assert caught.value.code != 0
    assert "使用中" in str(caught.value)
    assert snapshot(home) == before

    # 他の保持者がいなければ同じホームで通り、ロックのためのファイルは作らない。
    free = make_measured_home(tmp_path / "free")
    free_before = snapshot(free)
    Workspace(free, tmp_path).preflight()
    assert snapshot(free) == free_before


def _claude_md_file(home: Path) -> None:
    (home / ".claude" / "CLAUDE.md").write_text("# injected\n")


def _claude_md_symlink(home: Path) -> None:
    (home / ".claude" / "CLAUDE.md").symlink_to(home / "nowhere.md")


def _claude_md_dir(home: Path) -> None:
    (home / ".claude" / "CLAUDE.md").mkdir()


def _rule_file(relative: str) -> Callable[[Path], None]:
    def put(home: Path) -> None:
        path = home / ".claude" / "rules" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# rule\n")

    return put


def _settings_bytes(content: bytes) -> Callable[[Path], None]:
    def put(home: Path) -> None:
        (home / ".claude" / "settings.json").write_bytes(content)

    return put


def _no_credentials(home: Path) -> None:
    (home / ".claude" / ".credentials.json").unlink()


REJECTIONS: list[Any] = [
    pytest.param(_claude_md_file, ".claude/CLAUDE.md", "i", id="claude-md-file"),
    pytest.param(_claude_md_symlink, ".claude/CLAUDE.md", "i", id="claude-md-symlink"),
    pytest.param(_claude_md_dir, ".claude/CLAUDE.md", "i", id="claude-md-dir"),
    pytest.param(_rule_file("a.md"), ".claude/rules/a.md", "ii", id="rules-a"),
    pytest.param(_rule_file("sub/b.md"), ".claude/rules/sub/b.md", "ii", id="rules-sub-b"),
    *[
        pytest.param(
            _settings_bytes(json.dumps({"theme": "dark", key: {}}).encode()),
            ".claude/settings.json",
            "iii",
            id=f"settings-{key}",
        )
        for key in REJECTED_SETTINGS_KEYS
    ],
    pytest.param(_settings_bytes(b'{"theme": '), ".claude/settings.json", "iii", id="settings-broken-json"),
    pytest.param(_settings_bytes(b'{"theme": "\xff\xfe"}'), ".claude/settings.json", "iii", id="settings-not-utf8"),
    pytest.param(_settings_bytes(b"[]"), ".claude/settings.json", "iii", id="settings-array"),
    pytest.param(_no_credentials, ".claude/.credentials.json", "iv", id="no-credentials"),
]


@pytest.mark.parametrize(("breaker", "relative", "rule"), REJECTIONS)
def test_preflight_rejects_by_content_and_accepts_the_measured_shape(
    tmp_path: Path, claude_on_path: Path, breaker: Callable[[Path], None], relative: str, rule: str
) -> None:
    assert not set(REJECTED_SETTINGS_KEYS) & ALLOWED_SETTINGS_KEYS

    # positive control: 実測形・空の rules/・{} の settings はどれも通る(何でも拒否する実装を赤にする)。
    Workspace(make_measured_home(tmp_path / "measured"), tmp_path).preflight()
    empty_rules = make_measured_home(tmp_path / "empty-rules")
    (empty_rules / ".claude" / "rules" / "sub").mkdir(parents=True)
    Workspace(empty_rules, tmp_path).preflight()
    empty_settings = make_measured_home(tmp_path / "empty-settings")
    (empty_settings / ".claude" / "settings.json").write_text("{}")
    Workspace(empty_settings, tmp_path).preflight()

    home = make_measured_home(tmp_path / "broken")
    breaker(home)
    with pytest.raises(PreflightError) as caught:
        Workspace(home, tmp_path).preflight()
    assert isinstance(caught.value, SystemExit)
    message = str(caught.value)
    assert relative in message
    assert f"(規則 {rule})" in message


class _OpenSpy:
    """資格情報ファイルを開こうとしたパスを記録する(root で chmod 000 が効かないときの control)。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[str] = []
        real_open, real_io_open = os.open, io.open

        def spy_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
            self.opened.append(os.fsdecode(path))
            return real_open(path, *args, **kwargs)

        def spy_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, (str, bytes, os.PathLike)):
                self.opened.append(os.fsdecode(file))
            return real_io_open(file, *args, **kwargs)

        monkeypatch.setattr(os, "open", spy_os_open)
        monkeypatch.setattr(io, "open", spy_io_open)


def test_preflight_is_read_only_and_never_opens_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_on_path: Path
) -> None:
    home = make_measured_home(tmp_path / "home")
    credentials = home / ".claude" / ".credentials.json"
    before = snapshot(home)
    credentials.chmod(0o000)
    if os.geteuid() != 0:
        with pytest.raises(PermissionError):
            credentials.read_bytes()
    try:
        with pytest.MonkeyPatch.context() as patch:
            spy = _OpenSpy(patch)
            Workspace(home, tmp_path).preflight()
    finally:
        credentials.chmod(0o600)
    assert spy.opened, "spy が何も記録していない(settings.json は開くはず)"
    assert all(not path.endswith(".credentials.json") for path in spy.opened)
    assert snapshot(home) == before

    rejected = make_measured_home(tmp_path / "rejected")
    (rejected / ".claude" / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "x"}}))
    (rejected / ".claude" / "rules").mkdir()
    (rejected / ".claude" / "rules" / "a.md").write_text("# rule\n")
    rejected_before = snapshot(rejected)
    with pytest.raises(PreflightError):
        Workspace(rejected, tmp_path).preflight()
    assert snapshot(rejected) == rejected_before

    missing = tmp_path / "missing"
    with pytest.raises(PreflightError):
        Workspace(missing, tmp_path).preflight()
    assert not missing.exists()


def test_missing_claude_binary_fails_before_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_holder: Callable[[Path], None]
) -> None:
    home = make_measured_home(tmp_path / "home")
    monkeypatch.setenv("PATH", str(fake_bin(tmp_path, with_claude=False)))
    for target in (home, tmp_path / "missing"):
        with pytest.raises(PreflightError) as caught:
            Workspace(target, tmp_path).preflight()
        assert caught.value.code != 0
        assert "claude" in str(caught.value)

    lock_holder(home)
    with pytest.raises(PreflightError) as held:
        Workspace(home, tmp_path).preflight()
    assert "claude が子プロセスの PATH に見つからない" in str(held.value)
    assert "使用中" not in str(held.value)


class _Alarm(SystemExit):
    pass


def test_timeout_keeps_partial_stdout_and_prompt_travels_only_on_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    workspace = Workspace(home, tmp_path)
    env = workspace.child_env()
    runner = SubprocessRunner()
    assert CommandSpec((sys.executable,), env, tmp_path, "").timeout_sec == 600

    prompt = "教材本文のプロンプト"
    echo = (sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())")
    result = runner.run(CommandSpec(echo, env, tmp_path, prompt))
    assert result.outcome is Outcome.EXITED and result.returncode == 0
    assert result.stdout.decode() == prompt
    assert all(prompt not in arg for arg in echo)
    assert list(scratch.iterdir()) == []

    slow = (sys.executable, "-c", "import sys, time; print('partial', flush=True); time.sleep(60)")
    started = time.monotonic()
    timed_out = runner.run(CommandSpec(slow, env, tmp_path, prompt, timeout_sec=1))
    assert time.monotonic() - started < 30
    assert timed_out.outcome is Outcome.TIMEOUT
    assert timed_out.stdout == b"partial\n"

    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording_popen)

    def on_alarm(signum: int, frame: FrameType | None) -> None:
        raise _Alarm("12 時間の上限")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.5)
        with pytest.raises(_Alarm):
            runner.run(CommandSpec(slow, env, tmp_path, prompt))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert len(spawned) == 1
    assert spawned[0].returncode == -signal.SIGKILL

    missing = runner.run(CommandSpec((str(tmp_path / "no-such-binary"),), env, tmp_path, prompt))
    assert missing.outcome is Outcome.SPAWN
    assert missing.returncode is None


def _blocked_in(pid: int) -> set[int]:
    """`/proc/<pid>/status` の SigBlk(ブロック中のシグナルのビット列)を番号の集合にする。"""
    (line,) = [line for line in Path(f"/proc/{pid}/status").read_text().splitlines() if line.startswith("SigBlk:")]
    bits = int(line.split()[1], 16)
    return {signum for signum in range(1, 65) if bits >> (signum - 1) & 1}


@pytest.mark.parametrize(
    "signums",
    [(signal.SIGTERM,), (signal.SIGTERM, signal.SIGHUP), (signal.SIGALRM, signal.SIGTERM)],
    ids=["one", "term-and-hup", "alrm-and-term"],
)
def test_signal_during_spawn_still_kills_the_child_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signums: tuple[signal.Signals, ...]
) -> None:
    """fork/exec の後・Popen が戻る前に届いたシグナルの例外でも、起動済みの子のグループを止めてから通す(D-221)。

    遅らせたシグナルが2種類たまると、1本目のハンドラの例外の後始末の途中で2本目のハンドラが例外を送出する。
    それでも子のグループには SIGKILL が届く。子は親のシグナルマスクを継承するので、子の側では遅らせた
    シグナルがブロックされていないことも確かめる。
    """
    env = Workspace(tmp_path / "home", tmp_path).child_env()
    slow = (sys.executable, "-c", "import time; time.sleep(60)")
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    class SignalledPopen(real_popen):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            spawned.append(self)
            blocked_in_child.append(_blocked_in(self.pid))
            for signum in signums:
                os.kill(os.getpid(), signum)

    blocked_in_child: list[set[int]] = []

    def on_signal(signum: int, frame: FrameType | None) -> None:
        # runner の中で割り込んだときだけ例外にする。run が返った後(pytest.raises の出口など)で残りが
        # 走ってもテストの道具を壊さない。
        while frame is not None:
            if frame.f_globals.get("__name__") == claude_process.__name__:
                raise _Alarm(signal.Signals(signum).name)
            frame = frame.f_back

    monkeypatch.setattr(subprocess, "Popen", SignalledPopen)
    mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    previous = {signum: signal.signal(signum, on_signal) for signum in signums}
    try:
        with pytest.raises(_Alarm):
            SubprocessRunner().run(CommandSpec(slow, env, tmp_path, ""))
        (proc,) = spawned
        time.sleep(0.5)  # 後始末が wait まで届かなかった場合でも、SIGKILL を受けた子が終わるのを待つ
        returncode = proc.poll()  # 片付けで kill する前に、runner が止めたかを読む
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        for leftover in spawned:
            if leftover.poll() is None:
                leftover.kill()
                leftover.wait()
    assert returncode == -signal.SIGKILL
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask_before
    assert blocked_in_child[0] & set(claude_process.DEFERRED_SIGNALS) == set()


@pytest.mark.parametrize("path", ["signal", "timeout"])
def test_signal_arriving_during_cleanup_does_not_skip_the_killpg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """後始末に入った直後に次のシグナルが届いても、子のグループへの SIGKILL は飛ばされない(D-221)。

    次のシグナルは、後始末に入ってから claude_process の Python 関数が最初に呼ばれた瞬間に送る。そこで
    ハンドラの例外が出ても、killpg がそれより前に済んでいなければならない。`signal` は1本目のシグナルの
    例外の後始末、`timeout` はタイムアウトの後始末。
    """
    env = Workspace(tmp_path / "home", tmp_path).child_env()
    slow = (sys.executable, "-c", "import time; time.sleep(60)")
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen
    cleaning = False

    class RecordingPopen(real_popen):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            spawned.append(self)
            if path == "signal":
                os.kill(os.getpid(), signal.SIGTERM)

        def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
            nonlocal cleaning
            if path == "timeout" and not cleaning:
                cleaning = True
                raise subprocess.TimeoutExpired(self.args, timeout or 0)
            out: tuple[bytes, bytes] = super().communicate(input, timeout)
            return out

    def on_signal(signum: int, frame: FrameType | None) -> None:
        nonlocal cleaning
        cleaning = True
        raise _Alarm(signal.Signals(signum).name)

    sent: list[str] = []

    def profile(frame: FrameType, event: str, arg: object) -> None:
        if cleaning and not sent and event == "call" and frame.f_globals.get("__name__") == claude_process.__name__:
            sent.append(frame.f_code.co_name)
            os.kill(os.getpid(), signal.SIGHUP)

    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    previous = {signum: signal.signal(signum, on_signal) for signum in (signal.SIGTERM, signal.SIGHUP)}
    sys.setprofile(profile)
    try:
        with pytest.raises(_Alarm):
            SubprocessRunner().run(CommandSpec(slow, env, tmp_path, ""))
        sys.setprofile(None)
        (proc,) = spawned
        time.sleep(0.5)  # 後始末が wait まで届かなかった場合でも、SIGKILL を受けた子が終わるのを待つ
        returncode = proc.poll()  # 片付けで kill する前に、runner が止めたかを読む
    finally:
        sys.setprofile(None)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        for leftover in spawned:
            if leftover.poll() is None:
                leftover.kill()
                leftover.wait()
    assert sent  # 後始末の途中で次のシグナルを送った
    assert returncode == -signal.SIGKILL


def test_construction_and_import_have_no_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, scratch, cwd = tmp_path / "home", tmp_path / "tmp", tmp_path / "cwd"
    for path in (home, scratch, cwd):
        path.mkdir()
    before = snapshot(tmp_path)
    script = (
        "from quiz_sift.adapters import claude_process\nclaude_process.SubprocessRunner()\nclaude_process.Workspace()\n"
    )
    env = {
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    subprocess.run([sys.executable, "-c", script], env=env, cwd=cwd, check=True)
    assert snapshot(tmp_path) == before

    # default_preflight は HOME の下の最小ホームを検査する。
    bin_dir = fake_bin(tmp_path, with_claude=True)
    monkeypatch.setenv("PATH", str(bin_dir))
    measured = tmp_path / "measured-user"
    make_measured_home(measured / ".quiz-sift-claude-home")
    monkeypatch.setenv("HOME", str(measured))
    default_preflight()

    bare = tmp_path / "bare-user"
    no_credentials = make_measured_home(bare / ".quiz-sift-claude-home")
    (no_credentials / ".claude" / ".credentials.json").unlink()
    monkeypatch.setenv("HOME", str(bare))
    with pytest.raises(PreflightError) as caught:
        default_preflight()
    assert ".claude/.credentials.json" in str(caught.value)
