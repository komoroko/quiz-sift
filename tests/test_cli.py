"""CLI 入口(T-018)。各段が個別に起動でき、段階キャッシュで前段を呼び直さないこと。

LLM と TTS はスタブ。本文は自分で書いた合成フィクスチャで、NFR-2 の対象外。
"""

import io
import json
import math
import os
import subprocess
import sys
import traceback
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import pytest

from quiz_sift import cli
from quiz_sift.domain.attempt import AttemptRecord
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId, SessionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import ClassificationResult, Entailment, GroupProposal, RecallDraft, UsageRecorder
from quiz_sift.ports.tts import PcmAudio, Tts
from quiz_sift.store.deck_scope import DeckScopedStore

DECK = DeckId("aws-saa")
RATE = 16000
SRC = Path(__file__).resolve().parents[1] / "src"

# (id, 問題文, 選択肢, 正解, 解説)
ROWS = [
    (
        "q01",
        "アクセス頻度は低いが即座に取り出す必要があるデータに適した S3 のストレージクラスはどれか。",
        ["S3 標準", "S3 標準-IA", "S3 Glacier Deep Archive", "S3 One Zone-IA"],
        "B",
        "S3 標準-IA はアクセス頻度が低いデータ向けのストレージクラスで、ミリ秒単位で取り出せる。",
    ),
    (
        "q03",
        "IAM ポリシーで許可と明示的な拒否が両方ある場合、アクセスはどうなるか。",
        ["許可される", "拒否される", "先に書かれた方が優先される", "管理者だけ許可される"],
        "B",
        "IAM では明示的な拒否が常に許可より優先される。",
    ),
    (
        "q05",
        "RDS でプライマリ障害時に自動でフェイルオーバーさせるには何を使うか。",
        ["マルチ AZ 配置", "リードレプリカ", "スナップショット", "パラメータグループ"],
        "A",
        "マルチ AZ 配置はスタンバイへ自動でフェイルオーバーする。",
    ),
]
DRAFTS = {
    "q01": RecallDraft(
        RecallKey(("アクセス頻度", "即座", "ストレージクラス")),
        "S3 標準-IA",
        "標準-IA は低頻度向けでもミリ秒で取り出せるが、「S3 Glacier Deep Archive」は取り出しに数時間かかる",
    ),
    "q03": RecallDraft(
        RecallKey(("明示的な拒否", "許可")),
        "拒否される",
        "明示的な拒否は常に許可より優先され、書いた順序は関係しない",
    ),
    "q05": RecallDraft(
        RecallKey(("フェイルオーバー", "マルチ AZ")),
        "マルチ AZ 配置",
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、「リードレプリカ」は読み取りの負荷分散のためのもの",
    ),
}
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


@dataclass
class StubTts:
    calls: int = 0

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        self.calls += 1
        n = round(0.5 * RATE)
        tone = array("h", (round(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n)))
        return PcmAudio(tone.tobytes(), RATE)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    deck_dir = tmp_path / "data" / DECK
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck.yaml").write_text(f"deck_id: {DECK}\ntitle: AWS SAA 模試\nlanguage: ja\n", encoding="utf-8")
    rows = [
        {
            "id": qid,
            "stem": stem,
            "options": [{"id": label, "text": text} for label, text in zip("ABCD", texts, strict=True)],
            "answer": answer,
            "explanation": explanation,
        }
        for qid, stem, texts, answer, explanation in ROWS
    ]
    (deck_dir / "questions.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


class Runner:
    """`cli.main` を同じスタブで呼び、標準出力の最終行(成果物のパス)を返す。"""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str], llm: StubLlm, tts: StubTts) -> None:
        self.root = root
        self.capsys = capsys
        self.llm = llm
        self.tts = tts
        self.tts_model = "model-dir"

    def _llm(self, recorder: UsageRecorder) -> StubLlm:
        return self.llm

    def _tts(self, model_dir: str) -> Tts:
        assert model_dir == self.tts_model
        return self.tts

    def __call__(self, *argv: str) -> str:
        self.capsys.readouterr()
        extra = ["--tts-model", self.tts_model] if argv[0] == "audio" else []
        code = cli.main(["--root", str(self.root), *argv, *extra], llm_factory=self._llm, tts_factory=self._tts)
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
    """LLM を組み立てた実行だけが計測を開始行と終了行の2行書く(NFR-7)。キャッシュだけで済む実行は書かない。"""
    run = Runner(root, capsys, StubLlm(), StubTts())
    log = root / "out" / DECK / cli.METER_LOG
    run("ingest", DECK)
    assert not log.exists()
    run("cluster", DECK)
    run("cluster", DECK)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


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
    assert outputs["--version"].strip() == "quiz-sift 0.1.0"
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
        cli.main(["--root", str(root), "cluster", DECK], llm_factory=lambda recorder: llm)
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
