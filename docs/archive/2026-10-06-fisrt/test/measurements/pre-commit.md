# 実測ログ: pre-commit run --all-files

NFR-5 (c) の pre-commit は品質ゲートにも CI にも置いていない(D-031)。フックを GitHub から
clone するため、`--network none` のサンドボックスでは原理的に走らないため。代わりに
**ホストで取った実測ログ**をこのファイルに置き、`tests/measurements/test_pre_commit_log.py` が
その中身を機械判定する(受入基準 A-4)。

再実行できないログなので、テストが守れるのは「古いログ」と「作り話のログ」だけで、
「今この瞬間に緑である」ことではない。フックを足したら測り直すこと — 表の hook id 集合が
`.pre-commit-config.yaml` と食い違った時点で赤になる。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `pre-commit run --all-files` |
| 実行日時 | 2026-09-23T02:47:37+09:00 |
| コミット | `d14b5e8cdfb1` |
| 作業ツリー | 上記コミット + T-007 の変更を `git add` 済みの状態(このログ自身、`tests/measurements/test_pre_commit_log.py`、`pyproject.toml` の mypy override) |
| 実行環境 | ホスト(WSL2 / Linux 6.6.87.2) |
| pre-commit | 4.6.2 |

`mypy` フックは `.pre-commit-config.yaml` で `stages: [pre-push]` を宣言しているため、
`pre-commit run --all-files` の既定実行(pre-commit ステージ)には現れない。型検査は
品質ゲートの `bash gate.sh types` が担う。

`pre-commit run --all-files` が見るのは **git が知っているファイル**なので、上の変更を
`git add` してから実行している。未追跡のまま走らせると新規ファイルが素通りし、
「全ファイルで緑」を名乗れない(この差は実測で確認した — 未追跡時は `ruff format` の
整形漏れを1本見逃していた)。この表の「実行日時」はその実行時刻で、以降にこのファイルへ
加えた編集は表と出力の転記だけ。

## 終了コード

0

## 出力

```text
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check yaml...............................................................Passed
debug statements (python)................................................Passed
python tests naming......................................................Passed
fix requirements.txt.....................................................Passed
ruff (legacy alias)......................................................Passed
ruff format..............................................................Passed
```

## 実行されたフック

| hook id | 表示名 | 結果 |
|---|---|---|
| `trailing-whitespace` | trim trailing whitespace | Passed |
| `end-of-file-fixer` | fix end of files | Passed |
| `check-yaml` | check yaml | Passed |
| `debug-statements` | debug statements (python) | Passed |
| `name-tests-test` | python tests naming | Passed |
| `requirements-txt-fixer` | fix requirements.txt | Passed |
| `ruff` | ruff (legacy alias) | Passed |
| `ruff-format` | ruff format | Passed |
