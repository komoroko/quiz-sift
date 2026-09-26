"""実行の列の CLI(T-045、NFR-7 節「実行の列」、ADR-018)。

`--fresh-run` / `--resume`、起動前検査の結線、開始行と終了行、12 時間の上限、段の早期停止を、既定 LLM
(`ClaudeCliLlm`)の spawn だけを fake にして通す。本文はすべて合成で、NFR-2 の対象外。
"""

import inspect
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support.claude_fake import FakeRunner, exited, fake_stdout, success_events
from support.cli_harness import DECK, CliLlms, StubTts, flag, no_preflight, respond, write_root

from quiz_sift import cli, composition
from quiz_sift.adapters import claude_process, llm_prompts
from quiz_sift.adapters.claude_process import PreflightError, SubprocessRunner
from quiz_sift.pipeline.stage import inputs_digest
from quiz_sift.ports.process import CommandResult, CommandSpec

METERED_ENV = (
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
"""NFR-8 が列挙した 13 個の metered 認証変数(要件から書き写す。製品の定数は読まない)。"""

type Shape = Callable[[CommandSpec], CommandResult]


def overage(spec: CommandSpec) -> CommandResult:
    """応答そのものは成功だが、子の `rate_limit_event` に `isUsingOverage` 真が現れる。"""
    return respond(spec, is_using_overage=True)


def rejected(spec: CommandSpec) -> CommandResult:
    """枠切れ: `status: rejected` のイベントのあと、`is_error` 真の result で終わる。"""
    return exited(
        fake_stdout(
            subtype="error_during_execution", is_error=True, structured_output=None, rate_limit_status="rejected"
        ),
        1,
    )


def schema(spec: CommandSpec) -> CommandResult:
    """想起素材のスキーマに合わない応答(`SCHEMA`)。"""
    return exited(fake_stdout(structured_output={"terms": "合成", "answer": "合成", "discrimination": "合成"}))


SHAPES: dict[str, Shape] = {"overage": overage, "rejected": rejected, "schema": schema}


class Responder:
    """`respond` で応答し、`fail_next_recall` が設定されていれば次の想起素材の呼び出しだけをその形で返す。"""

    def __init__(self) -> None:
        self.fail_next_recall: Shape | None = None

    def __call__(self, spec: CommandSpec) -> CommandResult:
        if self.fail_next_recall is not None and flag(spec.argv, "--system-prompt") == llm_prompts.RECALL_SYSTEM:
            shape, self.fail_next_recall = self.fail_next_recall, None
            return shape(spec)
        return respond(spec)


class Harness:
    """1つの作業ルートで `cli.main` を何度も呼ぶ。spawn は `llms.runner` に残る。"""

    def __init__(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], runner: FakeRunner | None = None) -> None:
        self.root = write_root(tmp_path)
        self.capsys = capsys
        self.responder = Responder()
        self.llms = CliLlms(tmp_path, runner if runner is not None else FakeRunner(self.responder))
        self.log = self.root / "out" / DECK / cli.METER_LOG
        self.err = ""

    def main(self, *argv: str, preflight: Callable[[], None] = no_preflight) -> int:
        """`cli.main` を呼び、終了コードを返す。`SystemExit` で抜けたらその例外をそのまま上げる。"""
        extra = ["--tts-model", "model-dir"] if argv[0] == "audio" else []
        self.capsys.readouterr()
        try:
            return cli.main(
                ["--root", str(self.root), *argv, *extra],
                llm_factory=self.llms,
                preflight=preflight,
                tts_factory=lambda model_dir: StubTts(),
            )
        finally:
            self.err = self.capsys.readouterr().err

    def fails(self, *argv: str, preflight: Callable[[], None] = no_preflight) -> SystemExit:
        with pytest.raises(SystemExit) as raised:
            self.main(*argv, preflight=preflight)
        assert raised.value.code not in (0, None)
        return raised.value

    def run_id(self) -> str:
        (line,) = [line for line in self.err.splitlines() if line.startswith("run_id: ")]
        return line.removeprefix("run_id: ")

    def lines(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def last_end(self) -> dict[str, Any]:
        (end,) = [line for line in self.lines()[-1:] if line["record"] == "end"]
        return end

    def spawned(self) -> int:
        return len(self.llms.runner.specs)

    def systems(self, since: int = 0) -> list[str]:
        return self.llms.systems()[since:]


def test_fresh_run_registers_and_bypasses_the_cache(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A-1: 新しい run_id を stderr に出し、開始行を最初の spawn より前に書き、通常の実行の成果物を迂回する。"""
    seen_at_spawn: list[list[dict[str, Any]]] = []

    def reading(spec: CommandSpec) -> CommandResult:
        seen_at_spawn.append(harness.lines())
        return respond(spec)

    harness = Harness(tmp_path, capsys, FakeRunner(reading))
    assert harness.main("cluster", DECK) == 0
    normal_spawned = harness.spawned()
    assert normal_spawned > 0
    assert harness.lines()[0]["cache_mode"] == "normal"

    seen_at_spawn.clear()
    assert harness.main("cluster", DECK, "--fresh-run") == 0
    run_id = harness.run_id()
    # 同じ入力の通常の実行の成果物があっても、ヒットせずに呼び直す。
    assert harness.spawned() == 2 * normal_spawned
    # 最初の spawn の時点で、この実行の開始行(fresh)がファイルにあり、終了行はまだ無い。
    first = seen_at_spawn[0]
    starts = [line for line in first if line["record"] == "start" and line["run_id"] == run_id]
    assert [start["cache_mode"] for start in starts] == ["fresh"]
    assert not [line for line in first if line["record"] == "end" and line["run_id"] == run_id]

    # 成果物の名前は、通常の実行が run_id なしの digest、fresh の実行が run_id 入りの digest。
    out = harness.root / "out" / DECK
    (ingested,) = {path.read_bytes() for path in (out / "ingest").iterdir() if not path.name.startswith(".")}
    produced = {path.name for path in (out / "cluster").iterdir() if not path.name.startswith(".")}
    assert produced == {inputs_digest(cli.CLUSTER, ingested), inputs_digest(cli.CLUSTER, ingested, run_id)}

    # 2回目の fresh は別の run_id で、また呼び直す。
    assert harness.main("cluster", DECK, "--fresh-run") == 0
    assert harness.run_id() != run_id
    assert harness.spawned() == 3 * normal_spawned


def test_resume_is_allowed_only_for_a_resumable_sequence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-2: --resume は resumable の列だけ。再開した実行は自分の列の成果物にだけヒットする。"""
    harness = Harness(tmp_path, capsys)

    def refused(run_id: str) -> None:
        lines, spawned = len(harness.lines()), harness.spawned()
        assert isinstance(harness.fails("recall", DECK, "--resume", run_id), cli.ResumeRefused)
        assert (len(harness.lines()), harness.spawned()) == (lines, spawned)

    refused("no-such-run")

    # completed: 最終段 audio まで exit 0 で走った列。
    assert harness.main("audio", DECK, "--fresh-run") == 0
    completed = harness.run_id()
    assert harness.last_end()["sequence"]["status"] == "completed"
    refused(completed)

    # not_completable: metered 認証変数を持った実行を含む列。
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-key")
    assert harness.main("cluster", DECK, "--fresh-run") == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    not_completable = harness.run_id()
    assert harness.last_end()["sequence"]["status"] == "not_completable"
    refused(not_completable)

    # 通常の実行と別の列が、同じ入力の recall まで作っておく。
    assert harness.main("recall", DECK) == 0
    assert harness.main("recall", DECK, "--fresh-run") == 0

    assert harness.main("cluster", DECK, "--fresh-run") == 0
    resumable = harness.run_id()
    assert harness.last_end()["sequence"]["status"] == "resumable"

    # 列が作った段はキャッシュから読む(spawn 0)。
    spawned = harness.spawned()
    assert harness.main("cluster", DECK, "--resume", resumable) == 0
    assert harness.run_id() == resumable
    assert harness.spawned() == spawned
    assert harness.lines()[-2]["cache_mode"] == "resume"

    # 列がまだ作っていない recall は、通常の実行や別の列の成果物にヒットせずに作る。前段は呼び直さない。
    assert harness.main("recall", DECK, "--resume", resumable) == 0
    systems = harness.systems(spawned)
    assert llm_prompts.RECALL_SYSTEM in systems
    assert llm_prompts.CLASSIFY_SYSTEM not in systems
    assert llm_prompts.SUMMARIZE_SYSTEM not in systems


LLM_STAGES = ("cluster", "recall", "glossary", "script", "audio")


def test_preflight_runs_before_registration_on_llm_stages_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-3: LLM 段は開始行より前に preflight を呼び、落ちたら何も登録しない。ingest / review / serve は呼ばない。"""
    parameters = inspect.signature(cli.main).parameters
    assert parameters["llm_factory"].default is composition.build_default_llm
    assert parameters["preflight"].default is claude_process.default_preflight

    harness = Harness(tmp_path, capsys)
    for stage in LLM_STAGES:
        lines_at_call: list[int] = []

        def failing(lines_at_call: list[int] = lines_at_call) -> None:
            lines_at_call.append(len(harness.lines()))
            raise PreflightError("最小ホームの検査に不合格(合成)")

        lines, spawned = len(harness.lines()), harness.spawned()
        assert isinstance(harness.fails(stage, DECK, "--fresh-run", preflight=failing), PreflightError)
        assert lines_at_call == [lines]
        assert (len(harness.lines()), harness.spawned()) == (lines, spawned)

        # 通る preflight は、開始行を書く前に1回だけ呼ばれる。
        lines_at_call.clear()

        def passing(lines_at_call: list[int] = lines_at_call) -> None:
            lines_at_call.append(len(harness.lines()))

        assert harness.main(stage, DECK, preflight=passing) == 0
        assert lines_at_call == [lines]
        assert len(harness.lines()) == lines + 2

    def forbidden() -> None:
        raise AssertionError("LLM を呼ばない段が起動前検査を呼んだ")

    lines = len(harness.lines())
    assert harness.main("ingest", DECK, preflight=forbidden) == 0
    assert harness.main("review", DECK, preflight=forbidden) == 0

    class FakeServer:
        server_port = 8765

        def __enter__(self) -> "FakeServer":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def serve_forever(self) -> None:
            return None

    monkeypatch.setattr(cli, "make_server", lambda root, port: FakeServer())
    assert harness.main("serve", preflight=forbidden) == 0
    assert len(harness.lines()) == lines


class Hanging:
    """1本目は応答し(`rate_limit_event` を1件含む)、2本目は止まらない実の子プロセスを起動する。"""

    def __init__(self) -> None:
        self.calls = 0
        self.popens: list[subprocess.Popen[bytes]] = []

    def __call__(self, spec: CommandSpec) -> CommandResult:
        self.calls += 1
        if self.calls == 1:
            return respond(spec)
        hang = (sys.executable, "-c", "import time; time.sleep(60)")
        return SubprocessRunner().run(CommandSpec(argv=hang, env=spec.env, cwd=spec.cwd, stdin=spec.stdin))


def stop_by_limit(harness: Harness, monkeypatch: pytest.MonkeyPatch, hanging: Hanging) -> SystemExit:
    """上限を1秒にして、2本目の子で止まらない cluster 段を走らせる。"""
    real = subprocess.Popen

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc: subprocess.Popen[bytes] = real(*args, **kwargs)
        hanging.popens.append(proc)
        return proc

    monkeypatch.setattr(cli, "EXECUTION_LIMIT_SEC", 1)
    monkeypatch.setattr(subprocess, "Popen", recording)
    return harness.fails("cluster", DECK, "--fresh-run")


def test_the_execution_limit_stops_with_an_end_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-4: 上限は signal.alarm で掛かり、子を kill し、終了行に最後の resetsAt を残す。"""
    hanging = Hanging()
    harness = Harness(tmp_path, capsys, FakeRunner(hanging))
    error = stop_by_limit(harness, monkeypatch, hanging)
    assert isinstance(error, cli.ExecutionLimitExceeded)
    assert signal.getsignal(signal.SIGALRM) is not cli._on_alarm  # 元のハンドラに戻す

    (proc,) = hanging.popens
    assert proc.returncode == -signal.SIGKILL

    end = harness.last_end()
    assert end["stop"] == {"exit_code": 1, "exit_reason": "ExecutionLimitExceeded", "kind": "other"}
    (event,) = [event for event in success_events() if event["type"] == "rate_limit_event"]
    assert end["quota_at_end"]["resetsAt"] == event["rate_limit_info"]["resetsAt"]
    assert end["provider_invocations"] == 2
    assert end["elapsed_sec"] < 10  # 差し替えた1秒の上限で止まった(子の sleep 60 を待っていない)


def test_every_execution_writes_both_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-5: exit 0・失敗による非0・上限による停止・キャッシュだけの実行のどれも、開始行と終了行を1組ずつ書く。"""
    hanging = Hanging()
    harness = Harness(tmp_path, capsys, FakeRunner(hanging))
    stop_by_limit(harness, monkeypatch, hanging)  # 上限による停止
    monkeypatch.undo()

    harness.llms.runner = FakeRunner(harness.responder)
    assert harness.main("cluster", DECK, "--fresh-run") == 0  # exit 0
    run_id = harness.run_id()
    spawned = harness.spawned()
    assert harness.main("cluster", DECK, "--resume", run_id) == 0  # キャッシュだけ
    assert harness.spawned() == spawned
    harness.responder.fail_next_recall = schema
    harness.fails("recall", DECK, "--resume", run_id)  # 失敗による非0

    lines = harness.lines()
    executions = [line["execution_id"] for line in lines]
    assert len(set(executions)) == 4
    for execution_id in set(executions):
        records = [line["record"] for line in lines if line["execution_id"] == execution_id]
        assert records == ["start", "end"]
    assert [line["stop"]["exit_code"] for line in lines if line["record"] == "end"] == [1, 0, 0, 1]


@pytest.mark.parametrize(("shape", "spawned"), [("overage", 1), ("rejected", 1), ("schema", 3)])
def test_overage_or_rejected_stops_the_rest_of_the_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], shape: str, spawned: int
) -> None:
    """A-6: 3クラスタの recall 段で、1つ目が overage か枠切れなら残りを発行しない。SCHEMA なら3つとも発行する。"""
    harness = Harness(tmp_path, capsys)
    assert harness.main("cluster", DECK) == 0
    before = harness.spawned()
    harness.responder.fail_next_recall = SHAPES[shape]

    assert isinstance(harness.fails("recall", DECK), cli.RecallCallsFailed)
    assert harness.systems(before) == [llm_prompts.RECALL_SYSTEM] * spawned
    (summary,) = [line for line in harness.err.splitlines() if "recall summary" in line]
    assert "failure_count=1" in summary
    end = harness.last_end()
    assert end["failed_invocations"] == 1
    assert end["provider_invocations"] == spawned


def sequence_ends(harness: Harness, run_id: str) -> list[dict[str, Any]]:
    return [line for line in harness.lines() if line["record"] == "end" and line["run_id"] == run_id]


def test_a_sequence_completes_across_a_rate_limit_stop_and_overage_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A-7: fresh → 枠で停止 → resume で audio まで exit 0 の列は completed。overage を含む列は再開できない。"""
    harness = Harness(tmp_path, capsys)

    assert harness.main("cluster", DECK, "--fresh-run") == 0
    run_id = harness.run_id()
    harness.responder.fail_next_recall = rejected
    harness.fails("recall", DECK, "--resume", run_id)
    stopped = harness.last_end()
    assert stopped["ended_by_rate_limit"]["value"] is True
    assert stopped["stop"]["kind"] == "rate_limit"
    assert stopped["sequence"]["status"] == "resumable"
    assert harness.main("audio", DECK, "--resume", run_id) == 0

    ends = sequence_ends(harness, run_id)
    assert len(ends) == 3
    final = ends[-1]["sequence"]
    assert final["status"] == "completed", final["not_completable_reasons"]
    assert final["llm_calls"] == sum(end["llm_calls"] for end in ends)
    assert final["provider_invocations"] == sum(end["provider_invocations"] for end in ends)

    assert harness.main("cluster", DECK, "--fresh-run") == 0
    other = harness.run_id()
    harness.responder.fail_next_recall = overage
    harness.fails("recall", DECK, "--resume", other)
    assert harness.last_end()["sequence"]["status"] == "not_completable"
    assert "overage_invocations" in harness.last_end()["sequence"]["not_completable_reasons"]
    lines, spawned = len(harness.lines()), harness.spawned()
    assert isinstance(harness.fails("audio", DECK, "--resume", other), cli.ResumeRefused)
    assert (len(harness.lines()), harness.spawned()) == (lines, spawned)


def replace_environment(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def start_line(harness: Harness) -> dict[str, Any]:
    (start,) = [line for line in harness.lines()[-2:-1] if line["record"] == "start"]
    return start


def test_metered_env_names_come_from_the_parent_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-8: env_names は親環境の変数名そのもので値を含まず、metered_env_names は 13 変数との積集合。"""
    harness = Harness(tmp_path, capsys)
    base = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "QS_SYNTHETIC_NAME": "synthetic-value-0"}
    replace_environment(monkeypatch, base)
    assert harness.main("cluster", DECK, "--fresh-run") == 0
    start = start_line(harness)
    assert set(start["env_names"]) == set(base)
    assert start["metered_env_names"] == []
    assert "synthetic-value" not in harness.log.read_text(encoding="utf-8")

    for index, name in enumerate(METERED_ENV, start=1):
        env = {**base, name: f"synthetic-value-{index}"}
        replace_environment(monkeypatch, env)
        assert harness.main("cluster", DECK, "--fresh-run") == 0
        start = start_line(harness)
        assert set(start["env_names"]) == set(env)
        assert start["metered_env_names"] == [name]
        assert harness.last_end()["sequence"]["status"] == "not_completable"
    assert "synthetic-value" not in harness.log.read_text(encoding="utf-8")
