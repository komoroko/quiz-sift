"""cli を既定 LLM(`ClaudeCliLlm`)で通すテストの共通部品。spawn だけを fake にする(T-044、T-045)。

合成の3問デッキ(`ROWS` / `DRAFTS`)と、それに沿って応答する `respond`、`ClaudeCliLlm` をテストコードから直接構築する
`CliLlms`、音声の `StubTts` を置く。本文はすべて合成で、NFR-2 の対象外。
"""

import json
import math
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiz_sift.adapters.claude_process import Workspace
from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.recall import AnswerElement, DiscriminationPoint, RecallKey
from quiz_sift.ports.llm import Entailment, Llm, RecallDraft, TokenUsage
from quiz_sift.ports.metering import FailureKind, InvocationScope, RateLimitInfo, RunMeter
from quiz_sift.ports.process import CommandResult, CommandSpec
from quiz_sift.ports.tts import PcmAudio
from support.claude_fake import FakeRunner, classification_output, exited, fake_stdout

DECK = DeckId("aws-saa")
RATE = 16000

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


def _draft(qid: str, terms: tuple[str, ...], answer: str, discrimination: str, distractor: str) -> RecallDraft:
    """`answer` と `distractor` は選択肢のラベル。答えの要素は正解の選択肢の本文。"""
    (_, _, texts, _, _) = next(row for row in ROWS if row[0] == qid)

    def oid(label: str) -> OptionId:
        return OptionId(QuestionId(DECK, qid), label)

    return RecallDraft(
        RecallKey(terms),
        (AnswerElement(oid(answer), texts["ABCD".index(answer)]),),
        DiscriminationPoint(discrimination, (oid(distractor),)),
    )


DRAFTS = {
    "q01": _draft(
        "q01",
        ("アクセス頻度", "即座", "ストレージクラス"),
        "B",
        "標準-IA は低頻度向けでもミリ秒で取り出せるが、S3 Glacier Deep Archive は取り出しに数時間かかる",
        "C",
    ),
    "q03": _draft(
        "q03", ("明示的な拒否", "許可"), "B", "明示的な拒否は常に許可より優先され、書いた順序は関係しない", "C"
    ),
    "q05": _draft(
        "q05",
        ("プライマリ障害", "フェイルオーバー"),
        "A",
        "スタンバイへ自動で切り替わるのはマルチ AZ 配置で、リードレプリカは読み取りの負荷分散のためのもの",
        "B",
    ),
}


def recall_output(prompt: dict[str, Any]) -> dict[str, object]:
    """想起素材の呼び出し(`prompt` は子へ渡した payload)への、`DRAFTS` に沿った応答。

    選択肢は payload の不透明なキーで返す(D-206)。キーは選択肢の本文で引く。
    """
    question = prompt["question"]
    (qid,) = [qid for qid, stem, *_ in ROWS if stem == question["stem"]]
    (_, _, texts, _, _) = next(row for row in ROWS if row[0] == qid)
    keys = {option["text"]: option["key"] for option in question["options"]}

    def key(option_id: OptionId) -> str:
        return str(keys[texts["ABCD".index(option_id.local_id)]])

    draft = DRAFTS[qid]
    return {
        "terms": list(draft.key.terms),
        "answers": [{"option": key(e.option_id), "text": e.text} for e in draft.answers],
        "discrimination": {
            "text": draft.discrimination.text,
            "distractors": [key(o) for o in draft.discrimination.distractor_ids],
        },
    }


class CountingMeter:
    """`cli` が渡す `RunMeter` に委ね、`llm_calls` をメソッド名ごとに数える。"""

    def __init__(self, inner: RunMeter, methods: list[str]) -> None:
        self._inner = inner
        self._methods = methods

    def record(self, model: str, usage: TokenUsage) -> None:
        self._inner.record(model, usage)

    def begin_call(self, method: str) -> None:
        self._methods.append(method)
        self._inner.begin_call(method)

    def begin_invocation(self, argv_digest: str, *, reissue: bool = False) -> InvocationScope:
        return self._inner.begin_invocation(argv_digest, reissue=reissue)

    def end_invocation(
        self,
        model: str,
        turns: int | None,
        usage: TokenUsage | None,
        cost_usd: float | None,
        is_error: bool | None,
        skills_digest: str | None,
    ) -> None:
        self._inner.end_invocation(model, turns, usage, cost_usd, is_error, skills_digest)

    def record_quota(self, info: RateLimitInfo) -> None:
        self._inner.record_quota(info)

    def record_failure(self, kind: FailureKind) -> None:
        self._inner.record_failure(kind)


def flag(argv: Sequence[str], name: str) -> str:
    return argv[list(argv).index(name) + 1]


def respond(spec: CommandSpec, **stdout: Any) -> CommandResult:
    """ROWS / DRAFTS に沿った応答。分類は1問1グループ、要約は先頭の問題文、含意判定は常に consistent。

    `stdout` は `fake_stdout` へそのまま渡す置き換え(`is_using_overage` など)。
    """
    schema = json.loads(flag(spec.argv, "--json-schema"))
    prompt = json.loads(spec.stdin)
    properties = schema["properties"]
    output: dict[str, object]
    if "groups" in properties:
        output = classification_output([[q["key"]] for q in prompt["questions"]])
    elif "summary" in properties:
        output = {"summary": prompt["questions"][0]["stem"]}
    elif "terms" in properties:
        output = recall_output(prompt)
    elif "description" in properties:
        output = {"description": f"{prompt['term']}の説明"}
    else:
        output = {"verdict": Entailment.CONSISTENT.value}
    return exited(fake_stdout(structured_output=output, **stdout))


class CliLlms:
    """`llm_factory` として、テストコードから `ClaudeCliLlm` を直接構築する。spawn だけを fake にする。"""

    def __init__(self, tmp_path: Path, runner: FakeRunner) -> None:
        self.runner = runner
        self.methods: list[str] = []
        self.home = tmp_path / "min-home"
        self.cwd_root = tmp_path / "cwd-root"
        self.home.mkdir()
        self.cwd_root.mkdir()

    def __call__(self, meter: RunMeter) -> Llm:
        llm = ClaudeCliLlm(CountingMeter(meter, self.methods))
        llm.runner = self.runner
        llm.workspace = Workspace(self.home, self.cwd_root)
        return llm

    def systems(self) -> list[str]:
        """これまでに起動した子プロセスの `--system-prompt`(メソッドの別)。"""
        return [flag(spec.argv, "--system-prompt") for spec in self.runner.specs]


def write_root(root: Path) -> Path:
    """`root` に `.gitignore` と合成デッキ(`ROWS`)を書いて返す。"""
    (root / ".gitignore").write_text("data/\nout/\n", encoding="utf-8")
    deck_dir = root / "data" / DECK
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck.yaml").write_text(f"deck_id: {DECK}\ntitle: AWS SAA 模試\nlanguage: ja\n", encoding="utf-8")
    rows = [
        {
            "id": qid,
            "stem": stem,
            "options": [{"id": label, "text": text} for label, text in zip("ABCD", texts, strict=True)],
            "answers": [answer],
            "explanation": explanation,
        }
        for qid, stem, texts, answer, explanation in ROWS
    ]
    (deck_dir / "questions.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    return root


def no_preflight() -> None:
    """起動前検査の fake。何も検査しない(検査の中身は `Workspace` を直接走らせるテストが判定する)。"""


@dataclass
class StubTts:
    calls: int = 0

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        self.calls += 1
        n = round(0.5 * RATE)
        tone = array("h", (round(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n)))
        return PcmAudio(tone.tobytes(), RATE)
