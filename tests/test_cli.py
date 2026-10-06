"""CLI 入口(T-018)。各段が個別に起動でき、段階キャッシュで前段を呼び直さないこと。

LLM と TTS はスタブ。本文は自分で書いた合成フィクスチャで、NFR-2 の対象外。
"""

import dataclasses
import io
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import pytest
from support.claude_fake import FakeRunner, answer, exited, failure_shape
from support.cli_harness import DECK, DRAFTS, CliLlms, StubTts, no_preflight, respond, write_root

from quiz_sift import cli
from quiz_sift.adapters import llm_prompts
from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.ingest.reader import IngestFormatError
from quiz_sift.pipeline.stage import Stage, inputs_digest
from quiz_sift.ports.llm import ClassificationResult, Entailment, GroupProposal, Llm, RecallDraft
from quiz_sift.ports.metering import FailureKind, RunMeter
from quiz_sift.ports.process import CommandResult, CommandSpec
from quiz_sift.ports.tts import Tts
from quiz_sift.store.deck_scope import DeckScopedStore

SRC = Path(__file__).resolve().parents[1] / "src"

STAGES = {"ingest", "cluster", "recall", "glossary", "script", "audio", "review", "serve"}


@dataclass
class StubLlm:
    """1問1クラスタに分け、要約には先頭の問題文をそのまま返す(語句はすべて実在する)。"""

    calls: list[str] = field(default_factory=list)

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        self.calls.append("classify")
        groups = tuple(GroupProposal(q.question_id.local_id, (q.question_id,)) for q in deck.questions)
        return ClassificationResult(assignments=groups, failures=())

    def summarize_cluster(self, members: Deck) -> str:
        self.calls.append("summarize")
        return members.questions[0].stem

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        self.calls.append("recall")
        return DRAFTS[question.question_id.local_id]

    def describe_term(self, term: str, language: Language) -> str:
        self.calls.append("describe")
        return f"{term}の説明"

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.calls.append("entailment")
        return Entailment.CONSISTENT


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return write_root(tmp_path)


class Runner:
    """`cli.main` を同じスタブで呼び、標準出力の最終行(成果物のパス)を返す。"""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str], llm: StubLlm, tts: StubTts) -> None:
        self.root = root
        self.capsys = capsys
        self.llm = llm
        self.tts = tts
        self.tts_model = "model-dir"

    def _llm(self, meter: RunMeter) -> StubLlm:
        return self.llm

    def _tts(self, model_dir: str) -> Tts:
        assert model_dir == self.tts_model
        return self.tts

    def __call__(self, *argv: str) -> str:
        self.capsys.readouterr()
        extra = ["--tts-model", self.tts_model] if argv[0] == "audio" else []
        code = cli.main(
            ["--root", str(self.root), *argv, *extra],
            llm_factory=self._llm,
            preflight=no_preflight,
            tts_factory=self._tts,
        )
        assert code == 0
        return self.capsys.readouterr().out.strip().splitlines()[-1]


class FakeServer:
    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.server_port = port
        self.served = False

    def __enter__(self) -> "FakeServer":
        return self

    def __exit__(self, *exc: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def serve_forever(self) -> None:
        self.served = True


def test_every_stage_has_an_entry_point(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for stage in STAGES:
        argv = [stage] if stage == "serve" else [stage, DECK, "--tts-model", "m"] if stage == "audio" else [stage, DECK]
        assert cli._parser().parse_args(argv).stage == stage

    llm, tts = StubLlm(), StubTts()
    run = Runner(root, capsys, llm, tts)
    out = root / "out" / DECK

    # 各段を単独で起動する。前段はキャッシュに無ければその場で作られる。
    ingested = Path(run("ingest", DECK))
    assert ingested.parent == out / "ingest"
    assert len(json.loads(ingested.read_text(encoding="utf-8"))["questions"]) == 3
    assert llm.calls == []

    clustered = Path(run("cluster", DECK))
    assert [c["id"] for c in json.loads(clustered.read_text(encoding="utf-8"))["clusters"]] == ["c001", "c002", "c003"]
    assert llm.calls.count("classify") == 1

    recalled = Path(run("recall", DECK))
    assert len(json.loads(recalled.read_text(encoding="utf-8"))["materials"]) == 3
    assert llm.calls.count("classify") == 1  # クラスタリングは呼び直さない
    assert llm.calls.count("recall") == 3

    glossary = json.loads(Path(run("glossary", DECK)).read_text(encoding="utf-8"))
    assert glossary["entries"] and glossary["checked"] >= 1

    scripted = json.loads(Path(run("script", DECK)).read_text(encoding="utf-8"))
    assert [t["cluster"] for t in scripted["tracks"]] == ["c001", "c002", "c003"]
    assert llm.calls.count("recall") == 3

    audio = Path(run("audio", DECK))
    assert audio == out / "audio"
    assert sorted(p.name for p in audio.glob("*.mp3")) == [
        "001_aws-saa_c001.mp3",
        "002_aws-saa_c002.mp3",
        "003_aws-saa_c003.mp3",
    ]
    assert (audio / "playlist.m3u").is_file()
    assert (out / "glossary" / "glossary.m3u").is_file()
    assert tts.calls > 0

    # 学習記録に誤答が1件あれば、そのクラスタだけの復習系列ができる。
    question_id = QuestionId(DECK, "q03")
    DeckScopedStore(DECK, root).record_attempt(
        AttemptRecord(SessionId(DECK, "s1"), question_id, OptionId(question_id, "A"), False, 2.0, datetime.now(UTC))
    )
    review = Path(run("review", DECK))
    assert review == out / "review" / "review.m3u"
    assert [p.name for p in review.parent.glob("*.mp3")] == ["001_aws-saa_c002.mp3"]

    # 全段がキャッシュにある状態で配信側を消しても、LLM / TTS を呼ばずに作り直せる。
    llm_calls, tts_calls = len(llm.calls), tts.calls
    for delivered in (out / "audio", out / "glossary"):
        for path in delivered.iterdir():
            path.unlink()
    assert Path(run("audio", DECK)) == out / "audio"
    assert len(list((out / "audio").glob("*.mp3"))) == 3
    assert (out / "glossary" / "glossary.m3u").is_file()
    assert (len(llm.calls), tts.calls) == (llm_calls, tts_calls)

    # TTS のモデルを差し替えたら、台本もグロッサリも前のモデルの音声を返さず合成し直す(NFR-4)。
    run.tts_model = "other-model-dir"
    run("audio", DECK)
    assert tts.calls > tts_calls
    for stage in ("tracks", "glossary-audio"):
        assert len([p for p in (out / stage).iterdir() if not p.name.startswith(".")]) == 2

    servers: list[FakeServer] = []

    def fake_make_server(server_root: Path, port: int) -> FakeServer:
        servers.append(FakeServer(server_root, port))
        return servers[-1]

    monkeypatch.setattr(cli, "make_server", fake_make_server)
    assert run("serve", "--port", "8765") == "quiz-sift API: http://127.0.0.1:8765"
    assert [(s.root, s.server_port, s.served) for s in servers] == [(root, 8765, True)]


def test_llm_use_is_metered(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """LLM 段の実行は、キャッシュだけで済んでも開始行と終了行の2行を書く(NFR-7)。ingest は書かない。"""
    run = Runner(root, capsys, StubLlm(), StubTts())
    log = root / "out" / DECK / cli.METER_LOG
    run("ingest", DECK)
    assert not log.exists()
    run("cluster", DECK)
    run("cluster", DECK)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 4


def test_output_outside_gitignore_is_refused(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """出力先が `.gitignore` の対象でなければ、何も書かずに非0で止まる(NFR-2)。"""
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        Runner(root, capsys, StubLlm(), StubTts())("ingest", DECK)
    assert raised.value.code != 0
    assert not (root / "out").exists()


def test_cli_entry_point_exits_zero(tmp_path: Path) -> None:
    """gate.sh smoke の差し替え先。インストールせず `src` を通しただけの subprocess から起動できる。"""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    outputs = {}
    for flag in ("--version", "--help"):
        completed = subprocess.run(
            [sys.executable, "-m", "quiz_sift.cli", flag], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert completed.returncode == 0, completed.stderr
        outputs[flag] = completed.stdout
    assert outputs["--version"].strip() == "quiz-sift 0.1.1"
    assert all(stage in outputs["--help"] for stage in STAGES)


# 伏せる対象の合成値(C-015)。実物らしい形にしない。
SYNTHETIC_API_KEY = "sk-ant-api03-SyntheticKey"
SYNTHETIC_PASSWORD = "SyntheticPassword"
SYNTHETIC_DJ_SESSION = "SyntheticDjSession"
SYNTHETIC_UD_JWT = "SyntheticUdJwt"
SYNTHETIC_SECRETS = (SYNTHETIC_API_KEY, SYNTHETIC_PASSWORD, SYNTHETIC_DJ_SESSION, SYNTHETIC_UD_JWT)
LEAKING_MESSAGE = (
    f"接続に失敗した key={SYNTHETIC_API_KEY} password={SYNTHETIC_PASSWORD}\n"
    f"Cookie: dj_session_id={SYNTHETIC_DJ_SESSION}; ud_user_jwt={SYNTHETIC_UD_JWT}"
)


@pytest.fixture(autouse=True)
def restore_excepthook(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli.main` が差し替える `sys.excepthook` を、テストごとに元へ戻す。"""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)


class LeakingLlm(StubLlm):
    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        raise ConnectionError(LEAKING_MESSAGE)


def _hook_output(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> str:
    """`sys.stderr` を新しいバッファに差し替えてから、`error` で `sys.excepthook` を呼んだ出力。"""
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buffer)
    sys.excepthook(type(error), error, error.__traceback__)
    return buffer.getvalue()


def test_uncaught_exception_traceback_masks_credentials(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` の外へ抜けた例外のトレースバックで、認証情報が平文で出ない(C-015)。"""
    llm = LeakingLlm()
    with pytest.raises(ConnectionError) as raised:
        cli.main(["--root", str(root), "cluster", DECK], llm_factory=lambda recorder: llm, preflight=no_preflight)
    assert type(raised.value) is ConnectionError

    output = _hook_output(monkeypatch, raised.value)
    for secret in SYNTHETIC_SECRETS:
        assert secret not in output
    assert "ConnectionError: 接続に失敗した" in output
    assert "Cookie:" in output


def test_excepthook_masks_the_cause_chain_and_changes_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """伏せる以外は `traceback.format_exception` のまま。原因の側の認証情報も伏せ、連鎖の表示は残す。"""
    monkeypatch.setattr(sys, "excepthook", cli._masked_excepthook)

    def plain() -> None:
        raise ValueError("Synthetic の問題文が1件も無い")

    with pytest.raises(ValueError) as raised_plain:
        plain()
    error = raised_plain.value
    assert _hook_output(monkeypatch, error) == "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )

    def chained() -> None:
        try:
            raise ConnectionError(LEAKING_MESSAGE)
        except ConnectionError as cause:
            raise RuntimeError("Synthetic の段が失敗した") from cause

    with pytest.raises(RuntimeError) as raised_chained:
        chained()
    output = _hook_output(monkeypatch, raised_chained.value)
    assert "The above exception was the direct cause of the following exception" in output
    assert "ConnectionError: 接続に失敗した" in output
    assert "RuntimeError: Synthetic の段が失敗した" in output
    for secret in SYNTHETIC_SECRETS:
        assert secret not in output
    cause = raised_chained.value.__cause__
    assert cause is not None
    assert cause.args == (LEAKING_MESSAGE,)


def test_excepthook_is_installed_by_identity_and_does_not_delegate(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main` は `sys.excepthook` を伏せるフックに差し替える。何度呼んでも同じ関数で、直前のフックへ委ねない。"""
    recorded: list[BaseException] = []

    def recording_hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        recorded.append(exc)

    monkeypatch.setattr(sys, "excepthook", recording_hook)
    run = Runner(root, capsys, StubLlm(), StubTts())
    run("ingest", DECK)
    run("ingest", DECK)
    assert sys.excepthook is cli._masked_excepthook

    monkeypatch.setattr(sys, "excepthook", recording_hook)
    run("ingest", DECK)
    assert sys.excepthook is cli._masked_excepthook

    output = _hook_output(monkeypatch, ConnectionError(LEAKING_MESSAGE))
    assert "ConnectionError" in output
    assert recorded == []


def test_excepthook_failure_does_not_fall_back_to_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    """伏せるのに失敗しても、フックは例外を出さず、型名だけを書く。"""

    def broken_mask(text: str) -> str:
        raise RuntimeError("Synthetic の伏せ処理の失敗")

    monkeypatch.setattr(cli, "mask_secrets", broken_mask)
    monkeypatch.setattr(sys, "excepthook", cli._masked_excepthook)
    output = _hook_output(monkeypatch, ConnectionError(LEAKING_MESSAGE))
    assert "ConnectionError" in output
    for secret in SYNTHETIC_SECRETS:
        assert secret not in output


# ---- 既定 LLM(ClaudeCliLlm)を spawn だけ fake にして通す(T-044)----


def test_llm_stages_stop_on_failure_and_summarise(
    root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """外部 LLM 呼び出しに失敗があった実行は下流へ進まず非0で終わり、サマリの失敗件数が実際の数と一致する(C-003)。"""
    assert cli.LlmFactory.__value__ == Callable[[RunMeter], Llm]

    # 分割呼び出しが3回になる問題数にし、1回目と3回目だけを失敗させる。
    count = 2 * llm_prompts.CLASSIFY_BATCH_SIZE + 1
    rows = [
        {
            "id": f"q{n:03d}",
            "stem": f"合成の問題文 {n}",
            "options": [{"id": label, "text": f"合成の選択肢 {label}"} for label in "ABCD"],
            "answers": ["A"],
        }
        for n in range(count)
    ]
    (root / "data" / DECK / "questions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    shapes = [FailureKind.SCHEMA, None, FailureKind.INCOMPLETE_IDS]

    def respond(spec: CommandSpec) -> CommandResult:
        kind = shapes[len(llms.runner.specs) - 1]
        if kind is None:
            return exited(answer(spec.argv, spec.stdin))
        keys = [q["key"] for q in json.loads(spec.stdin)["questions"]]
        return failure_shape(kind, keys)

    llms = CliLlms(tmp_path, FakeRunner(respond))
    capsys.readouterr()
    with pytest.raises(SystemExit) as raised:
        cli.main(["--root", str(root), "cluster", DECK], llm_factory=llms, preflight=no_preflight)
    assert raised.value.code not in (0, None)

    # 下流(要約)は起動せず、成果物も書かない。
    assert llms.systems() == [llm_prompts.CLASSIFY_SYSTEM] * 3
    assert not any((root / "out" / DECK / "cluster").glob("[0-9a-f]*"))
    summary = [line for line in capsys.readouterr().err.splitlines() if "clustering summary" in line]
    assert len(summary) == 1
    assert "other_ratio=" in summary[0] and "max_cluster_ratio=" in summary[0]
    assert f"failure_count={sum(kind is not None for kind in shapes)}" in summary[0]


def test_stage_digest_carries_the_models_it_uses(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """各段の digest に、段が使うメソッドのモデル(ADR-015)が入る。1段のモデルだけを変えるとその段だけ作り直す。"""
    opus, haiku = "claude-opus-5-5", "claude-haiku-4-5"
    expected = (
        (cli.CLUSTER, {"classify_clusters": opus, "summarize_cluster": opus, "check_entailment": opus}),
        (cli.RECALL, {"generate_recall": haiku, "check_entailment": opus}),
        (cli.GLOSSARY, {"describe_term": haiku, "check_entailment": opus}),
        (cli.SCRIPT, {"check_entailment": opus}),
    )
    for stage, models in expected:
        assert dict(pair.split("=") for pair in stage.model.split(",")) == models, stage.name

    llm = StubLlm()
    run = Runner(root, capsys, llm, StubTts())
    run("recall", DECK)
    classified, recalled = llm.calls.count("classify"), llm.calls.count("recall")
    out = root / "out" / DECK

    # 想起素材の段のモデルだけを変える。その段は作り直され、クラスタリングの段はキャッシュのまま。
    swapped = cli.RECALL.model.replace(f"generate_recall={haiku}", "generate_recall=claude-synthetic-model")
    assert swapped != cli.RECALL.model
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "RECALL", dataclasses.replace(cli.RECALL, model=swapped))
        run("recall", DECK)
    assert llm.calls.count("classify") == classified
    assert llm.calls.count("recall") == 2 * recalled
    assert len(list((out / "recall").iterdir())) == 2
    assert len(list((out / "cluster").iterdir())) == 1


def test_ingest_cache_from_the_single_answer_format_is_not_reused(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """正解を単一の値で持つ形の成果物がキャッシュに残っていても、旧キーの入力は取り込みで止まる(C-001)。

    ingest の digest は入力のバイト列と段の設定だけで決まる。成果物の形を変えた版を段の設定に入れないと、
    旧コードが作った成果物にヒットして `IngestFormatError` が出ない。
    """
    old_key = "answ" + "er"
    questions = root / "data" / DECK / "questions.jsonl"
    rows = [json.loads(line) for line in questions.read_text(encoding="utf-8").splitlines()]
    legacy = [{**{k: v for k, v in row.items() if k != "answers"}, old_key: row["answers"][0]} for row in rows]
    questions.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in legacy) + "\n", encoding="utf-8")

    # 旧コード(段の設定に形の版が無い)が、同じ入力から作った成果物を置く。
    sources = [questions, root / "data" / DECK / "deck.yaml"]
    upstream = b"".join(path.name.encode() + b"\0" + path.read_bytes() + b"\0" for path in sources)
    stale = root / "out" / DECK / "ingest" / inputs_digest(Stage("ingest"), upstream)
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"questions": [{**row, "options": []} for row in legacy]}), encoding="utf-8")

    with pytest.raises(IngestFormatError, match="未知のキー"):
        Runner(root, capsys, StubLlm(), StubTts())("ingest", DECK)


def test_resume_makes_no_llm_calls_or_invocations_upstream(
    root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """途中の段から再実行すると、前段の llm_calls と provider_invocations がどちらも 0(C-016)。"""
    llms = CliLlms(tmp_path, FakeRunner(respond))

    def run(*argv: str) -> None:
        assert cli.main(["--root", str(root), *argv], llm_factory=llms, preflight=no_preflight) == 0

    run("recall", DECK)
    # positive control: 前段を作った実行では、分類・要約・想起素材が数えられている。
    upstream = {"classify_clusters", "summarize_cluster", "generate_recall"}
    upstream_systems = {llm_prompts.CLASSIFY_SYSTEM, llm_prompts.SUMMARIZE_SYSTEM, llm_prompts.RECALL_SYSTEM}
    assert upstream <= set(llms.methods)
    assert upstream_systems <= set(llms.systems())

    methods, spawned = len(llms.methods), len(llms.runner.specs)
    run("script", DECK)
    # 台本の段は自分の含意判定だけを呼ぶ(呼んでいること自体が、この実行で数えられていることの control)。
    assert set(llms.methods[methods:]) == {"check_entailment"}
    assert not upstream & set(llms.methods[methods:])
    assert not upstream_systems & set(llms.systems()[spawned:])

    # 全段がキャッシュにある段を再実行すると、LLM を1度も呼ばない。
    methods, spawned = len(llms.methods), len(llms.runner.specs)
    run("recall", DECK)
    assert (len(llms.methods), len(llms.runner.specs)) == (methods, spawned)
