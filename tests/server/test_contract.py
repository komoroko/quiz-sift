"""API 契約(R-12)。A-1: API が学習記録への唯一の書き込み経路。A-5: web/ は LLM SDK を import しない。"""

import re
import sqlite3
from pathlib import Path

from quiz_sift.store.location import store_path

from .conftest import DECK, QUESTIONS, SERVER_SRC, WEB_ROOT, Client

WEB_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
BROWSER_STORAGE = re.compile(r"\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b")
LLM_SDKS = re.compile(
    r"""(?:from\s+|import\s*\(?\s*|require\s*\(\s*)["'](?:@anthropic-ai/|anthropic|openai|@google/generative-ai"""
    r"""|@google/genai|@ai-sdk/|ai["'/]|@mistralai/|cohere-ai|ollama|groq-sdk)"""
)
LLM_PACKAGES = re.compile(r'"(?:@anthropic-ai/[^"]+|openai|@google/generative-ai|@google/genai|ai|@ai-sdk/[^"]+)"\s*:')


def _web_sources() -> list[Path]:
    skipped = {"node_modules", ".next", "out"}
    return [
        path
        for path in WEB_ROOT.rglob("*")
        if path.is_file() and path.suffix in WEB_SOURCE_SUFFIXES and not skipped & set(path.parts)
    ]


def _rows(root: Path) -> list[tuple[str, str, str, str, int]]:
    conn = sqlite3.connect(store_path(root))
    try:
        return conn.execute(
            "SELECT deck_id, session_id, question_id, chosen_option_id, correct FROM attempts ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


def _answer_all(client: Client, session: str, pick_correct: bool) -> None:
    base = f"/api/decks/{DECK}/sessions/{session}"
    for qid, answer in QUESTIONS.items():
        presented = client.json("GET", f"{base}/questions/{qid}")
        ids = [option["option_id"] for option in presented["options"]]
        assert sorted(ids) == ["A", "B", "C", "D"]
        chosen = answer if pick_correct else next(i for i in ids if i != answer)
        graded = client.json("POST", f"{base}/answers", {"question_id": qid, "chosen": chosen}, expect=201)
        assert graded == {"correct": pick_correct, "correct_option_id": answer, "explanation": f"解説 {qid}"}


def test_api_is_the_only_write_path(client: Client, root: Path) -> None:
    # UI 側: ブラウザ内ストレージへ書く経路を持たない。走査対象が空で素通りしないことも確かめる。
    sources = _web_sources()
    assert any(path.name == "api.ts" for path in sources)
    offenders = [str(path) for path in sources if BROWSER_STORAGE.search(path.read_text(encoding="utf-8"))]
    assert offenders == []

    # server 側: ストアの所在をハードコードせず、接続も自前で開かない(所在と形式は store/location.py の1箇所)。
    for path in SERVER_SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, path
        assert "learning.sqlite3" not in text, path

    # API 経由の回答が、CLI と同じ store_path の学習記録へ追記される。
    session = client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"]
    _answer_all(client, session, pick_correct=True)
    rows = _rows(root)
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [
        (DECK, session, qid, answer, 1) for qid, answer in QUESTIONS.items()
    ]

    # 同じ問題への2度目の回答は記録を増やさない(既存回答の上書き・重複の経路が無い)。
    response, _ = client.request(
        "POST", f"/api/decks/{DECK}/sessions/{session}/answers", {"question_id": "q1", "chosen": "B"}
    )
    assert response.status == 400
    assert len(_rows(root)) == len(QUESTIONS)


def test_session_submit_and_report(client: Client) -> None:
    pre = client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"]
    _answer_all(client, pre, pick_correct=False)
    post = client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"]
    assert post != pre
    _answer_all(client, post, pick_correct=True)

    assert client.json("GET", "/api/decks") == [{"deck_id": DECK, "title": "架空デッキ", "language": "ja"}]
    sessions = client.json("GET", f"/api/decks/{DECK}/sessions")
    assert sessions == [{"session_id": pre, "answered": 4}, {"session_id": post, "answered": 4}]
    detail = client.json("GET", f"/api/decks/{DECK}/sessions/{post}")
    assert detail["answered"] == list(QUESTIONS)

    report = client.json("GET", f"/api/decks/{DECK}/report?pre={pre}&post={post}")
    assert report["overall"]["pre"] == {"correct": 0, "answered": 4, "ratio": 0.0}
    assert report["overall"]["post"] == {"correct": 4, "answered": 4, "ratio": 1.0}
    assert report["overall"]["delta"] == 1.0
    assert report["limits"] and report["limits"] in report["markdown"]


def test_submit_accepts_only_an_option_id(client: Client) -> None:
    session = client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"]
    base = f"/api/decks/{DECK}/sessions/{session}"
    client.json("GET", f"{base}/questions/q1")
    # 表示位置(整数)では回答できない。
    response, _ = client.request("POST", f"{base}/answers", {"question_id": "q1", "chosen": 0})
    assert response.status == 400
    # application/json 以外の本文は受け取らない。
    response, _ = client.request(
        "POST", f"{base}/answers", "chosen=A", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status == 400


def test_unknown_deck_and_question_are_404(client: Client) -> None:
    assert client.request("GET", "/api/decks/no-such-deck")[0].status == 404
    session = client.json("POST", f"/api/decks/{DECK}/sessions", expect=201)["session_id"]
    assert client.request("GET", f"/api/decks/{DECK}/sessions/{session}/questions/q99")[0].status == 404


def test_web_does_not_import_llm_sdk() -> None:
    sources = _web_sources()
    assert sources
    offenders = [str(path) for path in sources if LLM_SDKS.search(path.read_text(encoding="utf-8"))]
    assert offenders == []
    manifest = (WEB_ROOT / "package.json").read_text(encoding="utf-8")
    assert LLM_PACKAGES.search(manifest) is None


def test_llm_sdk_pattern_catches_imports() -> None:
    # 上の検査が空振りしないことの対照。
    for line in (
        'import Anthropic from "@anthropic-ai/sdk";',
        "import OpenAI from 'openai';",
        'const { generateText } = require("ai");',
        'const m = await import("@google/genai");',
    ):
        assert LLM_SDKS.search(line), line
    assert LLM_PACKAGES.search('"@anthropic-ai/sdk": "1.0.0",')
    assert BROWSER_STORAGE.search("window.localStorage.setItem('a', 'b')")
