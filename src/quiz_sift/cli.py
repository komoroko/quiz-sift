"""quiz-sift の CLI 入口(NFR-4 / NFR-5 / D-033)。

`python -m quiz_sift.cli [--root DIR] <段> ...` で、取り込み・クラスタリング・想起素材・グロッサリ・
台本・音声・復習リスト・serve の各段を個別に起動する。

各段は前段を `pipeline.stage.StageCache` 越しに呼ぶ。前段の成果物がキャッシュにあればそれを読み、
無ければその場で作る。どの段から起動しても同じ digest の成果物は作り直さないので、途中の段からの
再実行で前段の LLM / TTS 呼び出しは 0 回になる。LLM と TTS は最初に要ったときに1度だけ組み立てる —
キャッシュだけで済む実行は `claude` も CosyVoice も要らない。

LLM を呼びうる段(cluster / recall / glossary / script / audio)は、起動前検査 → 開始行 → 段 → `finally` で終了行、
の順に動く(NFR-7 節「実行の列」、ADR-018)。`--fresh-run` は新しい列を始めて段キャッシュを迂回し、
`--resume RUN_ID` は `resumable` の列に実行を1つ足す。無指定は通常のキャッシュを使う1実行だけの列になる。
1回の実行は `signal.alarm` で 12 時間に制限する(D-138)。ingest / review / serve は LLM を呼ばないので、
検査も列への登録もしない(D-143)。

成果物は JSON(音声は書き出したディレクトリの tar)で `out/<deck>/<段>/<digest>` に置く。配信側の
`out/<deck>/audio/` と `out/<deck>/glossary/` は実行のたびにキャッシュから作り直す(AR-14)。
書き出す前に、デッキの出力先が `.gitignore` の対象であることを確かめる(NFR-2)。
"""

import argparse
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import tarfile
import tempfile
import traceback
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, BinaryIO

from quiz_sift import __version__, composition
from quiz_sift.adapters import claude_process, llm_prompts
from quiz_sift.adapters.llm_claude_cli import MAX_TURNS
from quiz_sift.adapters.llm_contract import ContractError
from quiz_sift.audio.synthesize import synthesize_tracks
from quiz_sift.cluster.run import Clustering, cluster_deck
from quiz_sift.decks.registry import deck_data_dir, deck_out_dir, manifest_path
from quiz_sift.domain.cluster import Cluster
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.glossary.audio_series import GLOSSARY_DIRNAME, write_glossary_audio
from quiz_sift.glossary.describe import GlossaryEntry
from quiz_sift.glossary.verify import Consistent, Glossary, NoReference, emit_glossary
from quiz_sift.ingest.deck import ingest_deck
from quiz_sift.pipeline.meter import Meter
from quiz_sift.pipeline.run_sequence import UnknownRunError, aggregate, read_log
from quiz_sift.pipeline.stage import Stage, StageCache
from quiz_sift.ports.clock import SystemClock
from quiz_sift.ports.llm import Llm, LlmCallError, RecallDraft
from quiz_sift.ports.metering import RunMeter
from quiz_sift.ports.tts import Tts
from quiz_sift.recall.generate import RecallSet, generate_recall_set
from quiz_sift.recall.verify import RecallMaterial, RecallReport, TermRate
from quiz_sift.safety.gitignore_guard import ensure_ignored
from quiz_sift.safety.log_filter import MaterialTextFilter
from quiz_sift.safety.secret_mask import SecretMaskFilter, mask_secrets
from quiz_sift.schedule.playlist import record_and_rank, write_review_series
from quiz_sift.schedule.rank import NoTargets
from quiz_sift.script.generate import PAUSE_SECONDS, ScriptSet, generate_scripts
from quiz_sift.script.segments import QuestionScript, Track
from quiz_sift.server.handler import DEFAULT_PORT, HOST, make_server
from quiz_sift.store.deck_scope import DeckScopedStore

logger = logging.getLogger(__name__)

type LlmFactory = Callable[[RunMeter], Llm]
type TtsFactory = Callable[[str], Tts]
"""`--tts-model` の値を受け取って TTS を組み立てる。"""
type Preflight = Callable[[], None]
"""起動前検査(NFR-8 (b))。落ちたら `SystemExit` の派生を上げる。"""

LLM_STAGES = ("cluster", "recall", "glossary", "script", "audio")
"""LLM を呼びうる段。起動前検査と実行の列への登録はこの段だけが行う(D-143)。"""

EXECUTION_LIMIT_SEC = 12 * 60 * 60
"""1回の実行の実経過時間の上限(R-3、D-138)。列の壁時計には上限を置かない。"""

DEFAULT_VOICES = {Language.JA: "日语男", Language.EN: "英文女"}
"""`--voice` を省いたときのボイス。`audio.voices.VOICE_TABLE` の話者名。"""

# 配信ディレクトリ。キャッシュの段名(`out/<deck>/<段>/`)と重ねない — 作り直しで消すので、重なると
# キャッシュごと消える。
AUDIO_DIRNAME = "audio"
REVIEW_DIRNAME = "review"
METER_LOG = "meter.jsonl"

# `inputs_digest` に入れるプロンプトとモデル(AR-21)。プロンプトは `llm_prompts` の定数そのもので、
# 直せば digest が変わって作り直される。含意判定はどの段でも走るので全段に入れる。モデルは段が使う
# メソッドごとの割り当て(ADR-015)を並べ、どれか1つを変えればその段の digest が動く。生成パラメータは
# CLI 経路に `max_tokens` が無いので `--max-turns` を入れる(AR-62)。
_LLM_PARAMS = {"max_turns": MAX_TURNS}
_ENTAILMENT = llm_prompts.ENTAILMENT_SYSTEM


def _models(*methods: str) -> str:
    return ",".join(f"{m}={llm_prompts.MODELS[m]}" for m in (*methods, "check_entailment"))


# ingest の digest は入力のバイト列だけで決まり、実装コードは入らない。成果物(deck JSON)の形を変えたら
# `deck_format` を上げて digest を変える。上げないと旧形式の成果物にキャッシュヒットし、旧形式の入力が
# 取り込みの検査(`IngestFormatError`)を通らずに素通りする。2 = 正解を `answers` 配列で持つ形(ADR-020)。
INGEST = Stage("ingest", params={"deck_format": 2})
CLUSTER = Stage(
    "cluster",
    prompt_template="\n".join((llm_prompts.CLASSIFY_SYSTEM, llm_prompts.SUMMARIZE_SYSTEM, _ENTAILMENT)),
    model=_models("classify_clusters", "summarize_cluster"),
    params={**_LLM_PARAMS, "batch_size": llm_prompts.CLASSIFY_BATCH_SIZE},
)
RECALL = Stage(
    "recall",
    prompt_template="\n".join((llm_prompts.RECALL_SYSTEM, _ENTAILMENT)),
    model=_models("generate_recall"),
    params=_LLM_PARAMS,
)
GLOSSARY = Stage(
    "glossary-terms",
    prompt_template="\n".join((llm_prompts.DESCRIBE_SYSTEM, _ENTAILMENT)),
    model=_models("describe_term"),
    params=_LLM_PARAMS,
)
SCRIPT = Stage(
    "script",
    prompt_template=_ENTAILMENT,
    model=_models(),
    params={**_LLM_PARAMS, "pause_seconds": PAUSE_SECONDS},
)


class ExecutionLimitExceeded(SystemExit):
    """1回の実行が `EXECUTION_LIMIT_SEC` を超えた。`signal.alarm` のハンドラが上げる(D-138)。"""


class TerminatedBySignal(SystemExit):
    """実行中に親が SIGHUP / SIGTERM を受けた。終了コードは 128 + シグナル番号(D-221)。"""


class ResumeRefused(SystemExit):
    """`--resume` の列が `resumable` でない(開始行の無い run_id を含む。D-145)。"""


class NoRunMeter(SystemExit):
    """LLM を呼ばない段(ingest / review)が、キャッシュに無い前段のために LLM を要した。"""


def _dump(data: object, out: BinaryIO) -> None:
    out.write(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


# ---- 成果物の JSON 表現 ----


def _deck_json(deck: Deck) -> dict[str, Any]:
    return {
        "deck_id": deck.deck_id,
        "language": deck.language.value,
        "default_studied": None if deck.default_studied is None else deck.default_studied.value,
        "questions": [
            {
                "id": q.question_id.local_id,
                "stem": q.stem,
                "options": [[o.option_id.local_id, o.text] for o in q.options],
                "answers": [o.local_id for o in q.correct_option_ids],
                "explanation": q.explanation,
                "studied": q.studied.value,
            }
            for q in deck.questions
        ],
    }


def _deck_from(data: dict[str, Any]) -> Deck:
    deck_id = DeckId(data["deck_id"])
    questions = []
    for row in data["questions"]:
        question_id = QuestionId(deck_id, row["id"])
        questions.append(
            Question(
                question_id=question_id,
                stem=row["stem"],
                options=tuple(Option(OptionId(question_id, oid), text) for oid, text in row["options"]),
                correct_option_ids=tuple(OptionId(question_id, oid) for oid in row["answers"]),
                explanation=row["explanation"],
                studied=StudiedFlag(row["studied"]),
            )
        )
    default = data["default_studied"]
    return Deck(
        deck_id=deck_id,
        language=Language(data["language"]),
        default_studied=None if default is None else StudiedFlag(default),
        questions=tuple(questions),
    )


def _clusters_json(clustering: Clustering) -> dict[str, Any]:
    return {
        "clusters": [
            {"id": c.cluster_id.local_id, "summary": c.summary, "questions": [q.local_id for q in c.question_ids]}
            for c in clustering.clusters
        ],
        "metrics": asdict(clustering.metrics),
    }


def _clusters_from(data: dict[str, Any], deck_id: DeckId) -> tuple[Cluster, ...]:
    return tuple(
        Cluster(ClusterId(deck_id, row["id"]), row["summary"], tuple(QuestionId(deck_id, q) for q in row["questions"]))
        for row in data["clusters"]
    )


def _recall_json(recall: RecallSet) -> dict[str, Any]:
    report = recall.report
    return {
        "materials": [
            {
                "cluster": m.cluster_id.local_id,
                "question": m.question_id.local_id,
                "terms": list(m.draft.key.terms),
                "answers": [{"option": e.option_id.local_id, "text": e.text} for e in m.draft.answers],
                "discrimination": {
                    "text": m.draft.discrimination.text,
                    "distractors": [o.local_id for o in m.draft.discrimination.distractor_ids],
                },
            }
            for m in recall.materials
        ],
        "report": {
            "ungrounded_keys": report.ungrounded_keys,
            "missing_options": report.missing_options,
            "duplicate_keys": report.duplicate_keys,
            "generic_keys": report.generic_keys,
            "verbatim": report.verbatim,
            "empty": report.empty,
            "answer_mismatch": report.answer_mismatch,
            "term_rates": [[r.question_id.local_id, r.term, r.rate] for r in report.term_rates],
            "regenerations": {q.local_id: n for q, n in report.regenerations},
        },
    }


def _recall_draft(row: dict[str, Any], question_id: QuestionId) -> RecallDraft:
    discrimination = row["discrimination"]
    return RecallDraft(
        RecallKey(tuple(row["terms"])),
        tuple(AnswerElement(OptionId(question_id, e["option"]), e["text"]) for e in row["answers"]),
        DiscriminationPoint(
            discrimination["text"], tuple(OptionId(question_id, o) for o in discrimination["distractors"])
        ),
    )


def _recall_from(data: dict[str, Any], deck_id: DeckId) -> RecallSet:
    materials = tuple(
        RecallMaterial(
            ClusterId(deck_id, row["cluster"]),
            QuestionId(deck_id, row["question"]),
            _recall_draft(row, QuestionId(deck_id, row["question"])),
        )
        for row in data["materials"]
    )
    report = data["report"]
    rates = tuple(TermRate(QuestionId(deck_id, q), term, rate) for q, term, rate in report.pop("term_rates"))
    regenerations = tuple((QuestionId(deck_id, q), n) for q, n in report.pop("regenerations").items())
    return RecallSet(materials, RecallReport(**report, term_rates=rates, regenerations=regenerations))


def _script_json(scripts: ScriptSet) -> dict[str, Any]:
    return {
        "split": [c.local_id for c in scripts.split],
        "tracks": [
            {
                "cluster": t.cluster_id.local_id,
                "part": t.part,
                "questions": [
                    {
                        "id": q.question_id.local_id,
                        "prompt": q.prompt.text,
                        "pause": q.pause.seconds,
                        "answer": q.answer.text,
                        "discrimination": None if q.discrimination is None else q.discrimination.text,
                    }
                    for q in t.questions
                ],
            }
            for t in scripts.tracks
        ],
    }


def _tracks_from(data: dict[str, Any], deck_id: DeckId) -> tuple[Track, ...]:
    return tuple(
        Track(
            ClusterId(deck_id, row["cluster"]),
            row["part"],
            tuple(
                QuestionScript(
                    QuestionId(deck_id, q["id"]),
                    Prompt(q["prompt"]),
                    Pause(q["pause"]),
                    Answer(q["answer"]),
                    None if q["discrimination"] is None else Discrimination(q["discrimination"]),
                )
                for q in row["questions"]
            ),
        )
        for row in data["tracks"]
    )


def _glossary_json(glossary: Glossary) -> dict[str, Any]:
    checked = glossary.verification.checked if isinstance(glossary.verification, Consistent) else 0
    return {"entries": [[e.term, e.description] for e in glossary.entries], "checked": checked}


def _glossary_from(data: dict[str, Any]) -> Glossary:
    checked = data["checked"]
    verification = Consistent(checked) if checked else NoReference()
    return Glossary(tuple(GlossaryEntry(term, description) for term, description in data["entries"]), verification)


# ---- 音声ディレクトリのキャッシュ ----


def _pack(directory: Path, out: BinaryIO) -> None:
    with tarfile.open(fileobj=out, mode="w") as tar:
        for path in sorted(directory.iterdir()):
            tar.add(path, arcname=path.name)


def _deliver(archive: Path, target: Path) -> Path:
    """キャッシュの tar から配信ディレクトリを作り直す。前回の残りは消す。"""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    return target


class Pipeline:
    """1デッキ分の段。各メソッドは前段をキャッシュ越しに呼んで、自分の成果物を返す。

    `cache_run_id` は段キャッシュの digest に入れる列の ID(通常の実行は `None`。D-137)。`meter` は LLM 段の
    実行で開始行を書いた計測器で、LLM は最初に要ったときにこれへ結線して1度だけ組み立てる。
    """

    def __init__(self, root: Path, deck_id: DeckId, llm_factory: LlmFactory, *, cache_run_id: str | None) -> None:
        self.root = root
        self.deck_id = deck_id
        self.out_dir = deck_out_dir(root / "out", deck_id)
        self._cache = StageCache(root / "out", deck_id, cache_run_id)
        self._llm_factory = llm_factory
        self._llm: Llm | None = None
        self.meter: Meter | None = None
        self.material_filter = MaterialTextFilter()
        ensure_ignored(self.out_dir, root, is_dir=True)

    def _run_meter(self) -> Meter:
        if self.meter is None:
            raise NoRunMeter("この段は LLM を呼ばない: キャッシュに無い前段を、先に LLM 段(cluster など)で作る")
        return self.meter

    @property
    def llm(self) -> Llm:
        if self._llm is None:
            self._llm = self._llm_factory(self._run_meter())
        return self._llm

    def _ingest_path(self) -> Path:
        manifest = manifest_path(self.root / "data", self.deck_id)
        if not manifest.is_file():
            raise SystemExit(f"デッキの manifest が無い: {manifest}")
        data_dir = deck_data_dir(self.root / "data", self.deck_id)
        sources = [*sorted(data_dir.glob("*.jsonl")), manifest]
        upstream = b"".join(path.name.encode() + b"\0" + path.read_bytes() + b"\0" for path in sources)

        def produce(out: BinaryIO) -> None:
            ingested = ingest_deck(self.root / "data", self.deck_id)
            report = ingested.report
            logger.info(
                "ingest: input=%d accepted=%d rejected=%d with_explanation=%d duplicates exact=%d near=%d",
                report.input_total,
                report.accepted,
                len(report.rejected),
                report.with_explanation,
                ingested.duplicates.exact_count,
                ingested.duplicates.near_count,
            )
            _dump(_deck_json(ingested.deck), out)

        return self._cache.run(INGEST, upstream, produce)

    def ingest(self) -> Path:
        return self._ingest_path()

    def deck(self) -> Deck:
        deck = _deck_from(_load(self._ingest_path()))
        for question in deck.questions:
            self.material_filter.register_question(question)
        return deck

    def cluster(self) -> Path:
        deck = self.deck()

        def produce(out: BinaryIO) -> None:
            cluster_deck(self.llm, deck, lambda clustering: _dump(_clusters_json(clustering), out))

        return self._cache.run(CLUSTER, self._ingest_path().read_bytes(), produce)

    def clusters(self) -> tuple[Cluster, ...]:
        return _clusters_from(_load(self.cluster()), self.deck_id)

    def _recall_upstream(self) -> bytes:
        return self._ingest_path().read_bytes() + b"\0" + self.cluster().read_bytes()

    def recall(self) -> Path:
        deck, clusters = self.deck(), self.clusters()

        def produce(out: BinaryIO) -> None:
            generate_recall_set(
                self.llm,
                deck,
                clusters,
                lambda result: _dump(_recall_json(result), out),
                stop_seen=self._run_meter().stop_seen,
                failure_kind=_failure_kind,
            )

        return self._cache.run(RECALL, self._recall_upstream(), produce)

    def glossary(self) -> Path:
        deck = self.deck()

        def produce(out: BinaryIO) -> None:
            emit_glossary(self.llm, deck, lambda result: _dump(_glossary_json(result), out))

        return self._cache.run(GLOSSARY, self._ingest_path().read_bytes(), produce)

    def script(self) -> Path:
        deck, clusters = self.deck(), self.clusters()
        recall_path = self.recall()
        recall = _recall_from(_load(recall_path), self.deck_id)

        def produce(out: BinaryIO) -> None:
            generate_scripts(self.llm, deck, clusters, recall, lambda result: _dump(_script_json(result), out))

        return self._cache.run(SCRIPT, self._recall_upstream() + b"\0" + recall_path.read_bytes(), produce)

    def audio(self, tts: Callable[[], Tts], tts_model: str, voice_id: str | None) -> Path:
        """台本とグロッサリを音声にし、`out/<deck>/audio/` と `out/<deck>/glossary/` を作り直す。

        `tts_model`(`--tts-model` の値)は digest に入れる。モデルを差し替えたら合成し直す(NFR-4)。
        """
        deck = self.deck()
        voice = voice_id or DEFAULT_VOICES[deck.language]
        script_path, glossary_path = self.script(), self.glossary()

        def produce_tracks(out: BinaryIO) -> None:
            with tempfile.TemporaryDirectory(dir=self.out_dir, prefix=".audio-") as tmp:
                tracks = _tracks_from(_load(script_path), self.deck_id)
                synthesize_tracks(tts(), tracks, language=deck.language, voice_id=voice, out_dir=Path(tmp))
                _pack(Path(tmp), out)

        stage = Stage("tracks", model=tts_model, params={"voice_id": voice})
        audio_dir = _deliver(
            self._cache.run(stage, script_path.read_bytes(), produce_tracks), self.out_dir / AUDIO_DIRNAME
        )

        glossary = _glossary_from(_load(glossary_path))
        if not glossary.entries:
            logger.info("glossary audio: 用語が0件なので書かない")
            shutil.rmtree(self.out_dir / GLOSSARY_DIRNAME, ignore_errors=True)
            return audio_dir

        def produce_glossary(out: BinaryIO) -> None:
            with tempfile.TemporaryDirectory(dir=self.out_dir, prefix=".glossary-") as tmp:
                written = write_glossary_audio(
                    tts(), glossary, deck_id=self.deck_id, language=deck.language, voice_id=voice, out_root=Path(tmp)
                )
                _pack(written.playlist.parent, out)

        stage = Stage("glossary-audio", model=tts_model, params={"voice_id": voice})
        _deliver(self._cache.run(stage, glossary_path.read_bytes(), produce_glossary), self.out_dir / GLOSSARY_DIRNAME)
        return audio_dir

    def review(self) -> Path | None:
        """学習記録から誤答率降順の復習系列を `out/<deck>/review/` に作る。誤答が無ければ `None`。"""
        store = DeckScopedStore(self.deck_id, self.root)
        ranks = record_and_rank(store, self.clusters(), store.attempts())
        if isinstance(ranks, NoTargets):
            logger.info("review: 誤答のあるクラスタが無い")
        return write_review_series(ranks, self.out_dir / AUDIO_DIRNAME, self.out_dir / REVIEW_DIRNAME)


def _default_tts(model_dir: str) -> Tts:
    # CosyVoice はホストにしか無い(ADR-012)。`--tts-model` を使う実行だけが読み込む。
    from quiz_sift.adapters.tts_cosyvoice import CosyVoiceTts

    return CosyVoiceTts(model_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quiz-sift", description="4択問題集を耳で学べる形に変換する")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="data/ と out/ を持つ作業ルート")
    stages = parser.add_subparsers(dest="stage", required=True, metavar="<段>")
    for name, text in (
        ("ingest", "取り込み"),
        ("cluster", "クラスタリング"),
        ("recall", "想起素材"),
        ("glossary", "グロッサリ"),
        ("script", "台本"),
        ("audio", "音声(台本とグロッサリ)"),
        ("review", "復習リスト"),
    ):
        stage = stages.add_parser(name, help=text)
        stage.add_argument("deck", help="data/<deck>/ のデッキ ID")
        if name in LLM_STAGES:
            sequence = stage.add_mutually_exclusive_group()
            sequence.add_argument(
                "--fresh-run",
                action="store_true",
                help="新しい実行の列を始め、段キャッシュを迂回する(run_id は stderr)",
            )
            sequence.add_argument("--resume", metavar="RUN_ID", help="resumable の実行の列に実行を1つ足す")
        if name == "audio":
            stage.add_argument("--tts-model", required=True, help="CosyVoice のモデルディレクトリ")
            stage.add_argument("--voice", help="ボイス(省略時はデッキの言語の既定)")
    serve = stages.add_parser("serve", help="ローカル HTTP API")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def _serve(root: Path, port: int) -> None:
    with make_server(root, port) as server:
        print(f"quiz-sift API: http://{HOST}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def _exit_code(error: BaseException) -> int:
    """`error` で抜けたときのプロセスの終了コード。"""
    if not isinstance(error, SystemExit):
        return 1
    if error.code is None:
        return 0
    return error.code if isinstance(error.code, int) else 1


def _failure_kind(error: LlmCallError) -> str:
    """ログに出す失敗の種別。例外文は子の出力から作った値を含みうるので出さない(NFR-2、ADR-024)。

    既定の `Llm` は種別を持つ `ContractError` を送出する。種別を持たない実装の例外は型名で表す。
    """
    return error.kind.value if isinstance(error, ContractError) else type(error).__name__


def _log_level() -> int:
    """`main` の間 root logger に設定するレベル。テストは差し替えて DEBUG のレコードも集める(AR-D6)。"""
    return logging.INFO


def _masked_excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """`main` の外へ抜けた例外のトレースバックを、認証情報を伏せて呼ばれた時点の `sys.stderr` へ書く(D-157)。

    ログを通らない経路なので `SecretMaskFilter` が効かない。直前のフックは呼ばない — 渡すと平文の例外
    オブジェクトが渡る。伏せるのに失敗したら型名だけを書く。フックが例外を出すとインタプリタが元の
    トレースバックを平文で書くので、外へは出さない。
    """
    try:
        text = mask_secrets("".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        text = f"{exc_type.__name__}: 認証情報を伏せられなかったのでトレースバックを書かない\n"
    sys.stderr.write(text)


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    raise ExecutionLimitExceeded(f"1回の実行が上限 {EXECUTION_LIMIT_SEC} 秒を超えた")


@contextlib.contextmanager
def _execution_limit() -> Iterator[None]:
    """`EXECUTION_LIMIT_SEC` 後に `ExecutionLimitExceeded` を上げる。子のグループは runner が killpg する(AR-149)。"""
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(EXECUTION_LIMIT_SEC)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


TERMINATION_SIGNALS = (signal.SIGHUP, signal.SIGTERM)


def _on_termination(signum: int, frame: FrameType | None) -> None:
    raise TerminatedBySignal(128 + signum)


@contextlib.contextmanager
def _terminate_children_on_signal() -> Iterator[None]:
    """SIGHUP / SIGTERM で `TerminatedBySignal` を上げる。実行中の子のグループは runner が killpg する(D-221)。

    子は別セッションで起動するので、端末を閉じても子には SIGHUP が届かない。親だけが死ぬと子が残り、
    すぐの `--resume` で2本の `claude` が同じ最小ホームを使う。
    """
    previous = {signum: signal.signal(signum, _on_termination) for signum in TERMINATION_SIGNALS}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _check_resumable(log: Path, run_id: str) -> None:
    """`run_id` の列が `resumable` でなければ `ResumeRefused`(D-145)。"""
    try:
        status = aggregate(read_log(log) if log.is_file() else [], run_id)["status"]
    except UnknownRunError as error:
        raise ResumeRefused(f"--resume {run_id}: 開始行の無い run_id") from error
    if status != "resumable":
        raise ResumeRefused(f"--resume {run_id}: 列の status が {status}(再開できるのは resumable だけ)")


def _register(args: argparse.Namespace, pipeline: Pipeline, run_id: str, cache_mode: str, preflight: Preflight) -> None:
    """起動前検査を通った LLM 段の実行を列へ登録する(開始行を書く)。検査に落ちた起動は登録しない(D-132)。"""
    if cache_mode == "resume":
        _check_resumable(pipeline.out_dir / METER_LOG, run_id)
    preflight()
    pipeline.meter = Meter(
        SystemClock(),
        log_path=pipeline.out_dir / METER_LOG,
        run_id=run_id,
        execution_id=uuid.uuid4().hex,
        stage=args.stage,
        cache_mode=cache_mode,
        env_names=os.environ.keys(),
    )
    print(f"run_id: {run_id}", file=sys.stderr, flush=True)


def _run(
    args: argparse.Namespace,
    handler: logging.Handler,
    llm_factory: LlmFactory,
    tts_factory: TtsFactory,
    preflight: Preflight,
) -> None:
    root: Path = args.root
    if args.stage == "serve":
        _serve(root, args.port)
        return
    llm_stage = args.stage in LLM_STAGES
    cache_mode = "normal"
    if llm_stage and args.fresh_run:
        cache_mode = "fresh"
    elif llm_stage and args.resume is not None:
        cache_mode = "resume"
    run_id: str = args.resume if cache_mode == "resume" else uuid.uuid4().hex
    pipeline = Pipeline(root, DeckId(args.deck), llm_factory, cache_run_id=None if cache_mode == "normal" else run_id)
    if llm_stage:
        _register(args, pipeline, run_id, cache_mode, preflight)
    handler.addFilter(pipeline.material_filter)
    exit_code: int = 0
    exit_reason: str | None = None
    try:
        with contextlib.ExitStack() as guards:
            if llm_stage:
                guards.enter_context(_terminate_children_on_signal())
                guards.enter_context(_execution_limit())
            result = _stage(args, pipeline, tts_factory)
    except BaseException as error:
        exit_code, exit_reason = _exit_code(error), type(error).__name__
        raise
    finally:
        handler.removeFilter(pipeline.material_filter)
        if pipeline.meter is not None:
            pipeline.meter.finish(exit_code=exit_code, exit_reason=exit_reason)
    if result is not None:
        print(result, flush=True)


def _stage(args: argparse.Namespace, pipeline: Pipeline, tts_factory: TtsFactory) -> Path | None:
    match args.stage:
        case "audio":
            model_dir: str = args.tts_model
            built: list[Tts] = []

            def tts() -> Tts:
                if not built:
                    built.append(tts_factory(model_dir))
                return built[0]

            return pipeline.audio(tts, model_dir, args.voice)
        case "review":
            return pipeline.review()
        case _:
            path: Path = getattr(pipeline, args.stage)()
            return path


def main(
    argv: Sequence[str] | None = None,
    *,
    llm_factory: LlmFactory = composition.build_default_llm,
    preflight: Preflight = claude_process.default_preflight,
    tts_factory: TtsFactory = _default_tts,
) -> int:
    """CLI を実行して終了コードを返す。段の検査に落ちた場合は `SystemExit` の派生が非0で抜ける。

    製品の入口はこれを `argv` だけで呼ぶ。`llm_factory` と `preflight` の差し替えはテストコードだけが行う(D-143)。
    """
    if sys.excepthook is not _masked_excepthook:
        sys.excepthook = _masked_excepthook
    args = _parser().parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskFilter())
    root_logger = logging.getLogger()
    level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(_log_level())
    try:
        _run(args, handler, llm_factory, tts_factory, preflight)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
