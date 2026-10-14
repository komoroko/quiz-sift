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
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from quiz_sift.ports.metering import FailureKind
from quiz_sift.ports.process import CommandResult, CommandSpec, Outcome

SUCCESS_STDOUT = Path(__file__).resolve().parents[1] / "fixtures" / "claude_cli" / "success.stdout"

STREAM_EVENT_TYPES = frozenset({"system", "assistant", "user", "result", "rate_limit_event"})
"""stream-json のイベントの `type`。テストファイルにこれを持つリテラルがあれば走査で赤にする。"""

EXECUTABLE_DIR = Path(__file__).resolve().parent / "bin"
"""PATH に置く fake の `claude` 実行ファイルがあるディレクトリ。中身は `main` を呼ぶだけ。"""

PAYLOAD_KEYS = frozenset({"structured_output", "input"})
"""キー骨格の比較で中身を見ないキー(応答の本体とツール入力)。形が要求ごとに違って当然の値。"""

SCAN_EXCLUDED_DIRS = frozenset({"support", "measurements"})
"""走査から外す `tests/` 直下のディレクトリ(ファクトリ自身と、実物の採取経路)。"""

SENTINEL = "ZqxJ番兵Wv8k教材Pp3r"
"""子の本文に混ぜる番兵(C-014)。ログ・例外文・stderr に、これもその断片(`sentinel_fragments`)も現れてはならない。"""

_KEEP: Any = object()
"""骨格の値をそのまま使う印。"""


def sentinel_fragments(width: int = 4) -> list[str]:
    """番兵の断片: 幅 `width` の部分文字列すべて。ASCII だけの断片を含むので、`\\u` エスケープされた形も拾う。"""
    return [SENTINEL[i : i + width] for i in range(len(SENTINEL) - width + 1)]


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
    sync_result_text: bool = True,
) -> bytes:
    """成功フィクスチャの骨格から値だけを置き換えた stdout。これが唯一のファクトリである。

    - `structured_output` を置き換えると、`result` の文字列も同じ値の JSON に揃える(`sync_result_text` が偽なら
      揃えず、骨格の文字列のまま残す)。
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
        if sync_result_text:
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


def max_turns_stdout() -> bytes:
    """`error_max_turns` の形(D-118)。実物は採れないので、成功フィクスチャの骨格から `result` イベントの
    `subtype` / `is_error` / `stop_reason` / `structured_output` の4つの値だけを置き換える。
    """
    return fake_stdout(
        subtype="error_max_turns", is_error=True, stop_reason=None, structured_output=None, sync_result_text=False
    )


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
            return exited(max_turns_stdout(), 1)
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


def answer(argv: Sequence[str], stdin: str) -> bytes:
    """argv の `--json-schema` に合う応答の stdout。成功フィクスチャの骨格に値だけを入れる。

    分類(スキーマに `groups` がある)なら、stdin のプロンプトの `questions[].key` をそのまま1グループに入れて
    返す(渡したキーと同じ多重集合)。想起素材(`terms` がある)なら、渡した選択肢のキーで正解と誤答1件を指し、
    想起キーは問題文そのものにする(検査を通る形)。含意判定(`verdicts` がある)なら、渡した主張の ID ごとに
    consistent を返す。それ以外はスキーマの形を埋めた値を返す(文字列は合成の文、
    enum は先頭)。
    """
    schema = json.loads(argv[list(argv).index("--json-schema") + 1])
    if "groups" in schema["properties"]:
        keys = [question["key"] for question in json.loads(stdin)["questions"]]
        return fake_stdout(structured_output=classification_output([keys]))
    if "verdicts" in schema["properties"]:
        ids = [s["id"] for s in json.loads(stdin)["statements"]]
        return fake_stdout(structured_output={"verdicts": [{"id": i, "verdict": "consistent"} for i in ids]})
    if "terms" in schema["properties"]:
        question = json.loads(stdin)["question"]
        wrong = [o["key"] for o in question["options"] if o["key"] not in question["correct"]]
        recall = {
            "terms": [question["stem"]],
            "answers": [{"option": key, "text": "合成の応答"} for key in question["correct"]],
            "discrimination": {"text": "合成の応答", "distractors": wrong[:1]},
        }
        return fake_stdout(structured_output=recall)
    return fake_stdout(structured_output=_filled(schema))


def _filled(schema: Mapping[str, Any]) -> Any:
    match schema.get("type"):
        case "object":
            return {key: _filled(value) for key, value in schema["properties"].items()}
        case "array":
            return [_filled(schema["items"])]
        case _:
            return schema["enum"][0] if "enum" in schema else "合成の応答"


SENTINEL_MODE_FILE = "sentinel-mode"
"""子の HOME(最小ホーム)にこの名前のファイルがあれば、fake の `claude` は番兵の本文を返す(`sentinel_answer`)。

子へ渡る環境は HOME / PATH / LANG / TZ だけなので、切り替えは HOME の下のファイルで伝える。
"""


def main() -> None:
    """PATH に置く fake の `claude` の入口(`EXECUTABLE_DIR/claude`)。stdin を読み、応答を stdout へ書く。"""
    if (Path.home() / SENTINEL_MODE_FILE).exists():
        sentinel_answer()
    else:
        sys.stdout.buffer.write(answer(sys.argv, sys.stdin.read()))


def sentinel_answer() -> None:
    """番兵の本文を返す。含意判定には番兵を判定語として返し(未知の判定語)、それ以外は `answer` で応答する。
    stderr にはどの呼び出しでも番兵を書く。
    """
    argv, stdin = sys.argv, sys.stdin.read()
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    if "verdicts" in schema["properties"]:
        ids = [s["id"] for s in json.loads(stdin)["statements"]]
        sys.stdout.buffer.write(
            fake_stdout(structured_output={"verdicts": [{"id": i, "verdict": SENTINEL} for i in ids]})
        )
    else:
        sys.stdout.buffer.write(answer(argv, stdin))
    sys.stderr.write(f"{SENTINEL}\n")


class FakeRunner:
    """`CommandRunner` の fake。spawn だけを差し替え、受けた `CommandSpec` を順に残す。

    `respond` を渡さなければ `answer` で応答する。
    """

    def __init__(self, respond: Callable[[CommandSpec], CommandResult] | None = None) -> None:
        self.specs: list[CommandSpec] = []
        self._respond = respond if respond is not None else (lambda spec: exited(answer(spec.argv, spec.stdin)))

    def run(self, spec: CommandSpec) -> CommandResult:
        self.specs.append(spec)
        return self._respond(spec)


def skeleton(stdout: bytes) -> list[Any]:
    """イベント列のキー骨格。値を捨ててキーの入れ子だけを残す(`PAYLOAD_KEYS` の下は見ない)。"""
    return [_skeleton(json.loads(line), None) for line in stdout.splitlines()]


def _skeleton(value: Any, key: str | None) -> Any:
    if key in PAYLOAD_KEYS:
        return None
    if isinstance(value, dict):
        return {k: _skeleton(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_skeleton(v, None) for v in value]
    return None
