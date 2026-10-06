"""子 CLI の argv の受入テスト(T-060 / C-023・C-024 NFR-8)。

spawn だけを fake(`support.claude_fake.FakeRunner`)に差し替えて `ClaudeCliLlm` を全メソッドで呼び、子へ渡った
argv の形を確かめる。期待するフラグは T-054 がホストで書いた `docs/test/measurements/claude-cli-flags.md` の
「## 結論」から読み、製品の定数は読まない。
"""

import json
import re
from pathlib import Path

import yaml
from support.claude_fake import FakeRunner

from quiz_sift.adapters.claude_process import Workspace
from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.pipeline.meter import Meter
from quiz_sift.ports.clock import SystemClock

ROOT = Path(__file__).resolve().parents[2]
FLAGS_RECORD = ROOT / "docs" / "test" / "measurements" / "claude-cli-flags.md"
DECK = DeckId("deckH")


def conclusion() -> tuple[str, str, str]:
    """T-054 の結論から (ユーザ設定の遮断のフラグ, 許可リストのフラグ, その値)。"""
    tail = FLAGS_RECORD.read_text(encoding="utf-8").rpartition("\n## 結論\n")[2]
    settings = re.search(r"^ユーザ設定の遮断: あり\((--[\w-]+)\)$", tail, re.M)
    allowlist = re.search(r"^許可リスト: 一致あり\((--[\w-]+) (\"[^\"]*\")\)$", tail, re.M)
    assert settings and allowlist, tail
    return settings.group(1), allowlist.group(1), yaml.safe_load(allowlist.group(2))


def all_argvs(tmp_path: Path) -> list[tuple[str, ...]]:
    """5つのメソッドを1回ずつ呼び、子へ渡った argv を返す。"""
    runner = FakeRunner()
    meter = Meter(
        SystemClock(),
        log_path=tmp_path / "out" / "meter.jsonl",
        run_id="run-1",
        execution_id="exec-1",
        stage="cluster",
        cache_mode="fresh",
        env_names=["HOME", "LANG", "PATH", "TZ"],
    )
    llm = ClaudeCliLlm(meter)
    llm.runner = runner
    llm.workspace = Workspace(tmp_path / "min-home", tmp_path / "cwd-root")
    (tmp_path / "min-home").mkdir()
    (tmp_path / "cwd-root").mkdir()
    qid = QuestionId(DECK, "q1")
    q = Question(
        question_id=qid,
        stem="合成問題文",
        options=tuple(Option(OptionId(qid, label), f"合成選択肢 {label}") for label in "AB"),
        correct_option_ids=(OptionId(qid, "A"),),
        explanation="合成解説",
        studied=StudiedFlag.UNKNOWN,
    )
    members = Deck(DECK, Language.JA, None, (q,))
    llm.classify_clusters(members)
    llm.summarize_cluster(members)
    llm.generate_recall(q, Language.JA)
    llm.describe_term("合成用語", Language.JA)
    llm.check_entailment("合成参照", "合成主張")
    assert len(runner.specs) == 5
    return [spec.argv for spec in runner.specs]


def value_of(argv: tuple[str, ...], name: str) -> str:
    assert argv.count(name) == 1, (name, argv)
    return argv[argv.index(name) + 1]


def test_c023_argv_excludes_user_settings(tmp_path: Path) -> None:
    settings_flag, _, _ = conclusion()
    for argv in all_argvs(tmp_path):
        sources = [s for s in value_of(argv, settings_flag).split(",") if s]
        assert "user" not in sources, sources
        assert set(sources) <= {"project", "local"}, sources


def test_c024_argv_uses_allowlist_without_denylist(tmp_path: Path) -> None:
    _, allowlist_flag, allowlist_value = conclusion()
    for argv in all_argvs(tmp_path):
        assert value_of(argv, allowlist_flag) == allowlist_value
        # 拒否リスト(`--settings` による deny 列挙)も、権限の自動承認のフラグも無い(D-208)。
        assert "--settings" not in argv, argv
        assert not {"--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools"} & set(argv)
        for arg in argv:
            try:
                parsed = json.loads(arg)
            except ValueError:
                continue
            assert not (isinstance(parsed, dict) and "permissions" in parsed), arg
