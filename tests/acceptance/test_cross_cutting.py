"""教材・認証情報の保護、段階的再実行、開発基盤の受入テスト(C-014〜C-018)。claim ごとに1本。

`cli.main` を通すものは conftest の実行ヘルパ越しに走らせる。本文と認証情報はすべて合成で、
実教材も本物の資格情報も含まない(NFR-2 / NFR-3)。合成の値には `Synthetic` を入れてあり、
追跡ファイルの走査はそれを印にして合成と本物を分ける。
"""

import io
import ipaddress
import logging
import os
import re
import shutil
import socket
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId
from quiz_sift.domain.recall import RecallKey
from quiz_sift.ports.llm import ClassificationResult, RecallDraft
from quiz_sift.safety.gitignore_guard import IgnoreRule, is_ignored, parse_gitignore
from quiz_sift.safety.secret_mask import SECRET_MASK

from .conftest import CliRunner, StubLlm, StubTts, write_deck

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = 3

logger = logging.getLogger("quiz_sift.adapters.synthetic_stub")


def _qid(n: int) -> str:
    return f"q{n}"


def _row(n: int) -> dict[str, Any]:
    return {
        "id": _qid(n),
        "stem": f"合成問題文{n}: 可用性ゾーン{n}をまたぐ構成はどれか。",
        "options": [{"id": label, "text": f"合成選択肢{n}{label}番"} for label in "ABCD"],
        "answer": "A",
        "explanation": f"合成公式解説{n}: ゾーン{n}の複数に配置する。",
    }


ROWS = [_row(n) for n in range(1, QUESTIONS + 1)]
MATERIAL = tuple(
    text for row in ROWS for text in (row["stem"], row["explanation"], *(o["text"] for o in row["options"]))
)
DRAFTS = {
    _qid(n): RecallDraft(RecallKey((f"ゾーン{n}",)), f"合成選択肢{n}A番", f"ゾーン{n}には先頭が結びつく")
    for n in range(1, QUESTIONS + 1)
}


def _write_deck(workspace: Path, name: str) -> DeckId:
    deck_id = DeckId(name)
    write_deck(workspace, deck_id, ROWS)
    return deck_id


def _capture_stderr(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """`cli.main` のログハンドラが書く先。ハンドラは起動時の `sys.stderr` に向くので、差し替えて読む。

    capsys は各フェーズの開始で `sys.stderr` を差し直すので、fixture ではなくテスト本体から呼ぶ。
    """
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    return stream


# ---- C-014 ----


@dataclass
class MaterialLoggingLlm(StubLlm):
    """受け取った教材本文をそのままログへ書く adapter の代役。書いたことは `logged` に残る。"""

    logged: list[str] = field(default_factory=list)

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        for question in deck.questions:
            logger.warning("分類の入力: %s / 選択肢 %r", question.stem, [o.text for o in question.options])
            self.logged.append(question.stem)
        return super().classify_clusters(deck)

    def summarize_cluster(self, members: Deck) -> str:
        question = members.questions[0]
        logger.info(f"要約の入力: {question.stem} 解説={question.explanation!r}")
        self.logged.append(question.stem)
        return super().summarize_cluster(members)


def _files_under(root: Path) -> Iterator[tuple[str, bytes]]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path.relative_to(root).as_posix(), path.read_bytes()


def test_c014_material_text_never_reaches_tracked_files_or_logs(
    workspace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _capture_stderr(monkeypatch)
    # 相対パスへの書き出しも作業ルートに落ちるようにして、走査から漏れないようにする。
    monkeypatch.chdir(workspace)
    deck_id = _write_deck(workspace, "deck-c014")
    llm = MaterialLoggingLlm(drafts=DRAFTS)
    run = CliRunner(workspace, capsys, llm, StubTts())
    for stage in ("ingest", "cluster", "script"):
        run(stage, deck_id)

    log = stderr.getvalue()
    # ログは実際に取れていて、本文を渡されたログ呼び出しも実際に走っている。
    assert "ingest: input=3 accepted=3" in log
    assert "分類の入力" in log and "要約の入力" in log
    assert len(llm.logged) >= QUESTIONS
    for text in MATERIAL:
        assert text not in log, text

    rules = parse_gitignore((workspace / ".gitignore").read_text(encoding="utf-8"))
    carrying = {rel for rel, data in _files_under(workspace) if any(t.encode() in data for t in MATERIAL)}
    # 本文は書かれている(走査が空振りしていない)。書かれた先はすべて .gitignore の対象。
    assert any(rel.startswith(f"out/{deck_id}/") for rel in carrying)
    assert sorted(rel for rel in carrying if not is_ignored(rel, rules)) == []


# ---- C-015 ----

API_KEY = "sk-ant-api03-SyntheticDummyKey_C015_0123456789-abcdef"
PASSWORD = "SyntheticUdemyPassw0rd!"
SESSION = "SyntheticDjSession0123456789abcdef"
UD_USER = "SyntheticUdCacheUserC015"
JWT = "SyntheticJwtC015.SyntheticBody.Sig"
SECRETS = (API_KEY, PASSWORD, SESSION, UD_USER, JWT)


@dataclass
class CredentialLoggingLlm(StubLlm):
    """認証情報を含む要求・応答・例外をログへ書く adapter の代役。"""

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        key = os.environ["ANTHROPIC_API_KEY"]
        logger.info("request headers %s", {"x-api-key": key, "content-type": "application/json"})
        logger.info("Cookie: ud_cache_user=%s; dj_session_id=%s", UD_USER, SESSION)
        logger.info("cookies %s", [{"name": "ud_user_jwt", "value": JWT, "domain": ".udemy.com"}])
        logger.info("login form %s", {"email": "synthetic@example.invalid", "password": PASSWORD})
        try:
            raise ConnectionError(f"認証に失敗 api_key={key} password={PASSWORD!r} sessionid={SESSION}")
        except ConnectionError:
            logger.exception("上流の呼び出しに失敗した")
        return super().classify_clusters(deck)


# 追跡されうるファイルに平文で置かれた認証情報の形。値に `Synthetic` を含むものは合成のダミー。
_CREDENTIAL_SHAPES = (
    re.compile(r"sk-ant-[a-z]+\d*-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?im)^\s*(?:set-)?cookie:\s*[\w.-]+=([^\s;%{]{12,})"),
    re.compile(
        r"""(?i)\b[\w.-]*(?:password|passwd|sessionid|dj_session_id|access_token|ud_user_jwt|csrftoken)"""
        r"""["']?\s*[=:]\s*["']([^"'\s%{}]{8,})["']"""
    ),
)
# .env / ini 形式の引用符なし `NAME=value`。Python のキーワード引数と区別できないので `.py` には当てない。
_UNQUOTED_SHAPE = re.compile(
    r"""(?im)^\s*(?:export\s+)?[\w.]*(?:password|passwd|session_?id|access_token|jwt)"""
    r"""\s*=\s*([^\s"'$%{}]{8,})\s*$"""
)


def _credential_shapes(text: str, *, python: bool = False) -> list[str]:
    shapes = _CREDENTIAL_SHAPES if python else (*_CREDENTIAL_SHAPES, _UNQUOTED_SHAPE)
    return [m.group(0) for p in shapes for m in p.finditer(text) if "Synthetic" not in m.group(0)]


def _trackable_files(root: Path) -> Iterator[tuple[str, bytes]]:
    """`.gitignore` の対象でない(git が追跡しうる)ファイル。`.git` の中身は除く。

    走査の途中で見つけた各ディレクトリの `.gitignore` を、そのディレクトリからの相対パスで適用する。
    `is_ignored` は1ファイル分の規則しか評価せず、階層をまたぐ否定の優先順位を表せない。
    ルート以外の `.gitignore` に否定の行があれば、無視しすぎの向きに誤らないよう失敗させる(AR-175)。
    """
    layers: dict[Path, list[tuple[Path, list[IgnoreRule]]]] = {}

    def ignored(rel: Path, own: list[tuple[Path, list[IgnoreRule]]], *, is_dir: bool = False) -> bool:
        return any(is_ignored(rel.relative_to(at).as_posix(), rules, is_dir=is_dir) for at, rules in own)

    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).relative_to(root)
        own = list(layers.pop(base, []))
        gitignore = Path(dirpath) / ".gitignore"
        if ".gitignore" in filenames and not gitignore.is_symlink():
            rules = parse_gitignore(gitignore.read_text(encoding="utf-8"))
            if base != Path() and any(rule.negated for rule in rules):
                raise ValueError(f"入れ子の .gitignore の否定は評価できない: {gitignore}")
            own.append((base, rules))
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not ignored(base / d, own, is_dir=True))
        for d in dirnames:
            layers[base / d] = own
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name != ".git" and not path.is_symlink() and not ignored(base / name, own):
                yield (base / name).as_posix(), path.read_bytes()


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_trackable_files_honor_nested_gitignore(tmp_path: Path) -> None:
    # 認証情報の形をした値は、このファイル自身に平文で残らないよう実行時に組み立てる。
    secret = "key = 'sk-ant-api03-" + "RealLookingKey0123456789abcd'"
    _write_tree(
        tmp_path,
        {
            ".gitignore": "*.log\n",
            "top.txt": "",
            "top.log": "",
            "node_modules/x.js": "",
            "sub/.gitignore": "node_modules/\n/build/\n",
            "sub/keep.txt": "",
            "sub/node_modules/pkg.js": secret,
            "sub/build/x": "",
            "sub/deeper/build/x": "",
            "sub/a/.gitignore": "*.tmp\n",
            "sub/a/b.tmp": "",
            "sub/a/c/d.tmp": "",
            "sub/e.tmp": "",
            "f.tmp": "",
        },
    )

    assert {rel for rel, _ in _trackable_files(tmp_path)} == {
        ".gitignore",
        "top.txt",
        "node_modules/x.js",
        "sub/.gitignore",
        "sub/keep.txt",
        "sub/deeper/build/x",
        "sub/a/.gitignore",
        "sub/e.tmp",
        "f.tmp",
    }


def test_trackable_files_refuse_nested_negation(tmp_path: Path) -> None:
    _write_tree(tmp_path, {".gitignore": "*.log\n!keep.log\n", "sub/.gitignore": "*.log\n!keep.log\n"})

    with pytest.raises(ValueError, match=re.escape(str(tmp_path / "sub" / ".gitignore"))):
        list(_trackable_files(tmp_path))


def test_c015_credentials_never_reach_logs_or_errors(
    workspace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = _capture_stderr(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    deck_id = _write_deck(workspace, "deck-c015")
    run = CliRunner(workspace, capsys, CredentialLoggingLlm(drafts=DRAFTS), StubTts())
    run("cluster", deck_id)

    log = stderr.getvalue()
    assert "上流の呼び出しに失敗した" in log and "ConnectionError" in log
    assert "application/json" in log and ".udemy.com" in log
    assert SECRET_MASK in log
    for secret in SECRETS:
        assert secret not in log, secret

    # ログを通らずに `cli.main` の外へ抜けた例外は、プロセスの終わりにインタプリタが
    # `sys.excepthook` で stderr へ書く。そのトレースバックにも平文で出ない。
    stderr = _capture_stderr(monkeypatch)
    raising = CliRunner(workspace, capsys, CredentialRaisingLlm(drafts=DRAFTS), StubTts())
    with pytest.raises(ConnectionError) as raised:
        raising("cluster", _write_deck(workspace, "deck-c015-raise"))
    sys.excepthook(raised.type, raised.value, raised.tb)
    shown = stderr.getvalue()
    assert "ConnectionError" in shown and "認証に失敗" in shown
    for secret in (API_KEY, PASSWORD, SESSION, JWT):
        assert secret not in shown, secret

    # 検出器はダミーでない値を拾う(走査が空振りしていないことの対照)。値はこのファイル自身に
    # 平文で残らないよう、実行時に組み立てる。
    assert _credential_shapes("key = 'sk-ant-api03-" + "RealLookingKey0123456789abcd'")
    assert _credential_shapes("Cookie: dj_session_id=abcdef0123456789abcdef")
    assert _credential_shapes('{"password' + '": "hunter2hunter2"}')
    assert _credential_shapes("UDEMY_PASSWORD" + "=hunter2hunter2")
    assert _credential_shapes("DJ_SESSION_ID" + "=abcdef0123456789")
    assert not _credential_shapes(f"key = '{API_KEY}'")
    assert not _credential_shapes("    session_id=session_id,", python=True)

    found = {
        rel: hits
        for rel, data in _trackable_files(ROOT)
        if (hits := _credential_shapes(data.decode("utf-8", "replace"), python=rel.endswith(".py")))
    }
    assert found == {}


@dataclass
class CredentialRaisingLlm(StubLlm):
    """認証情報を載せた例外を送出する adapter の代役。例外はログを通らず `cli.main` の外へ抜ける。"""

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        key = os.environ["ANTHROPIC_API_KEY"]
        raise ConnectionError(
            f"認証に失敗 api_key={key} password={PASSWORD!r} Cookie: dj_session_id={SESSION}; ud_user_jwt={JWT}"
        )


# ---- C-016 ----


def _stage_files(out_dir: Path, stage: str) -> list[str]:
    return sorted(p.name for p in (out_dir / stage).iterdir() if not p.name.startswith("."))


def test_c016_rerun_from_a_middle_stage_calls_nothing_upstream(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deck_id = _write_deck(workspace, "deck-c016")
    llm, tts = StubLlm(drafts=DRAFTS), StubTts()
    run = CliRunner(workspace, capsys, llm, tts)
    out_dir = workspace / "out" / deck_id

    run("audio", deck_id)
    assert {"classify", "summarize", "recall"} <= set(llm.calls)
    assert tts.inputs
    # 中間成果物は段ごとに1つずつ、digest 名で永続化されている。
    for stage in ("ingest", "cluster", "recall", "glossary-terms", "script", "tracks"):
        assert len(_stage_files(out_dir, stage)) == 1, stage

    # 途中の段(想起素材)の成果物を消して台本から起動する: 前段(取り込み・クラスタリング)の呼び出しは 0。
    shutil.rmtree(out_dir / "recall")
    llm.calls.clear()
    run("script", deck_id)
    assert "classify" not in llm.calls and "summarize" not in llm.calls and "describe" not in llm.calls
    assert llm.calls.count("recall") == QUESTIONS
    assert len(_stage_files(out_dir, "recall")) == 1

    # 音声の段だけを作り直す: LLM の段はどれも呼ばれず、TTS だけが呼ばれる。
    shutil.rmtree(out_dir / "tracks")
    llm.calls.clear()
    tts.inputs.clear()
    run("audio", deck_id)
    assert llm.calls == []
    assert tts.inputs

    # 何も消さずに再実行すると、LLM も TTS も呼ばれない。
    tts.inputs.clear()
    run("audio", deck_id)
    assert llm.calls == [] and tts.inputs == []


# ---- C-017 ----

_GATE_RUN = re.compile(r"(?:uv run )?bash gate\.sh (\w+)")


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _gate_command_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in config["quality_gate"] if step["kind"] == "command"]


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _outside_repo(value: str) -> list[str]:
    """設定値の中の、リポジトリの外を指すパス。URL はパスではないので見ない。"""
    outside = []
    for token in re.split(r"[\s=,;]+", value):
        if not token or re.match(r"^[a-z][a-z0-9+.-]*://", token):
            continue
        if token.startswith(("/", "~", "$HOME", "${HOME}")) or re.match(r"^[A-Za-z]:[\\/]", token):
            outside.append(token)
        elif ".." in Path(token).parts and not (ROOT / token).resolve().is_relative_to(ROOT):
            outside.append(token)
    return outside


def test_c017_ci_and_gate_share_steps_and_imported_config_is_local() -> None:
    config = _yaml(ROOT / ".rein" / "config.yaml")
    gate_steps = set()
    for step in _gate_command_steps(config):
        command = step["command"]
        assert command[:2] == ["bash", "gate.sh"] and len(command) == 3, command
        gate_steps.add(command[2])

    workflows = [_yaml(path) for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
    runs = [step["run"] for wf in workflows for job in wf["jobs"].values() for step in job["steps"] if "run" in step]
    checks = [run for run in runs if "gate.sh" in run]
    # 検査の run ステップはどれも `bash gate.sh <step>` の形で、1ステップ1検査。
    ci_steps = set()
    for run in checks:
        match = _GATE_RUN.fullmatch(run.strip())
        assert match, run
        ci_steps.add(match.group(1))
    assert gate_steps and ci_steps == gate_steps
    assert len(checks) == len(ci_steps)

    # 取り込んだ設定がリポジトリの外を参照しない。検出器が外部パスを拾うことも同じ場で確かめる。
    assert _outside_repo("paths: /home/someone/src ~/x ../../elsewhere C:\\tools")
    assert _outside_repo("--config-file=/etc/mypy.ini")
    assert not _outside_repo("https://github.com/pre-commit/pre-commit-hooks src docs ./tests")
    imported = {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")),
        "mkdocs.yml": _yaml(ROOT / "mkdocs.yml"),
        ".pre-commit-config.yaml": _yaml(ROOT / ".pre-commit-config.yaml"),
    }
    outside = {
        name: hits for name, data in imported.items() if (hits := [h for s in _strings(data) for h in _outside_repo(s)])
    }
    assert outside == {}


# ---- C-018 ----


class NetworkBlocked(OSError):
    """受入テストが外向きの接続を試みた。"""


def _is_loopback(address: object) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX のパス。ホストの外へは出ない。
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """socket の外向き接続(connect / connect_ex / create_connection)を例外にする。ループバックは通す。

    遮断した接続先を返すリストに記録する。製品コードが OSError を捕まえて握りつぶしても、
    試みたこと自体はリストに残る。
    """
    blocked: list[object] = []
    connect, connect_ex, create_connection = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if not _is_loopback(address):
            blocked.append(address)
            raise NetworkBlocked(f"外向きの接続: {address!r}")
        connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_loopback(address):
            blocked.append(address)
            raise NetworkBlocked(f"外向きの接続: {address!r}")
        return connect_ex(self, address)

    def guarded_create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        if not _is_loopback(address):
            blocked.append(address)
            raise NetworkBlocked(f"外向きの接続: {address!r}")
        return create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    return blocked


def _dial_out() -> None:
    # 192.0.2.0/24 は文書用のアドレス(RFC 5737)。
    socket.create_connection(("192.0.2.1", 443), timeout=0.1).close()


def test_c018_gate_steps_need_no_network(no_network: list[object]) -> None:
    config = _yaml(ROOT / ".rein" / "config.yaml")
    default = config["executors"]["quality_gate_profile"]
    for step in _gate_command_steps(config):
        profile = config["executor_profiles"][step.get("executor_profile", default)]
        assert profile["kind"] == "oci" and profile["network_profile"] == "none", step["name"]

    # negative control: この fixture の下では外向きの接続が例外になる。
    with pytest.raises(NetworkBlocked):
        _dial_out()
    with socket.socket() as sock, pytest.raises(NetworkBlocked):
        sock.connect_ex(("192.0.2.1", 443))
    assert no_network == [("192.0.2.1", 443)] * 2
    no_network.clear()

    # 受入テスト全体を同じ fixture の下で走らせる。このテスト自身は再帰になるので外す。
    this = f"{Path(__file__).relative_to(ROOT).as_posix()}::{test_c018_gate_steps_need_no_network.__name__}"
    code = pytest.main(
        [str(ROOT / "tests" / "acceptance"), "-q", "-p", "no:cacheprovider", "--rootdir", str(ROOT), "--deselect", this]
    )
    assert code == pytest.ExitCode.OK
    assert no_network == []
