"""書き出し先ガード(C-014 / NFR-2 / AR-16)。

自前評価器の期待値はここに書いた git の規則に対してだけ緑になる。本物の git との等価性は
ホスト上の実測(T-021)が受け持つ。
"""

import fnmatch
from pathlib import Path

import pytest

from quiz_sift.safety.gitignore_guard import (
    NotIgnoredError,
    ensure_ignored,
    find_repo_root,
    is_ignored,
    parse_gitignore,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def repo(tmp_path: Path, gitignore: str) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    return tmp_path


def test_directory_form_patterns_match(tmp_path: Path) -> None:
    rules = parse_gitignore((REPO_ROOT / ".gitignore").read_text(encoding="utf-8"))

    # fnmatch ではディレクトリ形式が配下のファイルにマッチしない。それが AR-16 の落とし穴。
    assert not fnmatch.fnmatch("data/deckA/q.jsonl", "data/")
    assert is_ignored("data/deckA/q.jsonl", rules)
    assert is_ignored("out/deckA/report.md", rules)
    assert is_ignored("out/deckA/audio/0001.mp3", rules)
    assert is_ignored("data", rules, is_dir=True)
    # 追跡対象の置き場所は無視されない。
    for tracked in ("docs/report.md", "reports/deckA.md", "src/quiz_sift/x.py", "tests/fixtures/q.jsonl", "README.md"):
        assert not is_ignored(tracked, rules), tracked
    # 末尾スラッシュはディレクトリにだけマッチする。`data` という名前のファイルは無視されない。
    assert not is_ignored("data", rules)
    assert not is_ignored("dataset/q.jsonl", rules)

    # 書き出し経路の入口: 対象なら開いてよいパスを返し、対象外なら非0で終了する。
    root = repo(tmp_path, (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"))
    assert ensure_ignored(Path("data/deckA/q.jsonl"), root) == root / "data/deckA/q.jsonl"
    assert ensure_ignored(root / "out/deckA/evidence.jsonl", root) == root / "out/deckA/evidence.jsonl"
    with pytest.raises(NotIgnoredError) as excinfo:
        ensure_ignored(Path("docs/deckA.md"), root)
    assert isinstance(excinfo.value, SystemExit)
    assert excinfo.value.code not in (None, 0)


def test_unanchored_pattern_matches_at_any_depth() -> None:
    rules = parse_gitignore("data/\n*.py[cod]\n")
    assert is_ignored("nested/data/q.jsonl", rules)
    assert is_ignored("src/a/b.pyc", rules)
    assert not is_ignored("src/a/b.py", rules)


def test_anchored_pattern_matches_only_at_root() -> None:
    rules = parse_gitignore("/out/\ndocs/generated\n")
    assert is_ignored("out/x", rules)
    assert not is_ignored("sub/out/x", rules)
    assert is_ignored("docs/generated", rules)
    assert is_ignored("docs/generated/a.md", rules)
    assert not is_ignored("sub/docs/generated", rules)


def test_star_does_not_cross_slash_but_double_star_does() -> None:
    rules = parse_gitignore("a/*.txt\n**/cache/\nlogs/**\nx/**/y\n")
    assert is_ignored("a/b.txt", rules)
    assert not is_ignored("a/b/c.txt", rules)
    assert is_ignored("cache/k", rules)
    assert is_ignored("deep/er/cache/k", rules)
    assert is_ignored("logs/a/b.log", rules)
    assert not is_ignored("logs", rules, is_dir=True)
    assert is_ignored("x/y", rules)
    assert is_ignored("x/p/q/y", rules)


def test_negation_last_match_wins_but_cannot_reinclude_under_ignored_parent() -> None:
    rules = parse_gitignore("*.log\n!keep.log\nout/\n!out/deckA/q.jsonl\n")
    assert is_ignored("a.log", rules)
    assert not is_ignored("keep.log", rules)
    assert is_ignored("out/deckA/q.jsonl", rules)


def test_comments_escapes_and_trailing_spaces() -> None:
    rules = parse_gitignore("# comment\n\n\\#hash\n\\!bang\nspaced   \n")
    assert not is_ignored("comment", rules)
    assert is_ignored("#hash", rules)
    assert is_ignored("!bang", rules)
    assert is_ignored("spaced", rules)


def test_character_class_never_matches_slash() -> None:
    rules = parse_gitignore("a[/x]b\nc[!y]d\n")
    assert is_ignored("axb", rules)
    assert not is_ignored("a/b", rules)
    assert is_ignored("czd", rules)
    assert not is_ignored("cyd", rules)
    assert not is_ignored("c/d", rules)


def test_paths_outside_repo_and_missing_gitignore_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    root = repo(tmp_path / "r", "out/\n")
    with pytest.raises(NotIgnoredError):
        ensure_ignored(tmp_path / "elsewhere/out/x", root)
    with pytest.raises(NotIgnoredError):
        ensure_ignored(Path("out/../docs/x"), root)
    (root / ".gitignore").unlink()
    with pytest.raises(NotIgnoredError):
        ensure_ignored(Path("out/x"), root)


def test_symlinked_output_into_tracked_path_is_refused(tmp_path: Path) -> None:
    root = repo(tmp_path, "out/\n")
    (root / "docs").mkdir()
    (root / "out").symlink_to(root / "docs")
    with pytest.raises(NotIgnoredError):
        ensure_ignored(Path("out/x.md"), root)


def test_find_repo_root(tmp_path: Path) -> None:
    root = repo(tmp_path, "out/\n")
    (root / "out/deckA").mkdir(parents=True)
    assert find_repo_root(root / "out/deckA") == root
