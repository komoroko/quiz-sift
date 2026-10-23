"""`claude` の子プロセス1本の失敗分類(R-3、NFR-8 (h)、D-156)。

判定順序は 起動失敗(`SPAWN`)→ overage(`PROVIDER_ERROR`)→ タイムアウト(`TIMEOUT`)→ stdout が
`result` イベントをちょうど1つ含む JSON のイベント列にならない(`PARSE`)→ 許可集合外のツール
(`PROVIDER_ERROR`)→ 切り詰め(`TRUNCATED`)→ `is_error`(`PROVIDER_ERROR`)→ スキーマ不適合(`SCHEMA`)
→ ID 完全性(`INCOMPLETE_IDS`)。先に当たった種別で決まる。再試行の経路は持たない。

`PARSE` は stdout だけで決め、終了コードと stderr を使わない(D-119)。`stop_reason` / `is_error` /
`subtype` は `result` イベントからだけ読む(D-131。`assistant` メッセージにも `stop_reason` が載る)。

失敗の説明(`Judgement.detail`)には子の出力から作った値(ツール名・`subtype`・`stop_reason` など)を入れず、
種別と件数だけを書く(NFR-2、ADR-024)。説明は例外文としてログと stderr へ届く。

overage の走査はストリーム全体の厳格なパースと別に行う(D-134): stdout を行ごとに `json.loads` し、
読めない行は飛ばす。こうしないと `PARSE` / `TIMEOUT` で終わった呼び出しの overage が記録されない。
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from quiz_sift.adapters.llm_contract import ContractError
from quiz_sift.ports.metering import FailureKind, QuotaWindow, RateLimitInfo
from quiz_sift.ports.process import CommandResult, Outcome

ALLOWED_TOOLS = frozenset({"StructuredOutput"})
"""子プロセスに許す有効ツールの集合(NFR-8 (e))。`init.tools` はこれと一致しなければならない。"""

TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
"""切り詰めを表す `stop_reason`(D-102、D-120。Messages API の定義から取った値)。"""

TRUNCATED_SUBTYPE = "error_max_turns"


@dataclass(frozen=True, slots=True)
class RateLimitScan:
    """子1本の stdout に現れた `rate_limit_event` の要約(D-123、D-125)。

    `count` と `overage_seen` は `rate_limit_event` の行すべてから数える。`first` / `last` は
    `rate_limit_info` を `RateLimitInfo` として読めた行のうち最初と最後で、1件も無ければ `None`。
    """

    first: RateLimitInfo | None
    last: RateLimitInfo | None
    count: int
    overage_seen: bool
    """`isUsingOverage` が1回でも真だったか。"""


@dataclass(frozen=True, slots=True, repr=False)
class Judgement[T]:
    """子1本の判定。`failure` が `None` のときだけ `value` が応答を読んだ値を持つ。

    `result` / `init` は stdout を厳格に読めたときの `result` イベントと `init` イベント。どちらも
    教材本文を含みうるので repr を出さない(AR-17)。`detail` は子の出力から作った値を含まない理由の説明。
    """

    failure: FailureKind | None
    detail: str
    value: T | None
    result: Mapping[str, Any] | None
    init: Mapping[str, Any] | None
    rate_limits: RateLimitScan

    @property
    def result_is_error(self) -> bool | None:
        """`result` イベントの `is_error`。`result` が無いか真偽値でなければ `None`。"""
        if self.result is None:
            return None
        value = self.result.get("is_error")
        return value if isinstance(value, bool) else None


def scan_rate_limits(stdout: bytes) -> RateLimitScan:
    """stdout を行ごとに読み、`rate_limit_event` だけを拾う。読めない行は飛ばす。"""
    first: RateLimitInfo | None = None
    last: RateLimitInfo | None = None
    count = 0
    overage_seen = False
    for line in stdout.split(b"\n"):
        try:
            event = json.loads(line)
        except ValueError:
            # 途中で切れた行・JSON でない行。厳格な判定は `PARSE` 側が行う。
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue
        count += 1
        raw = event.get("rate_limit_info")
        if isinstance(raw, dict) and raw.get("isUsingOverage") is True:
            overage_seen = True
        info = _rate_limit_info(raw)
        if info is not None:
            first = info if first is None else first
            last = info
    return RateLimitScan(first=first, last=last, count=count, overage_seen=overage_seen)


def classify[T](result: CommandResult, parse: Callable[[Mapping[str, Any]], T]) -> Judgement[T]:
    """子1本の結果を判定順序どおりに分類する。

    `parse` は `structured_output` を読む契約のパーサ(`llm_contract`)で、`ContractError` の種別
    (`SCHEMA` / `INCOMPLETE_IDS`)がそのまま失敗の種別になる。
    """
    scan = scan_rate_limits(result.stdout)

    def fail(kind: FailureKind, detail: str, events: _Events | None = None) -> Judgement[T]:
        return Judgement(
            failure=kind,
            detail=detail,
            value=None,
            result=None if events is None else events.result,
            init=None if events is None else events.init,
            rate_limits=scan,
        )

    if result.outcome is Outcome.SPAWN:
        return fail(FailureKind.SPAWN, "子プロセスを起動できなかった")
    if scan.overage_seen:
        return fail(FailureKind.PROVIDER_ERROR, "isUsingOverage が真の rate_limit_event があった(D-125)")
    if result.outcome is Outcome.TIMEOUT:
        return fail(FailureKind.TIMEOUT, "子プロセスがタイムアウトした")
    parsed = _strict_events(result.stdout)
    if isinstance(parsed, str):
        return fail(FailureKind.PARSE, parsed)
    events = parsed
    tool_problem = _tool_problem(events)
    if tool_problem is not None:
        return fail(FailureKind.PROVIDER_ERROR, tool_problem, events)
    subtype = events.result.get("subtype")
    stop_reason = events.result.get("stop_reason")
    if subtype == TRUNCATED_SUBTYPE or stop_reason in TRUNCATED_STOP_REASONS:
        return fail(FailureKind.TRUNCATED, "応答が切り詰められた", events)
    if events.result.get("is_error") is not False:
        return fail(FailureKind.PROVIDER_ERROR, "result の is_error が偽でない", events)
    structured = events.result.get("structured_output")
    if not isinstance(structured, dict):
        return fail(FailureKind.SCHEMA, "result の structured_output がオブジェクトでない", events)
    try:
        value = parse(structured)
    except ContractError as exc:
        return fail(exc.kind, exc.detail, events)
    return Judgement(failure=None, detail="", value=value, result=events.result, init=events.init, rate_limits=scan)


@dataclass(frozen=True, slots=True, repr=False)
class _Events:
    events: list[dict[str, Any]]
    result: dict[str, Any]
    init: dict[str, Any] | None


def _strict_events(stdout: bytes) -> _Events | str:
    """stdout を `result` をちょうど1つ含む JSON のイベント列として読む。読めなければ理由を返す。"""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return "stdout が UTF-8 でない"
    # 行の区切りは改行だけ。`splitlines` は JSON 文字列の中に生で現れる U+2028 などでも割ってしまう。
    lines = text.removesuffix("\n").split("\n")
    if lines == [""]:
        return "stdout が空"
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except ValueError:
            return f"stdout の {number} 行目が JSON でない"
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return f"stdout の {number} 行目が type を持つイベントでない"
        events.append(event)
    results = [event for event in events if event["type"] == "result"]
    if len(results) != 1:
        return f"result イベントが {len(results)} 件(ちょうど1件でなければならない)"
    inits = [event for event in events if event["type"] == "system" and event.get("subtype") == "init"]
    return _Events(events=events, result=results[0], init=inits[0] if inits else None)


def _tool_problem(events: _Events) -> str | None:
    """許可集合外のツール(D-156)。`init.tools` の不一致と、途中に現れたツール使用の両方を見る。"""
    inits = [event for event in events.events if event["type"] == "system" and event.get("subtype") == "init"]
    if not inits:
        return "init イベントが無く、有効ツールの集合を確かめられない"
    mismatched = sum(1 for init in inits if not _allowed_tools(init.get("tools")))
    if mismatched:
        return f"init.tools が許可集合と一致しない init イベントが {mismatched} 件"
    used = 0
    for event in events.events:
        if event["type"] != "assistant":
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            # tool_use / server_tool_use / mcp_tool_use のどれでも、許可集合外なら失敗に倒す。
            if isinstance(kind, str) and kind.endswith("tool_use") and block.get("name") not in ALLOWED_TOOLS:
                used += 1
    return f"許可集合外のツール使用が {used} 件" if used else None


def _allowed_tools(tools: object) -> bool:
    return isinstance(tools, list) and all(isinstance(t, str) for t in tools) and set(tools) == ALLOWED_TOOLS


def _rate_limit_info(raw: object) -> RateLimitInfo | None:
    """`rate_limit_info` を生のまま写す。`status` が文字列でなければ読めないものとして `None`。

    値の型が合わない項目は `None` にする(`isUsingOverage` の `None` は「使っていない」ではない)。
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
        return None
    windows = raw.get("unifiedWindows")
    windows = windows if isinstance(windows, dict) else {}
    return RateLimitInfo(
        status=raw["status"],
        resets_at=_int(raw.get("resetsAt")),
        five_hour=_window(windows.get("five_hour")),
        seven_day=_window(windows.get("seven_day")),
        rate_limit_type=_str(raw.get("rateLimitType")),
        is_using_overage=raw.get("isUsingOverage") if isinstance(raw.get("isUsingOverage"), bool) else None,
        overage_status=_str(raw.get("overageStatus")),
    )


def _window(raw: object) -> QuotaWindow | None:
    if not isinstance(raw, dict):
        return None
    utilization = raw.get("utilization")
    resets_at = _int(raw.get("resetsAt"))
    if isinstance(utilization, bool) or not isinstance(utilization, int | float) or resets_at is None:
        return None
    return QuotaWindow(utilization=float(utilization), resets_at=resets_at)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None
