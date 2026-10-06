"""`ClaudeCliLlm`(NFR-8 (c)(e)(h)、R-3、NFR-7、T-043)。

spawn だけを fake(`support.claude_fake.FakeRunner`)に差し替え、`Workspace` は `tmp_path` を根にした実物を
走らせる(AR-72)。argv の期待値は要件・設計と T-041 の meta から書き写し、製品の定数を読まない(AR-30)。
"""

import ast
import dataclasses
import functools
import hashlib
import inspect
import json
import logging
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from support.claude_fake import (
    EXECUTABLE_DIR,
    FakeRunner,
    answer,
    classification_output,
    exited,
    failure_shape,
    fake_stdout,
    success_events,
    timed_out,
)

from quiz_sift.adapters import llm_claude_cli, llm_contract
from quiz_sift.adapters.claude_failure import scan_rate_limits
from quiz_sift.adapters.claude_process import Workspace
from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm, argv_digest
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.pipeline.meter import PRICES, Meter
from quiz_sift.pipeline.overhead import skills_digest
from quiz_sift.ports.llm import Entailment, LlmCallError, TokenUsage
from quiz_sift.ports.metering import FailureKind, InvocationScope, RateLimitInfo
from quiz_sift.ports.process import CommandResult, CommandSpec
from quiz_sift.safety.entailment import Claim, emit_if_consistent

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude_cli"
MODULE = Path(llm_claude_cli.__file__)

ALLOWED_ENV = {"HOME", "PATH", "LANG", "TZ"}
"""NFR-8 (b) の許可集合。"""
BYPASS_MODES = {"bypassPermissions", "acceptEdits"}
BODY_FLAGS = ("--json-schema", "--model", "--system-prompt")
"""A-1 が「プロンプト・スキーマ・モデル以外で一致」と言うときに除く値のフラグ。"""
DECK = DeckId("deckA")


def question(name: str) -> Question:
    """本文が問題ごとに一意な合成の問題。本文が argv やログに漏れたら文字列一致で見つかる。"""
    qid = QuestionId(DECK, name)
    return Question(
        question_id=qid,
        stem=f"合成問題文 {name} 固有句",
        options=tuple(Option(OptionId(qid, label), f"合成選択肢 {name}{label} 固有句") for label in "ABCD"),
        correct_option_ids=(OptionId(qid, "A"),),
        explanation=f"合成解説 {name} 固有句",
        studied=StudiedFlag.UNKNOWN,
    )


def deck(count: int) -> Deck:
    return Deck(DECK, Language.JA, None, tuple(question(f"q{i:03d}") for i in range(count)))


def material(q: Question) -> list[str]:
    assert q.explanation is not None
    return [q.stem, *(o.text for o in q.options), q.explanation]


def fixture_meta(name: str) -> dict[str, Any]:
    meta = yaml.safe_load((FIXTURES / f"{name}.meta.yaml").read_text(encoding="utf-8"))
    assert isinstance(meta, dict)
    return meta


def returning(result: CommandResult) -> Callable[[CommandSpec], CommandResult]:
    return lambda spec: result


def real_fixture(name: str) -> Callable[[CommandSpec], CommandResult]:
    meta = fixture_meta(name)
    stdout = (FIXTURES / f"{name}.stdout").read_bytes()
    return lambda spec: exited(stdout, meta["exit_code"], meta["stderr"].encode("utf-8"))


class RecordingMeter:
    """`RunMeter` の記録用 fake。呼ばれた順に (名前, 引数) を残す。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._invocations = 0

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args in self.calls if called == name]

    def record(self, model: str, usage: TokenUsage) -> None:
        self.calls.append(("record", (model, usage)))

    def begin_call(self, method: str) -> None:
        self.calls.append(("begin_call", (method,)))

    def begin_invocation(self, argv_digest: str) -> InvocationScope:
        self.calls.append(("begin_invocation", (argv_digest,)))
        self._invocations += 1
        return InvocationScope(self._invocations - 1, argv_digest)

    def end_invocation(
        self,
        model: str,
        turns: int | None,
        usage: TokenUsage | None,
        cost_usd: float | None,
        is_error: bool | None,
        skills_digest: str | None,
    ) -> None:
        self.calls.append(("end_invocation", (model, turns, usage, cost_usd, is_error, skills_digest)))

    def record_quota(self, info: RateLimitInfo) -> None:
        self.calls.append(("record_quota", (info,)))

    def record_failure(self, kind: FailureKind) -> None:
        self.calls.append(("record_failure", (kind,)))


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> datetime:
        return datetime(2026, 9, 26, tzinfo=UTC)

    def monotonic(self) -> float:
        self._t += 1.0
        return self._t


def make_meter(tmp_path: Path) -> Meter:
    return Meter(
        FakeClock(),
        log_path=tmp_path / "out" / "meter.jsonl",
        run_id="run-1",
        execution_id="exec-1",
        stage="cluster",
        cache_mode="fresh",
        env_names=sorted(ALLOWED_ENV),
    )


def build(meter: Any, tmp_path: Path, runner: FakeRunner) -> ClaudeCliLlm:
    """テストコードからの直接構築。spawn だけを fake にし、Workspace は tmp_path を根にした実物にする。"""
    llm = ClaudeCliLlm(meter)
    llm.runner = runner
    llm.workspace = Workspace(tmp_path / "min-home", tmp_path / "cwd-root")
    (tmp_path / "min-home").mkdir(exist_ok=True)
    (tmp_path / "cwd-root").mkdir(exist_ok=True)
    return llm


def masked_body(argv: tuple[str, ...] | list[str]) -> list[str]:
    return ["<body>" if i > 0 and argv[i - 1] in BODY_FLAGS else arg for i, arg in enumerate(argv)]


def flag(argv: tuple[str, ...], name: str) -> str:
    assert argv.count(name) == 1, name
    return argv[argv.index(name) + 1]


def test_argv_matches_the_frozen_shape_and_the_fixture(tmp_path: Path) -> None:
    runner = FakeRunner()
    llm = build(RecordingMeter(), tmp_path, runner)
    q = question("q1")
    members = Deck(DECK, Language.JA, None, (q,))
    llm.generate_recall(q, Language.JA)  # haiku
    llm.summarize_cluster(members)  # opus
    llm.classify_clusters(members)  # opus
    llm.check_entailment(material(q)[-1], q.stem)  # opus
    llm.describe_term("合成用語 固有句", Language.JA)  # haiku
    haiku, opus = runner.specs[0].argv, runner.specs[1].argv

    for argv in (spec.argv for spec in runner.specs):
        assert argv[:2] == ("claude", "-p")
        assert flag(argv, "--output-format") == "stream-json"
        assert "--verbose" in argv
        assert int(flag(argv, "--max-turns")) >= 3
        assert "--strict-mcp-config" in argv
        assert json.loads(flag(argv, "--mcp-config")) == {"mcpServers": {}}
        # ユーザ設定を読まず、有効ツールは空の許可リストで絞る。拒否リストは持たない(D-208)。
        assert flag(argv, "--setting-sources") == ""
        assert flag(argv, "--tools") == ""
        assert "--settings" not in argv
        assert "--safe-mode" in argv
        assert "--dangerously-skip-permissions" not in argv
        assert flag(argv, "--permission-mode") not in BYPASS_MODES
        # 教材本文は argv のどこにも(システムプロンプトにも)入らない。stdin にだけある。
        for text in [*material(q), "合成用語 固有句"]:
            assert not any(text in arg for arg in argv), text
    for spec in runner.specs[:4]:
        assert q.stem in spec.stdin

    # T-041 の meta が記録した argv と、プロンプト・スキーマ・モデル以外で一致する。
    assert masked_body(haiku) == masked_body(fixture_meta("success")["argv"])
    assert masked_body(opus) == masked_body(fixture_meta("success_opus")["argv"])
    assert flag(haiku, "--model") == fixture_meta("success")["model"]
    assert flag(opus, "--model") == fixture_meta("success_opus")["model"]


def test_init_tools_mismatch_is_still_refused(tmp_path: Path) -> None:
    """拒否リストを外しても、呼び出しごとの `init.tools` の一致判定は残る(T-060 A-3)。"""
    for tools in (["StructuredOutput", "Bash"], ["Read"], []):
        meter = RecordingMeter()
        runner = FakeRunner(returning(exited(fake_stdout(structured_output={"description": "合成説明"}, tools=tools))))
        with pytest.raises(LlmCallError, match="^provider_error: init.tools"):
            build(meter, tmp_path, runner).describe_term("合成用語", Language.JA)
        assert meter.of("record_failure") == [(FailureKind.PROVIDER_ERROR,)], tools

    # 対照: init.tools が {StructuredOutput} なら同じ応答が通る。
    runner = FakeRunner(returning(exited(fake_stdout(structured_output={"description": "合成説明"}))))
    assert build(RecordingMeter(), tmp_path, runner).describe_term("合成用語", Language.JA) == "合成説明"


FORBIDDEN_NODES = (ast.If, ast.Match, ast.Try, ast.TryStar, ast.Subscript, ast.IfExp, ast.BoolOp, ast.Lambda)
FORBIDDEN_NAMES = {"os", "environ", "open", "importlib", "__import__", "getattr", "eval", "exec"}


def init_violations(source: str, namespace: dict[str, Any]) -> list[str]:
    """`ClaudeCliLlm.__init__` が「属性代入と引数なしのクラス呼び出しだけ」でない箇所。"""
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ClaudeCliLlm")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    params = {a.arg for a in init.args.args}
    found: list[str] = []
    if init.args.defaults or any(d is not None for d in init.args.kw_defaults):
        found.append("既定値")
    for statement in init.body:
        match statement:
            case (
                ast.Assign(targets=[ast.Attribute(value=ast.Name(id="self"))], value=value)
                | ast.AnnAssign(target=ast.Attribute(value=ast.Name(id="self")), value=value)
            ) if value is not None:
                pass
            case _:
                found.append(f"属性代入でない文: {ast.dump(statement)[:60]}")
                continue
        if isinstance(value, ast.Name) and value.id in params:
            continue
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and not value.args and not value.keywords:
            if not inspect.isclass(namespace.get(value.func.id)):
                found.append(f"クラスでない呼び出し: {value.func.id}")
            continue
        found.append(f"引数か引数なしのクラス呼び出しでない右辺: {ast.dump(value)[:60]}")
    for node in ast.walk(init):
        if isinstance(node, FORBIDDEN_NODES):
            found.append(type(node).__name__)
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(node.id)
        if isinstance(node, ast.Attribute) and not (isinstance(node.value, ast.Name) and node.value.id == "self"):
            found.append(f"属性参照 {ast.unparse(node)}")
    return found


def test_init_body_is_assignments_and_bare_class_calls() -> None:
    namespace = vars(llm_claude_cli)
    source = MODULE.read_text(encoding="utf-8")
    assert init_violations(source, namespace) == []

    # negative control: 死角に分岐・環境変数・既定値・引数つき構築を置くと赤になる。
    original = "        self.workspace = Workspace()\n"
    assert original in source
    mutants = {
        "if": "        if meter:\n            self.workspace = Workspace()\n",
        "ifexp": "        self.workspace = Workspace() if meter else SubprocessRunner()\n",
        "environ": "        self.workspace = Workspace(os.environ['HOME'])\n",
        "引数つき": "        self.workspace = Workspace(Path('x'))\n",
        "関数": "        self.workspace = build_argv()\n",
        "try": "        try:\n            self.workspace = Workspace()\n        except OSError:\n            raise\n",
        "import": "        import importlib\n        self.workspace = Workspace()\n",
    }
    for name, replacement in mutants.items():
        assert init_violations(source.replace(original, replacement), namespace), name
    with_default = source.replace("def __init__(self, meter: RunMeter)", "def __init__(self, meter: RunMeter = None)")
    assert with_default != source
    assert init_violations(with_default, namespace) == ["既定値"]


def snapshot(root: Path) -> set[tuple[str, str]]:
    return {
        (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "-")
        for p in [root, *root.rglob("*")]
    }


def test_a_call_creates_nothing_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    min_home = tmp_path / "min-home"
    (min_home / ".claude").mkdir(parents=True)
    (min_home / ".claude.json").write_text("{}")
    (min_home / ".claude" / "settings.json").write_text('{"theme": "dark"}')
    (min_home / ".claude" / ".credentials.json").write_text('{"token": "dummy"}')
    before = (snapshot(home), snapshot(min_home))

    observed: list[tuple[set[str], Path, list[Path]]] = []

    def respond(spec: CommandSpec) -> CommandResult:
        observed.append((set(spec.env), spec.cwd, list(spec.cwd.iterdir())))
        return exited(answer(spec.argv, spec.stdin))

    llm = build(RecordingMeter(), tmp_path, FakeRunner(respond))
    llm.classify_clusters(deck(30))
    llm.summarize_cluster(deck(2))

    assert (snapshot(home), snapshot(min_home)) == before
    assert len(observed) == 3
    for env_keys, cwd, entries in observed:
        assert env_keys == ALLOWED_ENV
        assert cwd.parent == tmp_path / "cwd-root"
        assert entries == []
        assert not cwd.exists()  # 呼び出しごとに後始末する
    assert len({cwd for _, cwd, _ in observed}) == 3  # 呼び出しごとに new_cwd() を作る
    assert list((tmp_path / "cwd-root").iterdir()) == []


def test_usage_is_recorded_before_judging_and_no_retry(tmp_path: Path) -> None:
    keys = ["0", "1", "2"]
    complete = classification_output([keys])
    shapes: dict[str, tuple[CommandResult, FailureKind]] = {
        "PARSE(result あり)": (
            exited(fake_stdout(structured_output=complete, junk_line="合成の非 JSON 行")),
            FailureKind.PARSE,
        ),
        **{
            kind.name: (failure_shape(kind, keys), kind)
            for kind in (
                FailureKind.SCHEMA,
                FailureKind.TRUNCATED,
                FailureKind.INCOMPLETE_IDS,
                FailureKind.PROVIDER_ERROR,
            )
        },
    }
    fixture_usage = TokenUsage(1253, 0, 0, 141)
    for name, (result, kind) in shapes.items():
        meter = RecordingMeter()
        runner = FakeRunner(returning(result))
        outcome = build(meter, tmp_path, runner).classify_clusters(deck(3))
        assert len(runner.specs) == 1, name  # 再試行しない
        assert [f.reason.split(":")[0] for f in outcome.failures] == [kind.value], name
        assert outcome.failures[0].reason.startswith(kind.value), name
        assert meter.of("end_invocation")[0][2] == fixture_usage, name
        assert meter.of("record_failure") == [(kind,)], name
        # usage は成否の判定より先に渡る。
        assert meter.names().index("end_invocation") < meter.names().index("record_failure"), name

    # result の無い PARSE(と SPAWN / TIMEOUT)は usage=None で記録され、件数が別に数えられる。
    real = make_meter(tmp_path)
    for result in (failure_shape(FailureKind.PARSE, keys), failure_shape(FailureKind.SPAWN, keys)):
        build(real, tmp_path, FakeRunner(returning(result))).classify_clusters(deck(3))
    end = real.finish(exit_code=1, exit_reason=None)
    assert end["invocations_without_usage"] == 2
    assert end["failures_by_kind"] == {**{k.value: 0 for k in FailureKind}, "parse": 1, "spawn": 1}

    # 25 問ごとの分割: 60 問は子プロセス3本、llm_calls は 1。
    meter = RecordingMeter()
    runner = FakeRunner()
    outcome = build(meter, tmp_path, runner).classify_clusters(deck(60))
    assert outcome.failures == ()
    assert [len(json.loads(spec.stdin)["questions"]) for spec in runner.specs] == [25, 25, 10]
    assert meter.of("begin_call") == [("classify_clusters",)]
    assert len(meter.of("begin_invocation")) == 3
    assert sorted(qid for g in outcome.assignments for qid in g.question_ids) == [
        q.question_id for q in deck(60).questions
    ]


def test_every_nfr7_value_reaches_the_meter(tmp_path: Path) -> None:
    later = [{"status": "allowed_warning", "resetsAt": 1790390000}]
    stdout = fake_stdout(structured_output={"summary": "合成要約"}, later_rate_limits=later)
    meter = RecordingMeter()
    runner = FakeRunner(lambda spec: exited(stdout))
    assert build(meter, tmp_path, runner).summarize_cluster(deck(2)) == "合成要約"

    init = next(e for e in success_events() if e["type"] == "system" and e["subtype"] == "init")
    fixture_result = next(e for e in success_events() if e["type"] == "result")
    assert meter.of("begin_invocation") == [(argv_digest(runner.specs[0].argv),)]
    assert meter.of("end_invocation") == [
        (
            "claude-opus-5-5",
            fixture_result["num_turns"],
            TokenUsage(1253, 0, 0, 141),
            fixture_result["total_cost_usd"],
            False,
            skills_digest(init["skills"]),
        )
    ]
    quotas = [args[0] for args in meter.of("record_quota")]
    first = scan_rate_limits(stdout).first
    assert first is not None
    assert quotas == [first, dataclasses.replace(first, status="allowed_warning", resets_at=1790390000)]
    assert meter.of("record_failure") == []

    # 実物の success / success_opus を返す呼び出し: model は単価表のキー(日付付き ID ではない)。
    calls: list[tuple[str, Callable[[ClaudeCliLlm], object]]] = [
        ("success", lambda llm: llm.generate_recall(question("q1"), Language.JA)),
        ("success_opus", lambda llm: llm.summarize_cluster(deck(1))),
    ]
    for name, call in calls:
        meter = RecordingMeter()
        with pytest.raises(LlmCallError):  # 採取時のスキーマ({"answer": 5})なので SCHEMA で落ちる。
            call(build(meter, tmp_path, FakeRunner(real_fixture(name))))
        [(model, *_)] = meter.of("end_invocation")
        assert model == fixture_meta(name)["model"] and model in PRICES, name
        assert len(meter.of("record_quota")) == 1, name

    # argv のダイジェストはプロンプトとスキーマの本文を除いた argv から取る。
    runner = FakeRunner()
    llm = build(RecordingMeter(), tmp_path, runner)
    llm.classify_clusters(deck(2))
    llm.summarize_cluster(deck(2))
    llm.generate_recall(question("q1"), Language.JA)
    classify_argv, summarize_argv, recall_argv = (spec.argv for spec in runner.specs)
    assert classify_argv != summarize_argv
    assert argv_digest(classify_argv) == argv_digest(summarize_argv)
    assert argv_digest(recall_argv) != argv_digest(summarize_argv)
    only_model = tuple("claude-haiku-4-5" if a == "claude-opus-5-5" else a for a in summarize_argv)
    assert argv_digest(only_model) != argv_digest(summarize_argv)
    other_flag = tuple(
        "4" if i > 0 and summarize_argv[i - 1] == "--max-turns" else a for i, a in enumerate(summarize_argv)
    )
    assert argv_digest(other_flag) != argv_digest(summarize_argv)


def test_entailment_uses_the_shared_parser(tmp_path: Path) -> None:
    assert vars(llm_claude_cli)["parse_entailment"] is llm_contract.parse_entailment

    def verdict(value: object) -> CommandResult:
        return exited(fake_stdout(structured_output={"verdict": value}))

    llm = build(RecordingMeter(), tmp_path, FakeRunner(lambda spec: verdict("consistent")))
    assert llm.check_entailment("合成参照", "合成主張") is Entailment.CONSISTENT
    llm = build(RecordingMeter(), tmp_path, FakeRunner(lambda spec: verdict("contradicted")))
    assert llm.check_entailment("合成参照", "合成主張") is Entailment.CONTRADICTED

    keys = ["0"]
    failing = {
        "未知の判定語": verdict("probably_consistent"),
        "PARSE": failure_shape(FailureKind.PARSE, keys),
        "SCHEMA": exited(fake_stdout(structured_output={"verdict": 1})),
        "TIMEOUT": timed_out(fake_stdout(structured_output={"verdict": "consistent"})),
        "PROVIDER_ERROR": failure_shape(FailureKind.PROVIDER_ERROR, keys),
        "TRUNCATED": failure_shape(FailureKind.TRUNCATED, keys),
    }
    for name, result in failing.items():
        llm = build(RecordingMeter(), tmp_path, FakeRunner(returning(result)))
        emitted: list[str] = []
        with pytest.raises(LlmCallError):
            emit_if_consistent(llm, [Claim("合成参照", "合成主張")], functools.partial(emitted.append, "書いた"))
        assert emitted == [], name


def test_overage_check_passes_on_the_real_fixture_and_fails_on_overage(tmp_path: Path) -> None:
    def run(respond: Callable[[CommandSpec], CommandResult]) -> dict[str, object]:
        meter = make_meter(tmp_path / respond.__name__)
        llm = build(meter, tmp_path, FakeRunner(respond))
        try:
            llm.check_entailment("合成参照", "合成主張")
        except LlmCallError:
            pass
        return meter.finish(exit_code=0, exit_reason=None)

    def real(spec: CommandSpec) -> CommandResult:
        return real_fixture("success")(spec)

    def overage(spec: CommandSpec) -> CommandResult:
        return exited(fake_stdout(structured_output={"verdict": "consistent"}, is_using_overage=True))

    def no_event(spec: CommandSpec) -> CommandResult:
        return exited(fake_stdout(structured_output={"verdict": "consistent"}, drop_rate_limit_event=True))

    assert run(real)["overage_check"] == "passed"

    end = run(overage)
    assert end["overage_check"] == "failed"
    assert end["failures_by_kind"] == {**{k.value: 0 for k in FailureKind}, "provider_error": 1}
    llm = build(RecordingMeter(), tmp_path, FakeRunner(overage))
    with pytest.raises(LlmCallError, match="^provider_error"):
        llm.check_entailment("合成参照", "合成主張")

    # negative control: rate_limit_event が届かなければ unknown(passed に潰さない)。
    end = run(no_event)
    assert end["overage_check"] == "unknown"
    assert end["failures_by_kind"] == {k.value: 0 for k in FailureKind}


def test_overage_or_rejected_stops_the_remaining_batches(tmp_path: Path) -> None:
    def first_batch(result: CommandResult) -> Callable[[CommandSpec], CommandResult]:
        spawned: list[None] = []

        def respond(spec: CommandSpec) -> CommandResult:
            spawned.append(None)
            return result if len(spawned) == 1 else exited(answer(spec.argv, spec.stdin))

        return respond

    cases = {
        "overage": (exited(fake_stdout(is_using_overage=True)), 1),
        "rejected": (
            exited(fake_stdout(rate_limit_status="rejected", is_error=True, subtype="error_during_execution"), 1),
            1,
        ),
        "SCHEMA": (failure_shape(FailureKind.SCHEMA, ["0"]), 3),
    }
    for name, (result, spawns) in cases.items():
        meter = make_meter(tmp_path / name)
        runner = FakeRunner(first_batch(result))
        outcome = build(meter, tmp_path, runner).classify_clusters(deck(60))
        assert len(runner.specs) == spawns, name
        assert len(outcome.failures) == 1, name
        assert outcome.failures[0].question_ids == tuple(q.question_id for q in deck(60).questions[:25]), name
        end = meter.finish(exit_code=1, exit_reason=None)
        assert end["provider_invocations"] == spawns, name
        assert end["failed_invocations"] == 1, name


@pytest.fixture
def captured() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root = logging.getLogger()
    handler = Keep(logging.DEBUG)
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield records
    root.removeHandler(handler)
    root.setLevel(previous)


def test_each_invocation_emits_one_record_without_child_bodies(
    tmp_path: Path, captured: list[logging.LogRecord], capsys: pytest.CaptureFixture[str]
) -> None:
    members = deck(3)
    stem = members.questions[0].stem

    def echo(spec: CommandSpec) -> CommandResult:
        # 合成の教材本文を stdout と stderr に含める(モデルが本文を復唱した形)。
        return exited(fake_stdout(structured_output={"summary": stem}), 0, f"合成の警告 {stem}\n".encode())

    runner = FakeRunner(echo)
    llm = build(RecordingMeter(), tmp_path, runner)
    assert llm.summarize_cluster(members) == stem
    llm.classify_clusters(members)  # 応答の形が合わず SCHEMA で落ちるが、記録は同じく1件出る。

    lines = [r.getMessage() for r in captured if r.name == "quiz_sift.safety.invocation_log"]
    assert len(lines) == len(runner.specs) == 2
    for line, spec in zip(lines, runner.specs, strict=True):
        record = json.loads(line.removeprefix("claude invocation "))
        stdin = spec.stdin.encode("utf-8")
        assert record["stdin_sha256"] == hashlib.sha256(stdin).hexdigest()
        assert record["stdin_len"] == len(stdin)
        assert record["init_tools"] == ["StructuredOutput"]
        assert "stdout_masked" not in record and "stderr_masked" not in record
    everything = "\n".join(r.getMessage() for r in captured) + capsys.readouterr().out + capsys.readouterr().err
    for q in members.questions:
        for text in material(q):
            for form in (text, json.dumps(text)[1:-1]):
                assert form not in everything, form
    for spec in runner.specs:
        assert spec.stdin not in everything
    assert [r.name for r in captured] == ["quiz_sift.safety.invocation_log"] * 2

    # 子の出力をログや標準出力へ直接書く経路がモジュールに無い。
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names}
    assert not {"print", "logging", "logger", "sys"} & (names | imported)


def test_system_exit_passes_through_uncounted(tmp_path: Path) -> None:
    class Alarm(SystemExit):
        """cli の signal.alarm ハンドラが上げる形(D-138)。"""

    def interrupted(spec: CommandSpec) -> CommandResult:
        raise Alarm("合成の実経過時間上限")

    meter = make_meter(tmp_path)
    llm = build(meter, tmp_path, FakeRunner(interrupted))
    with pytest.raises(Alarm):
        llm.classify_clusters(deck(60))
    with pytest.raises(Alarm):
        llm.check_entailment("合成参照", "合成主張")
    end = meter.finish(exit_code=1, exit_reason="Alarm")
    assert end["failures_by_kind"] == {k.value: 0 for k in FailureKind}
    assert end["failed_invocations"] == 0
    assert list((tmp_path / "cwd-root").iterdir()) == []


def test_real_executable_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """spawn も実物(`SubprocessRunner`)にし、PATH に置いた fake の `claude` を走らせる。"""
    monkeypatch.setenv("PATH", f"{EXECUTABLE_DIR}{os.pathsep}{os.environ['PATH']}")
    meter = RecordingMeter()
    llm = ClaudeCliLlm(meter)
    llm.workspace = Workspace(tmp_path / "min-home", tmp_path / "cwd-root")
    (tmp_path / "min-home").mkdir()
    (tmp_path / "cwd-root").mkdir()
    outcome = llm.classify_clusters(deck(30))
    assert outcome.failures == ()
    assert sorted(qid for g in outcome.assignments for qid in g.question_ids) == [
        q.question_id for q in deck(30).questions
    ]
    assert len(meter.of("end_invocation")) == 2
