# Current state (baseline of the existing codebase)

> `/onboard` がリポジトリを読み取りだけで調べて作った**持続する基準線**。`/req` と `/design` が最初に読む。
> `rein cycle-close` でアーカイブされず、サイクルの終わりごとに更新する。既存文書はここからリンクするだけで、移さない。

## Architecture overview

- 入口は3つ。
  - CLI `python -m quiz_sift.cli [--root] <段> <deck>`(`src/quiz_sift/cli.py`)。段は ingest / cluster / recall / glossary / script / audio / glossary-audio / review / serve。`pyproject.toml` にコンソールスクリプトは無い。
  - ローカル API サーバ(`src/quiz_sift/server/__main__.py`)。
  - Web UI(`web/`、Next.js 16 / React 19)。ローカル API を呼ぶだけで、判定は Python 側(ADR-006)。
- データの流れ: `data/<deck>/` を取り込み・正規化・重複除去 → LLM でクラスタリング → 想起素材・グロッサリ・台本 → CosyVoice で音声 → `out/<deck>/…`。
  - cluster 要約・想起素材・グロッサリの説明は含意判定(safety)と grounding を通り、落ちた項目だけを上限つきで作り直す(作り直しには前回の出力と落ちた理由を渡す)。台本段は含意判定を呼ばず、想起素材の段の判定で通す(D-248、ADR-030)。台本段の検査はラベル列挙判定(R-6)。
  - 段の結果は `pipeline/stage.StageCache` が digest ごとにキャッシュする(NFR-4)。recall は問題ごとの途中結果を run_id の範囲で残し、`--resume` で続きから進む。
  - 学習記録は SQLite(ADR-005)。
  - 書き出し前に `ensure_ignored` で出力先が git の追跡外かを確かめる(NFR-2)。
- LLM は `claude -p`(`composition.build_default_llm` → `adapters/llm_claude_cli`、サブスクリプション認証、最小ホーム)。`llm_anthropic.py` は削除済み(T-062)。依存の `anthropic` は pyproject.toml に残っている。
- 問題の自動抽出(R-2)に製品側の CLI は無い。実取得は `tests/measurements/test_extraction_logs.py` の採取経路から行った。

## Modules / directories

| Path | Role |
|------|------|
| `src/quiz_sift/domain/` | ドメイン型(Question・Deck・Cluster・ID 型) |
| `src/quiz_sift/ports/` | Protocol(llm・tts・clock・metering。`RunMeter` と `FailureKind` 7種) |
| `src/quiz_sift/adapters/` | `llm_claude_cli`(と `claude_process`・`claude_failure`・`llm_contract`・`llm_prompts`)・`tts_cosyvoice`・`browser_cdp` |
| `src/quiz_sift/{ingest,cluster,recall,glossary,script,audio,schedule,scoring}/` | R-1、R-3〜R-8、R-10 の各段 |
| `src/quiz_sift/extract/` | 抽出(HTML の純粋関数・evidence・failures・writer) |
| `src/quiz_sift/grounding/` | 語句の実在性判定(fugashi、ADR-010) |
| `src/quiz_sift/safety/` | 含意判定・`gitignore_guard`・`log_filter`・`secret_mask` |
| `src/quiz_sift/pipeline/` | stage キャッシュ・meter・overhead(Opus 5.5・Sonnet 5.5・Haiku 4.5 の較正値)・run_sequence(実行の列) |
| `src/quiz_sift/{store,decks}/` | 学習記録ストア、デッキの manifest とレジストリ |
| `src/quiz_sift/server/` | ローカル HTTP API |
| `tests/` | src と同じ構成。`tests/measurements/` はホスト実測ログの判定と採取、`tests/fixtures/claude_cli/` は実 CLI の契約フィクスチャ |
| `web/` | Next.js UI。テストは無く、R-12 は artifact で判定(ADR-012) |
| `gate.sh` | 品質ゲートと CI が共通で呼ぶ検査ステップ(NFR-5) |

## Reusable assets

- ID と共有型: `domain/ids.py`、`domain/question.py`
- 計測: `pipeline/meter.Meter`、`ports/metering.RunMeter`・`FailureKind`(単価表は claude-opus-5-5 と claude-haiku-4-5)
- マスク: `safety/secret_mask.SecretMaskFilter`、`safety/log_filter.MaterialTextFilter`
- 書き出しガード `ensure_ignored`、キャッシュ `StageCache`
- LLM のプロンプト・JSON Schema・メソッド別のモデルは `adapters/llm_prompts.py`、応答の純粋パーサは `adapters/llm_contract.py`。
- 実 CLI の契約フィクスチャ `tests/fixtures/claude_cli/`(success / invalid_schema)と採取スクリプト `tests/measurements/collect_claude_cli.py`
- テストの fake: 共有ファクトリは無い。`StubLlm` / `StubTts` / `FakeClock` がテストファイルごとに重複している。パッケージごとの `conftest.py` に `make_deck` などがある。

## Conventions

- Python 3.13。PEP 695 の `type` 構文、frozen dataclass、mypy strict、ruff(E/F/I/UP/B、120桁)。
- docstring とコメントは日本語で、要件 ID・AR・D 番号を引く。
- エラー: 段の検査に落ちたら `SystemExit` の派生で非0終了、入力の不正は `ValueError` の派生、「対象なし」は結果型(`NoTargets` など)。
- テストは `tests/<pkg>/test_<振る舞い>.py`。受入基準は pytest のノード ID と対応させる。命名は pre-commit の `name-tests-test` で強制。
- コミットは `T-NNN: <要約>` / `chore: …` / `tasks: …`、本文は日本語。

## Test / CI commands

- `bash gate.sh <step>`: `lint`(ruff check)/ `format`(ruff format --check)/ `types`(mypy .)/ `test`(pytest -q)/ `docs`(mkdocs build --strict)/ `smoke`(`quiz_sift.__version__` の import)。`.rein/config.yaml` の quality_gate も同じ6ステップを呼ぶ。
- CI(`.github/workflows/ci.yml`)は `uv sync` のあと同じ6ステップを呼ぶ。
- `makefile`、`.pre-commit-config.yaml`(ruff、pre-push で mypy)。TTS の依存は `uv sync --extra tts` のときだけで、ゲートには含まれない。

## Existing documents

- [00-product-brief.md](00-product-brief.md)。入力を「4択」と書いたままで、R-1 の M ∈ {4, 5, 6} に合わせる更新は人が行う。
- [archive/2026-10-23-n-of-m-full-run/](archive/2026-10-23-n-of-m-full-run/): 2026-10-23 に閉じたサイクルの成果物。要件の差分(R-1〜R-4・R-6〜R-8・R-12・NFR-1・NFR-2・NFR-5・NFR-7・NFR-8)、設計、ADR-020〜ADR-030、T-054〜T-090、test-plan、実測記録(`test/measurements/`)、retrospective、SSOT(`rein/`)。決定は D-168〜D-250。
- [archive/2026-10-06-fisrt/](archive/2026-10-06-fisrt/): 2026-10-06 に閉じたサイクルの成果物。要件の全文(R-1〜R-12、R-9 は欠番。NFR-1〜NFR-8)、ADR-001〜ADR-019、T-001〜T-053。
- [archive/2026-09-25-cli-migration-unclosed/](archive/2026-09-25-cli-migration-unclosed/README.md): rein 0.9.6 のサイクルの SSOT。閉じずに保存した。
- `docs/` 直下の `10-requirements.md`・`20-design.md`・`tasks/`・`test/` などは、次のサイクル用のテンプレートに戻っている。

## Implementation status

### Implemented capabilities

2026-10-06 までの分に加え、2026-10-23 に閉じたサイクルの T-054〜T-090。acceptance は GA-ACCEPTANCE-788C1629、PR #1 で main に統合。

- R-1〜R-8、R-10、R-11、R-12、NFR-1〜NFR-8。
- 問題は M ∈ {4, 5, 6} 択・正解 N 件(1 ≤ N < M)。正解は選択肢 ID の配列で、採点・学習記録ストア v2・API・Web UI は選択の集合の完全一致(T-055、T-065、T-066)。
- 子 CLI: タイムアウトはプロセスグループ単位(SEC-004)、出力本文をログと例外文に出さない(SEC-003)、ユーザ設定を遮断し有効ツールは StructuredOutput だけ(SEC-001、T-054・T-060)。切り詰め(TRUNCATED)は同じ入力のまま上限つきで出し直す(T-072)。
- モデル: 分類・要約・用語説明は Opus 5.5、含意判定は Sonnet 5.5(1問1回にまとめ、判定値を主張の ID で受ける。T-074、T-084)、recall の生成は Haiku 4.5(思考あり、D-246)。
- フル処理: 実デッキ aip-c01(190 問)で cluster → recall → script → audio を完走(試行7、列 384e3e42)。無音判定 116 ファイルすべて真。再現性の列2本(80bc0d11・b5096b8b)で Rand 指数 0.9612。記録は `docs/test/measurements/full-run.md`・`reproducibility.md`・`audio-silence.md`(T-071)。
- 実 CosyVoice は torch 2.10 で動く(pyworld を上流の git タグ v0.3.6 に固定。T-069)。グロッサリの音声は glossary-audio 段(T-080)。
- バージョンは 0.1.1(T-067)。依存の監査は clean(2026-10-23)。

### In-flight / unfinished work

| 項目 | 状態 | 次にやること |
|---|---|---|
| **クールダウンの一時除外** | `pyproject.toml` の `exclude-newer-package` で pillow・virtualenv・certifi だけを 1 週間のクールダウンから外した(D-250、人の判断 2026-10-23) | 2026-10-27 11:25 JST 以降にその行を消し、`uv lock` で解決が変わらないことを確かめる |
| **再開で戻した問題がクラスタをまたぐ重複キーの検査を受け直さない** | 受け入れ時の残りの指摘(must_fix)。1回目の実行で問題 i が枠切れ以外の LlmCallError で失敗し、後ろの別クラスタの問題 j が i を含まずに通ると、`--resume` で i を作ったとき j との重複キーが成果物に入りうる。実デッキの記録では起きていない | 再現テストを書いて recall の再開経路を直す |
| **テストの穴(受け入れ時の残りの指摘 12 件)** | 赤になることを確かめるテストが無い基準(分類のダイジェスト、A-3 の段ごとの数、作り直しの上限を定数でなく基準の値で比べる、grounding の作り直しのログ、glossary-audio の `--voice`、`pytest.raises` の送出内容 ほか)と、実装と食い違う docstring・コメント 2 件。一覧は archive の `rein/review.yaml` の `residual_findings` | 次のサイクルのタスクにする |
| **依存 `anthropic` の削除** | `llm_anthropic.py` は消した(T-062)が、pyproject.toml の依存は残る | 削除して lock を出し直す |
| **レビュアーの findings の置き場所** | タスク単位のレビュアーが findings を worktree 側の `.rein/work/` に書き、loop が読めず blocked になる。T-082・T-086・T-088・T-089・T-090 で起きた | rein 側の問題。retrospective §6 を参照 |

### Known TODOs / open issues (reference only — Issues are never read back as truth)

- ソースに TODO / FIXME は無い。
- 追跡中の扱いにくいファイル: 空の `tests/fixtures/claude_cli/invalid_schema.stdout`(旧 T-029)。

## Last updated

- 2026-10-23: サイクル `n-of-m-full-run` を閉じる前に、構成・実装済み・引き継ぎを更新。
- 2026-10-06: サイクル `2026-10-06-fisrt` を閉じた後に、文書の置き場・実装済み・引き継ぎを更新。loose-rein を 0.13.0 に上げる。
- 2026-09-29: サイクル 2026-09-26 を閉じる前に、実装済みと引き継ぎを更新。
- 2026-09-25: 初回の onboarding 調査(rein 0.10.0 への移行時)。
