# 実測ログ: .gitignore 評価器と git check-ignore の等価性

NFR-2(C-014)の書き出し先ガード `safety/gitignore_guard.py` は `.gitignore` を自前で評価する。
その評価器が本物の git と同じ判定を返すことを、ホストの `git check-ignore -v` と代表パス集合で
突き合わせて確かめた記録(AR-16、受入基準 A-1)。`tests/measurements/test_host_check_logs.py`
がこの記録を機械判定する。

突き合わせは使い捨てのリポジトリで行う。`.gitignore` をそこへ写し、各パスをファイルまたは
ディレクトリとして実体化してから、`--no-index` と `core.excludesFile=/dev/null` を付けて
git に聞く。リポジトリ自身の `.git/info/exclude`・追跡状態・利用者設定が判定に混ざらない
ようにするためで、自前評価器が読むのもリポジトリ直下の `.gitignore` だけなので入力が揃う。

`.gitignore`・代表集合・評価器のどれかを変えたら測り直すこと。古いままの記録はテストが赤にする。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_host_check_logs.py gitignore` |
| 実行日時 | 2026-09-23T18:03:09+09:00 |
| コミット | `46d58a3e4445` |
| git | git version 2.53.0 |
| 実行環境 | ホスト(Linux 6.6.87.2-microsoft-standard-WSL2) |

## 結果

判定の不一致: 0 件 / 56 件

## 突き合わせ: リポジトリの .gitignore

`.gitignore` の digest: `sha256:c5b98cbb169131dd754abf5c6a34e8b8cbce21cb563433ece166e5b3b35b4e3b`

| パス | 種別 | git | git の根拠 | 自前評価器 | 判定 |
|---|---|---|---|---|---|
| `"data"` | dir | ignored | `.gitignore:13:data/` | ignored | 一致 |
| `"data/deckA/questions.jsonl"` | file | ignored | `.gitignore:13:data/` | ignored | 一致 |
| `"data/deckA/raw/page-001.html"` | file | ignored | `.gitignore:13:data/` | ignored | 一致 |
| `"out"` | dir | ignored | `.gitignore:14:out/` | ignored | 一致 |
| `"out/deckA/recall/abc123.json"` | file | ignored | `.gitignore:14:out/` | ignored | 一致 |
| `"out/deckA/audio/001_intro.mp3"` | file | ignored | `.gitignore:14:out/` | ignored | 一致 |
| `"out/deckA/report.html"` | file | ignored | `.gitignore:14:out/` | ignored | 一致 |
| `"out/learning.sqlite3"` | file | ignored | `.gitignore:14:out/` | ignored | 一致 |
| `"src/quiz_sift/out.py"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"src/quiz_sift/data/__init__.py"` | file | ignored | `.gitignore:13:data/` | ignored | 一致 |
| `"src/quiz_sift/__pycache__/cli.cpython-312.pyc"` | file | ignored | `.gitignore:16:__pycache__/` | ignored | 一致 |
| `"src/quiz_sift/cli.pyc"` | file | ignored | `.gitignore:17:*.py[cod]` | ignored | 一致 |
| `"src/quiz_sift/cli.py"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"tests/fixtures/data/synthetic.jsonl"` | file | ignored | `.gitignore:13:data/` | ignored | 一致 |
| `"docs/test/measurements/gitignore-equivalence.md"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"web/app/page.tsx"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"web/lib/data"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"web/out"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"site/index.html"` | file | ignored | `.gitignore:21:site/` | ignored | 一致 |
| `".venv/bin/python"` | file | ignored | `.gitignore:15:.venv/` | ignored | 一致 |
| `".rein/work/T-021.json"` | file | ignored | `.gitignore:7:.rein/work/` | ignored | 一致 |
| `".rein/plan.yaml"` | file | not-ignored | `-` | not-ignored | 一致 |
| `".worktrees/T-021/data/x.jsonl"` | file | ignored | `.gitignore:8:.worktrees/` | ignored | 一致 |
| `".mypy_cache/3.12/cache.db"` | file | ignored | `.gitignore:19:.mypy_cache/` | ignored | 一致 |
| `"README.md"` | file | not-ignored | `-` | not-ignored | 一致 |

## 突き合わせ: 合成 .gitignore

リポジトリの `.gitignore` はディレクトリ形式と拡張子の規則しか使っていない。否定・`**`・
文字クラス・エスケープの扱いを測るため、評価器が扱う規則を一通り踏む合成の
`.gitignore` でも突き合わせた。表のパスは JSON 文字列で書いてある。

```gitignore
# comment
/build
logs/
*.tmp
!keep.tmp
docs/**/draft.md
**/cache
vendor/**
file?.txt
report[0-9].csv
note[!a].md
\#hash
\!bang
two\ words
secret/
!secret/allowed.txt
a/**/b
```

| パス | 種別 | git | git の根拠 | 自前評価器 | 判定 |
|---|---|---|---|---|---|
| `"build"` | file | ignored | `.gitignore:2:/build` | ignored | 一致 |
| `"sub/build"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"logs"` | dir | ignored | `.gitignore:3:logs/` | ignored | 一致 |
| `"sub/logs/app.log"` | file | ignored | `.gitignore:3:logs/` | ignored | 一致 |
| `"other/logs"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"a.tmp"` | file | ignored | `.gitignore:4:*.tmp` | ignored | 一致 |
| `"sub/b.tmp"` | file | ignored | `.gitignore:4:*.tmp` | ignored | 一致 |
| `"keep.tmp"` | file | not-ignored | `.gitignore:5:!keep.tmp` | not-ignored | 一致 |
| `"sub/keep.tmp"` | file | not-ignored | `.gitignore:5:!keep.tmp` | not-ignored | 一致 |
| `"docs/draft.md"` | file | ignored | `.gitignore:6:docs/**/draft.md` | ignored | 一致 |
| `"docs/x/y/draft.md"` | file | ignored | `.gitignore:6:docs/**/draft.md` | ignored | 一致 |
| `"sub/docs/draft.md"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"cache/f"` | file | ignored | `.gitignore:7:**/cache` | ignored | 一致 |
| `"deep/er/cache/f"` | file | ignored | `.gitignore:7:**/cache` | ignored | 一致 |
| `"vendor"` | dir | not-ignored | `-` | not-ignored | 一致 |
| `"vendor/a/b.js"` | file | ignored | `.gitignore:8:vendor/**` | ignored | 一致 |
| `"file1.txt"` | file | ignored | `.gitignore:9:file?.txt` | ignored | 一致 |
| `"file10.txt"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"report5.csv"` | file | ignored | `.gitignore:10:report[0-9].csv` | ignored | 一致 |
| `"reportx.csv"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"notea.md"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"noteb.md"` | file | ignored | `.gitignore:11:note[!a].md` | ignored | 一致 |
| `"#hash"` | file | ignored | `.gitignore:12:\#hash` | ignored | 一致 |
| `"!bang"` | file | ignored | `.gitignore:13:\!bang` | ignored | 一致 |
| `"two words"` | file | ignored | `.gitignore:14:two\ words` | ignored | 一致 |
| `"two"` | file | not-ignored | `-` | not-ignored | 一致 |
| `"secret/allowed.txt"` | file | ignored | `.gitignore:15:secret/` | ignored | 一致 |
| `"secret/other.txt"` | file | ignored | `.gitignore:15:secret/` | ignored | 一致 |
| `"a/b"` | file | ignored | `.gitignore:17:a/**/b` | ignored | 一致 |
| `"a/x/y/b"` | file | ignored | `.gitignore:17:a/**/b` | ignored | 一致 |
| `"plain.txt"` | file | not-ignored | `-` | not-ignored | 一致 |
