"""`claude` の stream-json 出力の単一のファクトリ(AR-79、D-118)。

テストが使う stream-json のイベント列は、すべてここで作る。T-041 が実 CLI から採った成功フィクスチャ
(`tests/fixtures/claude_cli/success.stdout`)を骨格とし、値だけを置き換える。イベントを落とすのは
`rate_limit_event` を落とした形・`result` の無い形など、形そのものが「無い」ことを表すときだけ。

テストファイルにイベント列を直接書くと骨格から外れた fake が混ざるので、`assert_no_inline_stream_json`
がそれを走査で拒む。
"""

import ast
import copy
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quiz_sift.ports.metering import FailureKind
from quiz_sift.ports.process import CommandResult, Outcome

SUCCESS_STDOUT = Path(__file__).resolve().parents[1] / "fixtures" / "claude_cli" / "success.stdout"

STREAM_EVENT_TYPES = frozenset({"system", "assistant", "user", "result", "rate_limit_event"})
"""stream-json のイベントの `type`。テストファイルにこれを持つリテラルがあれば走査で赤にする。"""

SCAN_EXCLUDED_DIRS = frozenset({"support", "measurements"})
"""走査から外す `tests/` 直下のディレクトリ(ファクトリ自身と、実物の採取経路)。"""

_KEEP: Any = object()
"""骨格の値をそのまま使う印。"""


def success_events() -> list[dict[str, Any]]:
    """成功フィクスチャのイベント列。呼ぶたびに新しい複製を返す。"""
    return [json.loads(line) for line in SUCCESS_STDOUT.read_bytes().splitlines()]


def classification_output(groups: Sequence[Sequence[str]], unassigned: Sequence[str] = ()) -> dict[str, Any]:
    """分類応答の `structured_output`。`groups` の各要素がそのグループの問題キー。"""
    return {
        "groups": [{"label": f"合成グループ{i}", "question_ids": list(keys)} for i, keys in enumerate(groups, 1)],
        "unassigned": list(unassigned),
    }


def fake_stdout(
    *,
    structured_output: Any = _KEEP,
    subtype: Any = _KEEP,
    is_error: Any = _KEEP,
    stop_reason: Any = _KEEP,
    assistant_stop_reason: Any = _KEEP,
    tools: Any = _KEEP,
    tool_use_name: Any = _KEEP,
    is_using_overage: Any = _KEEP,
    rate_limit_status: Any = _KEEP,
    drop_rate_limit_event: bool = False,
    later_rate_limits: Sequence[Mapping[str, Any]] = (),
    result_count: int = 1,
    junk_line: str | None = None,
) -> bytes:
    """成功フィクスチャの骨格から値だけを置き換えた stdout。これが唯一のファクトリである。

    - `structured_output` を置き換えると、`result` の文字列も同じ値の JSON に揃える。
    - `assistant_stop_reason` は `assistant` イベントすべての `message.stop_reason`。
    - `tool_use_name` は `assistant` イベントの `tool_use` ブロックの `name`。
    - `later_rate_limits` の各要素は、`rate_limit_event` の複製を直後に足し、その `rate_limit_info` の値を
      要素のとおりに置き換える(最初と最後が異なる列を作る)。
    - `result_count` は `result` イベントの個数(0 で落とし、2 で複製する)。
    - `junk_line` は `result` の直前(`result` を落としたときは末尾)に挟む JSON でない行。
    """
    events = success_events()
    result = _only(events, "result")
    init = _only(events, "system", subtype="init")
    rate_limit = _only(events, "rate_limit_event")
    assistants = [event for event in events if event["type"] == "assistant"]

    if structured_output is not _KEEP:
        result["structured_output"] = structured_output
        result["result"] = json.dumps(structured_output, ensure_ascii=False, separators=(",", ":"))
    _replace(result, "subtype", subtype)
    _replace(result, "is_error", is_error)
    _replace(result, "stop_reason", stop_reason)
    _replace(init, "tools", tools)
    _replace(rate_limit["rate_limit_info"], "isUsingOverage", is_using_overage)
    _replace(rate_limit["rate_limit_info"], "status", rate_limit_status)
    for assistant in assistants:
        _replace(assistant["message"], "stop_reason", assistant_stop_reason)
        for block in assistant["message"]["content"]:
            if block["type"] == "tool_use":
                _replace(block, "name", tool_use_name)

    position = events.index(rate_limit) + 1
    for updates in later_rate_limits:
        later = copy.deepcopy(rate_limit)
        for key, value in updates.items():
            _replace(later["rate_limit_info"], key, value)
        events.insert(position, later)
        position += 1
    if drop_rate_limit_event:
        events.remove(rate_limit)
    events.remove(result)
    lines = [json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events]
    if junk_line is not None:
        lines.append(junk_line)
    lines.extend(
        json.dumps(copy.deepcopy(result), ensure_ascii=False, separators=(",", ":")) for _ in range(result_count)
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def exited(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> CommandResult:
    """子が終了した結果。"""
    return CommandResult(Outcome.EXITED, returncode, stdout, stderr)


def timed_out(stdout: bytes) -> CommandResult:
    """タイムアウトした結果。`stdout` は途中まで(最後の行が途中で切れた形にする)。"""
    lines = stdout.splitlines(keepends=True)
    partial = b"".join(lines[:-1]) + lines[-1][: len(lines[-1]) // 2]
    return CommandResult(Outcome.TIMEOUT, None, partial, b"")


def spawn_failed() -> CommandResult:
    """起動に失敗した結果(`SubprocessRunner` が `OSError` を受けたときの形)。"""
    return CommandResult(Outcome.SPAWN, None, b"", b"[Errno 2] No such file or directory: 'claude'")


def failure_shape(kind: FailureKind, keys: Sequence[str]) -> CommandResult:
    """7種それぞれの失敗の形。`keys` は分類呼び出しに渡した問題キー。"""
    complete = classification_output([keys])
    match kind:
        case FailureKind.SPAWN:
            return spawn_failed()
        case FailureKind.TIMEOUT:
            return timed_out(fake_stdout(structured_output=complete))
        case FailureKind.PARSE:
            return exited(fake_stdout(result_count=0, junk_line="Error: stream ended unexpectedly"))
        case FailureKind.TRUNCATED:
            return exited(
                fake_stdout(subtype="error_max_turns", is_error=True, stop_reason=None, structured_output=None), 1
            )
        case FailureKind.PROVIDER_ERROR:
            return exited(fake_stdout(subtype="error_during_execution", is_error=True, structured_output=None), 1)
        case FailureKind.SCHEMA:
            return exited(fake_stdout(structured_output={"groups": "合成", "unassigned": []}))
        case FailureKind.INCOMPLETE_IDS:
            return exited(fake_stdout(structured_output=classification_output([keys[1:]])))


def assert_no_inline_stream_json(tests_root: Path) -> None:
    """`tests/` 配下の *.py(`support/` と `measurements/` を除く)に stream-json のイベントが直書きされていない。

    キー `"type"` の値が `STREAM_EVENT_TYPES` のどれかである dict リテラルと、その形を含む文字列リテラルを拒む。
    """
    types = "|".join(sorted(STREAM_EVENT_TYPES))
    pattern = re.compile(rf"""["']type["']\s*:\s*["'](?:{types})["']""")
    offenders: list[str] = []
    for path in sorted(tests_root.rglob("*.py")):
        relative = path.relative_to(tests_root)
        if relative.parts[0] in SCAN_EXCLUDED_DIRS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Dict) and any(
                _constant(key) == "type" and _constant(value) in STREAM_EVENT_TYPES
                for key, value in zip(node.keys, node.values, strict=True)
            ):
                offenders.append(f"{relative.as_posix()}:{node.lineno}: dict リテラル")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and pattern.search(node.value):
                offenders.append(f"{relative.as_posix()}:{node.lineno}: 文字列リテラル")
    assert not offenders, "stream-json のイベントは tests/support/claude_fake.py で作る:\n" + "\n".join(offenders)


def _constant(node: ast.expr | None) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _only(events: list[dict[str, Any]], kind: str, *, subtype: str | None = None) -> dict[str, Any]:
    matches = [e for e in events if e["type"] == kind and (subtype is None or e.get("subtype") == subtype)]
    assert len(matches) == 1, f"成功フィクスチャの {kind} イベントが {len(matches)} 件"
    return matches[0]


def _replace(target: dict[str, Any], key: str, value: Any) -> None:
    """骨格にあるキーの値だけを置き換える。キーを足したり消したりしない。"""
    if value is _KEEP:
        return
    assert key in target, f"骨格に無いキー {key!r} を置き換えようとした"
    target[key] = value
