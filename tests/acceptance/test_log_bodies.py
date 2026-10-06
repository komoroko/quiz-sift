"""子の出力本文をログからも例外文からも外す受入テスト(T-061 / C-014、NFR-2、ADR-024)。

fake の子(`support.claude_fake`)が stdout と stderr に番兵の本文を混ぜて返す。成功と各失敗経路を `cli.main` で
流し、全レベルのログレコード・stderr・`main` の外へ抜けた例外に、番兵とその断片が 0 件であることを確かめる。
`cli.main` は root logger を INFO にするので、ログ設定関数(`cli._log_level`)を差し替えて DEBUG も集める(AR-D6)。
本文はすべて合成。
"""

import io
import json
import logging
import os
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from support.claude_fake import (
    EXECUTABLE_DIR,
    SENTINEL,
    SENTINEL_MODE_FILE,
    FakeRunner,
    classification_output,
    exited,
    fake_stdout,
    sentinel_fragments,
    timed_out,
)
from support.cli_harness import DECK, CliLlms, flag, no_preflight, respond, write_root

from quiz_sift import cli
from quiz_sift.ports.process import CommandResult, CommandSpec

ROOT = Path(__file__).resolve().parents[2]
INVOCATION_LOGGER = "quiz_sift.safety.invocation_log"
STDERR = f"Error: {SENTINEL}\n".encode()
"""どの経路でも子の stderr に載せる本文。"""


def _keys(spec: CommandSpec) -> list[str]:
    return [question["key"] for question in json.loads(spec.stdin)["questions"]]


@dataclass(frozen=True)
class Route:
    """1つの経路: `target`(スキーマのプロパティ名)のメソッドだけ `bad` で応答し、`stages` を順に走らせる。"""

    stages: tuple[str, ...]
    target: str
    bad: Callable[[CommandSpec], CommandResult]


PATHS = {
    "success": Route(
        ("cluster", "recall"),
        "groups",
        lambda s: exited(
            fake_stdout(
                structured_output={
                    "groups": [{"label": SENTINEL, "question_ids": [k]} for k in _keys(s)],
                    "unassigned": [],
                }
            )
        ),
    ),
    "schema": Route(
        ("cluster",), "groups", lambda s: exited(fake_stdout(structured_output={"groups": SENTINEL, "unassigned": []}))
    ),
    "parse": Route(("cluster",), "summary", lambda s: exited(fake_stdout(result_count=0, junk_line=SENTINEL), 1)),
    "timeout": Route(
        ("cluster",), "summary", lambda s: timed_out(fake_stdout(structured_output={"summary": SENTINEL}))
    ),
    "unknown_verdict": Route(
        ("cluster",), "verdict", lambda s: exited(fake_stdout(structured_output={"verdict": SENTINEL}))
    ),
    "unknown_key": Route(
        ("cluster",),
        "groups",
        lambda s: exited(fake_stdout(structured_output=classification_output([_keys(s)], [SENTINEL]))),
    ),
    "provider_error_tool": Route(
        ("cluster",),
        "summary",
        lambda s: exited(fake_stdout(structured_output={"summary": SENTINEL}, tool_use_name=SENTINEL)),
    ),
    "provider_error_subtype": Route(
        ("cluster",),
        "summary",
        lambda s: exited(fake_stdout(subtype=SENTINEL, is_error=True, structured_output={"summary": SENTINEL}), 1),
    ),
    "incomplete_ids": Route(
        ("cluster",),
        "groups",
        lambda s: exited(fake_stdout(structured_output=classification_output([_keys(s)[1:]]))),
    ),
    "truncated": Route(
        ("cluster",),
        "summary",
        lambda s: exited(
            fake_stdout(
                subtype="error_max_turns", is_error=True, stop_reason=SENTINEL, structured_output={"summary": SENTINEL}
            ),
            1,
        ),
    ),
    "recall_schema": Route(
        ("cluster", "recall"),
        "terms",
        lambda s: exited(fake_stdout(structured_output={"terms": [], "answer": SENTINEL, "discrimination": SENTINEL})),
    ),
}


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return write_root(root)


def _responder(path: Route) -> Callable[[CommandSpec], CommandResult]:
    def run(spec: CommandSpec) -> CommandResult:
        properties = json.loads(flag(spec.argv, "--json-schema"))["properties"]
        result = path.bad(spec) if path.target in properties else respond(spec)
        return CommandResult(result.outcome, result.returncode, result.stdout, STDERR)

    return run


class _Keep(logging.Handler):
    """NOTSET のハンドラ。届いたレコードを、例外とスタックを含めて整形して残す。"""

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []
        self.texts: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.texts.append(logging.Formatter("%(levelname)s %(name)s: %(message)s").format(record))


@pytest.fixture
def kept(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Keep]:
    monkeypatch.setattr(cli, "_log_level", lambda: logging.DEBUG)
    handler = _Keep()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _run_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: Route) -> tuple[str, list[BaseException], CliLlms]:
    """`path` の段を順に走らせ、stderr と `main` の外へ抜けた例外を返す。抜けた段の後は走らせない。"""
    root = _root(tmp_path)
    llms = CliLlms(tmp_path, FakeRunner(_responder(path)))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    escaped: list[BaseException] = []
    for stage in path.stages:
        try:
            cli.main(["--root", str(root), stage, DECK], llm_factory=llms, preflight=no_preflight)
        except BaseException as error:  # 失敗経路では SystemExit の派生や ContractError が抜ける。
            escaped.append(error)
            break
    return stderr.getvalue(), escaped, llms


def _assert_no_sentinel(text: str, label: str) -> None:
    for fragment in sentinel_fragments():
        assert fragment not in text, (label, fragment)


@pytest.mark.parametrize("name", list(PATHS))
def test_c014_child_bodies_never_reach_any_log_level(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: _Keep
) -> None:
    path = PATHS[name]
    stderr, escaped, llms = _run_path(tmp_path, monkeypatch, path)

    # 番兵を返す子が実際に起動され、DEBUG まで集めていて、記録が出ている(走査が空振りしていない)。
    targeted = [s for s in llms.runner.specs if path.target in json.loads(flag(s.argv, "--json-schema"))["properties"]]
    assert targeted, name
    assert any(r.name == INVOCATION_LOGGER for r in kept.records), name
    assert (name == "success") == (not escaped), (name, escaped)
    if name == "recall_schema":
        # cli の warning は失敗種別だけを出す。
        assert [r.getMessage() for r in kept.records if r.levelno == logging.WARNING] == [
            f"recall: 問題 {qid} の呼び出しが失敗: schema" for qid in ("q01", "q03", "q05")
        ]

    _assert_no_sentinel("\n".join(kept.texts), f"{name}: log records")
    _assert_no_sentinel(stderr, f"{name}: stderr")
    for error in escaped:
        _assert_no_sentinel("".join(traceback.format_exception(error)), f"{name}: escaped exception")


def test_c014_debug_records_are_collected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: _Keep) -> None:
    """差し替えたログ設定で DEBUG のレコードが実際に集まる(全レベルの検査の対照)。"""
    probe = logging.getLogger("quiz_sift.adapters.synthetic_probe")

    def run(spec: CommandSpec) -> CommandResult:
        probe.debug("合成の DEBUG レコード")
        return respond(spec)

    llms = CliLlms(tmp_path, FakeRunner(run))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    cli.main(["--root", str(_root(tmp_path)), "cluster", DECK], llm_factory=llms, preflight=no_preflight)
    assert any(r.levelno == logging.DEBUG and r.name == probe.name for r in kept.records)


def test_c014_invocation_record_keeps_structured_fields_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept: _Keep
) -> None:
    _run_path(tmp_path, monkeypatch, PATHS["unknown_verdict"])
    records = [
        json.loads(r.getMessage().removeprefix("claude invocation "))
        for r in kept.records
        if r.name == INVOCATION_LOGGER
    ]
    assert records
    for record in records:
        assert set(record) == {
            "argv",
            "exit_code",
            "duration_sec",
            "usage",
            "failure_kind",
            "result_is_error",
            "rate_limit_first",
            "rate_limit_last",
            "rate_limit_event_count",
            "overage_seen",
            "stdin_sha256",
            "stdin_len",
            "init_tools",
        }
        assert record["argv"][:2] == ["claude", "-p"]
        assert record["exit_code"] == 0
        assert isinstance(record["duration_sec"], float)
        assert set(record["usage"]) == {
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        }
        assert record["rate_limit_first"]["status"] == record["rate_limit_last"]["status"] == "allowed"
        assert record["rate_limit_event_count"] == 1
        assert record["overage_seen"] is False
        assert len(record["stdin_sha256"]) == 64 and record["stdin_len"] > 0
        assert record["init_tools"] == ["StructuredOutput"]
    kinds = [record["failure_kind"] for record in records]
    assert kinds[-1] == "schema" and set(kinds[:-1]) == {None}


def test_c014_excepthook_path_carries_no_child_body(tmp_path: Path) -> None:
    """`python -m quiz_sift.cli` を走らせ、未知の判定語で `main` の外へ抜けた例外を excepthook が stderr に書く。"""
    home = tmp_path / "home"
    minimal_home = home / ".quiz-sift-claude-home"
    (minimal_home / ".claude").mkdir(parents=True)
    (minimal_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    # fake の `claude` はリポジトリ内の実行ファイルを使う(tmp は noexec のことがある)。番兵モードは HOME の印で伝える。
    (minimal_home / SENTINEL_MODE_FILE).touch()
    root = _root(tmp_path)
    env = {
        "HOME": str(home),
        "PATH": f"{EXECUTABLE_DIR}{os.pathsep}{os.environ['PATH']}",
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(ROOT / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "quiz_sift.cli", "--root", str(root), "cluster", DECK],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # 子は実際に起動され、例外は main の外へ抜けて excepthook がトレースバックを書いた。
    assert proc.returncode == 1, proc.stderr
    assert "claude invocation" in proc.stderr
    assert "Traceback (most recent call last)" in proc.stderr and "ContractError" in proc.stderr
    _assert_no_sentinel(proc.stderr, "stderr")
    _assert_no_sentinel(proc.stdout, "stdout")
