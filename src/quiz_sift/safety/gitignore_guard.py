"""書き出し先ガード(NFR-2 / D-055)。書き出す前に、そのパスが `.gitignore` 対象であることを確かめる。

`.gitignore` の意味論は `fnmatch` と同じではない(AR-16)。ディレクトリ形式の `data/` は
`data/deckA/q.jsonl` を無視させるが、`fnmatch` でその2つを比べると偽になる。ここでは git の規則を
自前で評価する:

- スラッシュを先頭か途中に含むパターンは `.gitignore` の置き場所に固定され、含まないパターンは
  どの階層の名前にもマッチする。
- 末尾スラッシュはディレクトリにだけマッチする。
- `*` と `?` と `[...]` は `/` をまたがない。`**` は `/` で区切られた位置でだけ複数階層にマッチする。
- `!` は否定で、最後にマッチした行が勝つ。ただし親ディレクトリが無視されていれば、その下を否定で
  戻すことはできない。
- `\\` は次の1文字をエスケープする。末尾の空白はエスケープされていなければ捨てる。

評価するのはリポジトリ直下の `.gitignore` だけ。下位ディレクトリの `.gitignore` や
`.git/info/exclude` は読まない。下位の `.gitignore` の否定は直下の判定を覆しうるが、`data/` や
`out/` のように無視されたディレクトリの下は否定で戻せないので、書き出し先の判定には効かない。
強制追加済みで追跡されているファイルも見ない。本物の git との等価性は、ホスト上の
`git check-ignore` との突き合わせで別に実測する(T-021)。
"""

import re
from dataclasses import dataclass
from pathlib import Path


class NotIgnoredError(SystemExit):
    """書き出し先が `.gitignore` 対象ではなかった。捕まえずに上げれば非0で終了する。"""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"書き出し先が .gitignore 対象ではないので書かない: {path} ({reason})")
        self.path = path


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """`.gitignore` の1行。"""

    pattern: str
    negated: bool
    directory_only: bool
    regex: re.Pattern[str]


def parse_gitignore(text: str) -> list[IgnoreRule]:
    """`.gitignore` の内容を規則の列にする。空行とコメントは捨てる。"""
    rules = []
    for raw in text.splitlines():
        line = _strip_trailing_spaces(raw)
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        body = line[1:] if negated else line
        directory_only = body.endswith("/")
        if directory_only:
            body = body[:-1]
        if not body:
            continue
        anchored = "/" in body
        body = body.removeprefix("/")
        prefix = "" if anchored else "(?:.*/)?"
        regex = re.compile(f"{prefix}{_translate(body)}", re.DOTALL)
        rules.append(IgnoreRule(pattern=raw, negated=negated, directory_only=directory_only, regex=regex))
    return rules


def _strip_trailing_spaces(line: str) -> str:
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    return line


def _translate(pattern: str) -> str:
    """パスの区切りを `/` とする wildmatch パターンを正規表現にする。"""
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            at_segment_start = i == 0 or pattern[i - 1] == "/"
            if j - i == 2 and at_segment_start and j == n:
                out.append(".*")
            elif j - i == 2 and at_segment_start and pattern[j] == "/":
                out.append("(?:.*/)?")
                j += 1
            else:
                out.append("[^/]*")
            i = j
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            translated, i = _translate_class(pattern, i)
            out.append(translated)
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _translate_class(pattern: str, start: int) -> tuple[str, int]:
    """`[...]` を正規表現の文字クラスにする。閉じていなければ `[` を文字として扱う。"""
    i = start + 1
    negate = i < len(pattern) and pattern[i] in "!^"
    if negate:
        i += 1
    members: list[str] = []
    first = True
    while i < len(pattern):
        c = pattern[i]
        if c == "]" and not first:
            head = "^/" if negate else ""
            body = "".join(members)
            return (f"[{head}{body}]" if negate else f"(?!/)[{body}]"), i + 1
        if c == "[" and pattern.startswith("[:", i):
            raise ValueError(f"文字クラス名 [:...:] は評価できない: {pattern}")
        if c == "\\" and i + 1 < len(pattern):
            i += 1
            c = pattern[i]
        members.append(c if c == "-" else re.escape(c))
        first = False
        i += 1
    return re.escape("["), start + 1


def is_ignored(relative_path: str, rules: list[IgnoreRule], *, is_dir: bool = False) -> bool:
    """`/` 区切りの相対パスが無視対象かを返す。

    親ディレクトリを浅い順に評価し、どれかが無視されていればその下はすべて無視対象になる
    (否定で戻せない)。そうでなければパス自身に最後にマッチした規則が決める。
    """
    parts = [part for part in relative_path.split("/") if part]
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth])
        candidate_is_dir = depth < len(parts) or is_dir
        ignored = _last_match(candidate, candidate_is_dir, rules)
        if ignored and depth < len(parts):
            return True
        if depth == len(parts):
            return ignored
    return False


def _last_match(path: str, is_dir: bool, rules: list[IgnoreRule]) -> bool:
    ignored = False
    for rule in rules:
        if rule.directory_only and not is_dir:
            continue
        if rule.regex.fullmatch(path):
            ignored = not rule.negated
    return ignored


def find_repo_root(start: Path) -> Path:
    """`start` から親へ辿って `.git` を持つディレクトリを返す。見つからなければ `NotIgnoredError`。"""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    raise NotIgnoredError(start, "Git リポジトリの外なので判定できない")


def ensure_ignored(path: Path, repo_root: Path, *, is_dir: bool = False) -> Path:
    """`path` が `repo_root/.gitignore` の対象なら解決済みのパスを返し、そうでなければ `NotIgnoredError`。

    全書き出し経路は、ファイルを開く前にこれを通す。リポジトリの外は判定できないので止める。
    """
    root = repo_root.resolve()
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise NotIgnoredError(resolved, "リポジトリ内のパスではない")
    gitignore = root / ".gitignore"
    rules = parse_gitignore(gitignore.read_text(encoding="utf-8")) if gitignore.is_file() else []
    if not is_ignored(resolved.relative_to(root).as_posix(), rules, is_dir=is_dir):
        raise NotIgnoredError(resolved, "追跡対象になりうる")
    return resolved
