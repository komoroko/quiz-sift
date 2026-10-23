"""G-1 の照合先の定数を、生成プロンプトと検査が共有する(C-004 / ADR-022 / A-2)。"""

import ast
from pathlib import Path

from quiz_sift.adapters import llm_prompts
from quiz_sift.adapters.llm_prompts import RECALL_SYSTEM, recall_payload, render_recall_system
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.recall import RecallKey
from quiz_sift.grounding import targets
from quiz_sift.grounding.targets import OTHER_CLUSTER_FIELDS, RECALL_KEY_FIELDS, SUMMARY_FIELDS, texts_for
from quiz_sift.recall.verify import missing_key_terms

from .conftest import question

SRC = Path(llm_prompts.__file__).resolve().parents[1]
CHECKS = ("recall/verify.py", "cluster/summary.py")
"""想起キー・他クラスタ出現率(recall/verify)とクラスタ要約(cluster/summary)の検査を持つモジュール。"""


def test_prompt_and_check_share_one_definition(deck: Deck) -> None:
    # (i) 生成プロンプトは照合先の定数から組み立てたもの。
    assert RECALL_KEY_FIELDS == ("stem",)
    assert RECALL_SYSTEM == render_recall_system(RECALL_KEY_FIELDS)
    assert "question.stem から連続した部分をそのまま写す" in RECALL_SYSTEM
    assert "question.options" not in RECALL_SYSTEM and "question.explanation" not in RECALL_SYSTEM
    # プロンプトが名指す場所は、子へ渡す payload に実在する。
    q01 = question(deck, "q01")
    payload, _ = recall_payload(q01)
    assert payload["stem"] == q01.stem

    # (ii) 問題文にだけある語は grounded、選択肢にだけある語は ungrounded。
    stem_only, option_only = "アクセス頻度", "Glacier"
    assert stem_only in q01.stem and not any(stem_only in o.text for o in q01.options)
    assert option_only not in q01.stem and any(option_only in o.text for o in q01.options)
    assert missing_key_terms(RecallKey((stem_only, option_only)), q01) == (option_only,)

    # (iii) 別の fields を渡すと、検査とプロンプトの両方が変わる。
    wider: tuple[targets.QuestionField, ...] = ("stem", "options")
    assert missing_key_terms(RecallKey((stem_only, option_only)), q01, wider) == ()
    prompt = render_recall_system(wider)
    assert prompt != RECALL_SYSTEM
    assert "question.stem または question.options[].text から連続した部分をそのまま写す" in prompt
    assert texts_for(q01, wider) == [q01.stem, *(o.text for o in q01.options)]

    # 他クラスタ出現率とクラスタ要約は問題文・選択肢・解説(D-198)。
    assert OTHER_CLUSTER_FIELDS == SUMMARY_FIELDS == ("stem", "options", "explanation")
    assert texts_for(q01, SUMMARY_FIELDS) == [q01.stem, *(o.text for o in q01.options), q01.explanation]


def _module(relative: str) -> ast.Module:
    return ast.parse((SRC / relative).read_text(encoding="utf-8"))


def test_checks_build_targets_only_from_the_constants() -> None:
    """3つの検査は `is_present` だけを呼び、照合先は `texts_for` と targets の定数からしか作らない。"""
    constants = {"RECALL_KEY_FIELDS", "OTHER_CLUSTER_FIELDS", "SUMMARY_FIELDS"}
    used: set[str] = set()
    for relative in CHECKS:
        tree = _module(relative)
        imported = {
            (node.module, alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert ("quiz_sift.grounding.presence", "is_present") in imported, relative
        # 照合先の本文を問題から直接取り出さない(選択肢は誤答の判定と丸写しの検査にだけ使う)。
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not attributes & {"stem", "explanation"}, relative
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "texts_for":
                field_arg = node.args[1]
                if isinstance(field_arg, ast.Name) and field_arg.id in constants:
                    used.add(field_arg.id)
                else:
                    # 想起キーの検査だけは、照合先を引数で受け取る(既定値が RECALL_KEY_FIELDS)。
                    assert isinstance(field_arg, ast.Name) and field_arg.id == "fields", relative
        # 重複していた照合先の組み立て(question_texts / _texts)は無い。
        names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert not names & {"question_texts", "_texts"}, relative
    assert used == {"OTHER_CLUSTER_FIELDS", "SUMMARY_FIELDS"}
    defaults = [
        d.id
        for node in ast.walk(_module("recall/verify.py"))
        if isinstance(node, ast.FunctionDef) and node.name == "missing_key_terms"
        for d in node.args.defaults
        if isinstance(d, ast.Name)
    ]
    assert defaults == ["RECALL_KEY_FIELDS"]
