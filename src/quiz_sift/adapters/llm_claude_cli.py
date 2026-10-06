"""`ports.llm.Llm` の Claude Code CLI 実装(ADR-013、NFR-8 (c)(e)(h)、R-3、NFR-7)。

呼び出し1回 = `claude -p` の子プロセス1本。プロンプト本体は stdin だけで渡し、argv にもシステム
プロンプトにも教材本文を入れない(NFR-8 (e))。子の出力は次の順に扱う:

1. usage・num_turns・total_cost_usd・is_error・skills のダイジェストと、`rate_limit_event` ごとの枠を
   **成否の判定より先に** meter へ渡す(AR-65)。stdout を行ごとに読める範囲で拾い、厳格な判定を待たない。
2. `claude_failure.classify` で7種に分類し、失敗なら meter へ記録する。再試行はしない(D-101)。
3. `InvocationRecord` を1件ログへ出す。記録は構造化項目だけで、子の出力の本文を持たない(AR-146、ADR-024)。

`classify_clusters` は 25 問ごとに分割し、overage か枠切れ(`rate_limit_stop`)で失敗したバッチがあれば
残りのバッチを起動しない(D-144)。段をまたぐ停止は cli の責務(D-153)。起動前検査もここでは呼ばない(D-115)。
"""

import functools
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from quiz_sift.adapters import llm_prompts as prompts
from quiz_sift.adapters.claude_failure import Judgement, classify, scan_rate_limits
from quiz_sift.adapters.claude_process import SubprocessRunner, Workspace
from quiz_sift.adapters.llm_contract import (
    ContractError,
    parse_entailment,
    parse_proposals,
    parse_recall,
    parse_string,
)
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Question
from quiz_sift.pipeline.overhead import skills_digest
from quiz_sift.ports.clock import Clock, SystemClock
from quiz_sift.ports.llm import (
    CallFailure,
    ClassificationResult,
    Entailment,
    GroupProposal,
    RecallDraft,
    TokenUsage,
)
from quiz_sift.ports.metering import RateLimitInfo, RunMeter, rate_limit_stop
from quiz_sift.ports.process import CommandRunner, CommandSpec
from quiz_sift.safety.invocation_log import log_invocation, record_invocation
from quiz_sift.safety.log_filter import MaterialTextFilter

MAX_TURNS = 3
"""`--json-schema` の結果は `StructuredOutput` ツール経由で届くので、成功でも2ターンかかる(AR-57)。"""

SETTING_SOURCES = ""
"""`--setting-sources` の値。空にしてユーザ・プロジェクト・ローカルの設定ファイルを1つも読ませない(NFR-8、C-023)。"""

ENABLED_TOOLS = ""
"""`--tools` の値。空の許可リストでも `init.tools` は `{StructuredOutput}` になる(T-054 の実測。C-024)。

拒否リストは持たない(D-208)。有効ツールが `{StructuredOutput}` だけであることは、`init.tools` の実測(ホスト)と
`claude_failure` の判定(呼び出しごと)が確かめる。
"""

BODY_FLAGS = ("--json-schema", "--system-prompt")
"""値がタスク別の本文になるフラグ。argv のダイジェストはこの値を除いて取る(D-107、AR-144)。"""

_OMITTED = "<omitted>"


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def build_argv(model: str, system_prompt: str, schema: Mapping[str, object]) -> tuple[str, ...]:
    """設計 NFR-8 節 (e) の argv。教材本文は stdin で渡すので、ここには入らない。"""
    return (
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(MAX_TURNS),
        "--json-schema",
        _compact(schema),
        "--model",
        model,
        "--system-prompt",
        system_prompt,
        "--exclude-dynamic-system-prompt-sections",
        "--strict-mcp-config",
        "--mcp-config",
        _compact({"mcpServers": {}}),
        "--setting-sources",
        SETTING_SOURCES,
        "--tools",
        ENABLED_TOOLS,
        "--permission-mode",
        "default",
        "--safe-mode",
    )


def argv_digest(argv: Sequence[str]) -> str:
    """argv のダイジェスト。`BODY_FLAGS` の値だけを伏せ、モデルを含む残りすべてから取る。

    プロンプトとスキーマだけが違うメソッド同士(classify と summarize)は同じ値になり、オーバーヘッド定数の
    出所(T-046)と実行時の値を突き合わせられる。
    """
    masked = [_OMITTED if i > 0 and argv[i - 1] in BODY_FLAGS else arg for i, arg in enumerate(argv)]
    return hashlib.sha256(_compact(masked).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Envelope:
    """stdout から読める範囲で拾った、成否に関係なく meter へ渡す値。"""

    usage: TokenUsage | None
    turns: int | None
    cost_usd: float | None
    is_error: bool | None
    skills_digest: str | None
    tools: tuple[str, ...] | None
    quotas: tuple[RateLimitInfo, ...]


def _envelope(stdout: bytes) -> _Envelope:
    """行ごとに `json.loads` し、読めない行は飛ばす。`result` が複数あれば最後のもの、`init` は最初のもの。"""
    result: dict[str, Any] | None = None
    init: dict[str, Any] | None = None
    quotas: list[RateLimitInfo] = []
    for line in stdout.split(b"\n"):
        try:
            event = json.loads(line)
        except ValueError:
            # 途中で切れた行・JSON でない行。厳格な判定は `classify` が行う。
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "result":
            result = event
        elif kind == "system" and event.get("subtype") == "init" and init is None:
            init = event
        elif kind == "rate_limit_event":
            info = scan_rate_limits(line).first
            if info is not None:
                quotas.append(info)
    result = result or {}
    is_error = result.get("is_error")
    return _Envelope(
        usage=_usage(result.get("usage")),
        turns=_count(result.get("num_turns")),
        cost_usd=_cost(result.get("total_cost_usd")),
        is_error=is_error if isinstance(is_error, bool) else None,
        skills_digest=None if init is None else _skills(init),
        tools=None if init is None else _tools(init.get("tools")),
        quotas=tuple(quotas),
    )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(raw: object) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    counts = [
        _count(raw.get(name))
        for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    ]
    if any(count is None for count in counts):
        return None
    return TokenUsage(*(count for count in counts if count is not None))


def _cost(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _skills(init: Mapping[str, Any]) -> str | None:
    """`init.skills` のダイジェスト。キーが無ければ `"absent"` のダイジェスト、形が違えば `None`(D-141)。"""
    if "skills" not in init:
        return skills_digest(None)
    names = init["skills"]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        return None
    return skills_digest(names)


def _tools(raw: object) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        return None
    return tuple(raw)


def _error(judgement: Judgement[Any]) -> ContractError:
    """失敗の例外。説明は種別の値で始まり、子の出力から作った値を含まない(`Judgement.detail`)。"""
    assert judgement.failure is not None
    return ContractError(judgement.failure, judgement.detail)


class ClaudeCliLlm:
    """モデルはメソッドごとに `llm_prompts.MODELS` から選ぶ(ADR-015)。

    `runner` と `workspace` は本体で構築する(キーワード既定値にしない。AR-73)。構築はファイルシステムに
    何もしない(D-115)。ゲート内のテストは構築した後に `runner` を fake へ、`workspace` を `tmp_path` を
    根にした実物へ置き換える。
    """

    def __init__(self, meter: RunMeter) -> None:
        self._meter = meter
        self.runner: CommandRunner = SubprocessRunner()
        self.workspace = Workspace()
        self.clock: Clock = SystemClock()
        self._material = MaterialTextFilter()

    def _invoke[T](
        self,
        method: str,
        system: str,
        payload: object,
        schema: Mapping[str, object],
        parse: Callable[[Mapping[str, Any]], T],
    ) -> tuple[Judgement[T], bool | None]:
        """子プロセス1本。判定と、meter へ渡した `is_error` を返す。

        stdin に載せる問題は、呼び出し側が先に `_material` へ登録しておく(記録の argv と init.tools で伏せる)。
        """
        model = prompts.MODELS[method]
        argv = build_argv(model, system, schema)
        stdin = json.dumps(payload, ensure_ascii=False)
        self._meter.begin_invocation(argv_digest(argv))
        with self.workspace.new_cwd() as cwd:
            spec = CommandSpec(argv=argv, env=self.workspace.child_env(), cwd=cwd, stdin=stdin)
            started = self.clock.monotonic()
            result = self.runner.run(spec)
            duration = self.clock.monotonic() - started
        envelope = _envelope(result.stdout)
        # 失敗した呼び出しも枠とトークンを消費しているので、判定より先に渡す(AR-65)。
        self._meter.end_invocation(
            model, envelope.turns, envelope.usage, envelope.cost_usd, envelope.is_error, envelope.skills_digest
        )
        for info in envelope.quotas:
            self._meter.record_quota(info)
        judgement = classify(result, parse)
        if judgement.failure is not None:
            self._meter.record_failure(judgement.failure)
        log_invocation(
            record_invocation(
                argv=argv,
                exit_code=result.returncode,
                duration_sec=duration,
                usage=envelope.usage,
                failure_kind=judgement.failure,
                result_is_error=envelope.is_error,
                rate_limit_first=judgement.rate_limits.first,
                rate_limit_last=judgement.rate_limits.last,
                rate_limit_event_count=judgement.rate_limits.count,
                overage_seen=judgement.rate_limits.overage_seen,
                stdin=stdin,
                init_tools=envelope.tools,
                material=self._material,
            )
        )
        return judgement, envelope.is_error

    def _call[T](
        self,
        method: str,
        system: str,
        payload: object,
        schema: Mapping[str, object],
        parse: Callable[[Mapping[str, Any]], T],
    ) -> T:
        """1回の Protocol 呼び出し = 子プロセス1本。失敗は種別を持つ `ContractError` にする。"""
        self._meter.begin_call(method)
        judgement, _ = self._invoke(method, system, payload, schema, parse)
        if judgement.failure is not None:
            raise _error(judgement)
        assert judgement.value is not None
        return judgement.value

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        self._meter.begin_call("classify_clusters")
        for question in deck.questions:
            self._material.register_question(question)
        groups: list[GroupProposal] = []
        failures: list[CallFailure] = []
        for start in range(0, len(deck.questions), prompts.CLASSIFY_BATCH_SIZE):
            batch = deck.questions[start : start + prompts.CLASSIFY_BATCH_SIZE]
            # 応答のキーは入力のバッチへ写し戻すので、バッチ内の通し番号で渡す。
            keyed = {str(i): q for i, q in enumerate(batch)}
            payload = {
                "existing_labels": sorted({g.label for g in groups}),
                "questions": [{"key": k, **prompts.render_question(q)} for k, q in keyed.items()],
            }
            judgement, is_error = self._invoke(
                "classify_clusters",
                prompts.CLASSIFY_SYSTEM,
                payload,
                prompts.CLASSIFY_SCHEMA,
                functools.partial(parse_proposals, keyed=keyed),
            )
            if judgement.failure is None:
                assert judgement.value is not None
                groups.extend(judgement.value)
                continue
            failures.append(CallFailure(tuple(q.question_id for q in batch), str(_error(judgement))))
            # overage か枠切れなら、残りのバッチも同じ理由で落ちる。起動せずに返す(D-144)。
            if judgement.rate_limits.overage_seen or rate_limit_stop(
                judgement.failure, is_error, judgement.rate_limits.last
            ):
                break
        return ClassificationResult(assignments=tuple(groups), failures=tuple(failures))

    def summarize_cluster(self, members: Deck) -> str:
        for question in members.questions:
            self._material.register_question(question)
        payload = {
            "language": prompts.LANGUAGE_NAMES[members.language],
            "questions": [prompts.render_question(q) for q in members.questions],
        }
        return self._call(
            "summarize_cluster",
            prompts.SUMMARIZE_SYSTEM,
            payload,
            prompts.SUMMARIZE_SCHEMA,
            functools.partial(parse_string, key="summary"),
        )

    def generate_recall(self, question: Question, language: Language) -> RecallDraft:
        self._material.register_question(question)
        payload = {"language": prompts.LANGUAGE_NAMES[language], "question": prompts.render_question(question)}
        return self._call("generate_recall", prompts.RECALL_SYSTEM, payload, prompts.RECALL_SCHEMA, parse_recall)

    def describe_term(self, term: str, language: Language) -> str:
        payload = {"language": prompts.LANGUAGE_NAMES[language], "term": term}
        return self._call(
            "describe_term",
            prompts.DESCRIBE_SYSTEM,
            payload,
            prompts.DESCRIBE_SCHEMA,
            functools.partial(parse_string, key="description"),
        )

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        payload = {"reference": reference, "statement": statement}
        return self._call(
            "check_entailment", prompts.ENTAILMENT_SYSTEM, payload, prompts.ENTAILMENT_SCHEMA, parse_entailment
        )
