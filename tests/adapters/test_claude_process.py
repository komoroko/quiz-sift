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
    """子の PATH に置くディレクトリ。`claude` は検査が解決できるだけの実行ファイルで、走らせない。"""
    bin_dir = root / ("bin-with-claude" if with_claude else "bin-empty")
    bin_dir.mkdir()
    if with_claude:
        claude = bin_dir / "claude"
        claude.write_text("#!/bin/sh\nexit 99\n")
        claude.chmod(0o755)
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
