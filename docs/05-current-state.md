# Current state (baseline of the existing codebase)

> `/onboard` がリポジトリを読み取りだけで調べて作った**持続する基準線**。`/req` と `/design` が最初に読む。
> `rein cycle-close` でアーカイブされず、サイクルの終わりごとに更新する。既存文書はここからリンクするだけで、移さない。

## Architecture overview

- 入口は3つ。
  - CLI `python -m quiz_sift.cli [--root] <段> <deck>`(`src/quiz_sift/cli.py`)。段は ingest / cluster / recall / glossary / script / audio / review / serve。`pyproject.toml` にコンソールスクリプトは無い。
  - ローカル API サーバ(`src/quiz_sift/server/__main__.py`)。
  - Web UI(`web/`、Next.js 16 / React 19)。ローカル API を呼ぶだけで、判定は Python 側(ADR-006)。
- データの流れ: `data/<deck>/` を取り込み・正規化・重複除去 → LLM でクラスタリング → 想起素材・グロッサリ・台本 → CosyVoice で音声 → `out/<deck>/…`。
  - 各段は含意判定(safety)と grounding を通る。
  - 段の結果は `pipeline/stage.StageCache` が digest ごとにキャッシュする(NFR-4)。
  - 学習記録は SQLite(ADR-005)。
  - 書き出し前に `ensure_ignored` で出力先が git の追跡外かを確かめる(NFR-2)。
- LLM の既定は今も Anthropic SDK(`cli._default_llm` → `adapters/llm_anthropic.AnthropicLlm`)。Claude Code CLI への切り替え(ADR-013)は未実装。
- 問題の自動抽出(R-2)に製品側の CLI は無い。実取得は `tests/measurements/test_extraction_logs.py` の採取経路から行った。

## Modules / directories

| Path | Role |
|------|------|
| `src/quiz_sift/domain/` | ドメイン型(Question・Deck・Cluster・ID 型) |
| `src/quiz_sift/ports/` | Protocol(llm・tts・clock・metering。`RunMeter` と `FailureKind` 7種) |
| `src/quiz_sift/adapters/` | `llm_anthropic`・`tts_cosyvoice`・`browser_cdp` |
| `src/quiz_sift/{ingest,cluster,recall,glossary,script,audio,schedule,scoring}/` | R-1、R-3〜R-8、R-10 の各段 |
| `src/quiz_sift/extract/` | 抽出(HTML の純粋関数・evidence・failures・writer) |
| `src/quiz_sift/grounding/` | 語句の実在性判定(fugashi、ADR-010) |
| `src/quiz_sift/safety/` | 含意判定・`gitignore_guard`・`log_filter`・`secret_mask` |
| `src/quiz_sift/pipeline/` | stage キャッシュ・meter・overhead(`OVERHEAD` は空) |
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
- LLM のプロンプト・JSON スキーマ・パーサ(`_proposals` / `_string`)は `adapters/llm_anthropic.py` にある。CLI 移行では共有モジュールへ移す想定(旧 T-025)。
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

- [00-product-brief.md](00-product-brief.md)、[10-requirements.md](10-requirements.md)(R-1〜R-12、R-9 は欠番。NFR-1〜NFR-8)、[20-design.md](20-design.md)
- [decisions/](decisions/) の ADR-001〜ADR-016(ADR-013〜016 が Claude Code CLI への移行)
- [tasks/](tasks/) の T-001〜T-031
- [test/measurements/](test/measurements/): pre-commit、gitignore 等価性、ui-e2e、抽出、ゴールデンセット、claude-cli-contract の実測ログ
- [archive/2026-09-25-cli-migration-unclosed/](archive/2026-09-25-cli-migration-unclosed/README.md): rein 0.9.6 のサイクルの SSOT。閉じずに保存した。
- `test/test-plan.md`、`retrospective.md`、`speculative-work.md` は実質テンプレートのまま。

## Implementation status

### Implemented capabilities

旧 T-001〜T-019、T-021〜T-023、T-031 に加え、サイクル 2026-09-26 の T-032〜T-051 の分(T-047 は途中までの記録に作り直した。D-164)。

- R-1〜R-8、R-10、R-11、R-12、NFR-1〜NFR-6。
- NFR-7: overhead の較正値あり(T-046)。実行の列の集約と CLI(`--fresh-run` / `--resume`、12 時間の上限、早期停止)がある(T-038、T-045)。
- NFR-8: 既定の LLM は `ClaudeCliLlm`(`claude -p`、サブスクリプション認証、最小ホーム)。起動前検査・子プロセスの環境の固定・逐語ログ・失敗分類がある(T-039〜T-044)。
- 要約の実在判定は専門用語の断片だけを照合する(D-161、T-049)。
- 含意判定は `claude-opus-5-5`。プロンプトは要約の省略・一般化を矛盾としない(ADR-019、T-051)。

### In-flight / unfinished work

| 項目 | 状態 | 次にやること |
|---|---|---|
| **想起素材のプロンプトと検査の不一致** | 実データで全件落ちる。列 591e7bb9 の recall 段が RecallRejected(ungrounded_keys=175/175、missing_options=47)。`RECALL_SYSTEM` は想起キーを本文の表記のまま抜き出すよう指示しておらず、`recall/verify.py` は `is_present` による実在を要求する | プロンプトを直すタスクを先に置く。直した後に `generic_keys`(全 term が他クラスタにも出る)で止まらないかも実データで確かめる。リポジトリ外の診断スクリプト `~/quiz-sift-diag/diag_recall.py` で、クラスタ段のキャッシュから全問を検査にかけられる |
| **フル処理100〜200問の完走(元の T-047、不可逆)** | 未完。元の票と受入基準は archive の `docs/tasks/T-047.md` の「元の票」。途中までの記録は `docs/test/measurements/full-run.md`。クラスタ段は列 591e7bb9 で完走(n=175)。実行の道具は `tests/measurements/test_full_run_logs.py`(`exec` / `silence` / `render`) | 上の修正の後、票の受入基準のまま `cluster --fresh-run` から列を取り直す。C-003・C-007・C-019・C-020 の実データでの証拠はまだ無い。含意判定は 350〜500 回が Opus なので、週次枠の途中で止まって `--resume` を要する見込みが高い |

T-047 のための許可 `Bash(uv run *:*)` が `.claude/settings.json` にある(c46dbc8)。次のサイクルの acceptance でセキュリティの指摘になる見込みで、残すか外すかを決める。

### Known TODOs / open issues (reference only — Issues are never read back as truth)

- ソースに TODO / FIXME は無い。
- T-029 の採取時、CLI 自身が最小ホームに `settings.json`(theme だけ)・`skills/`・`plugins/`・`projects/` を書いた。旧 T-024 の「settings.json があれば preflight が拒否する」と衝突する見込み。NFR-8 (b) と合わせて判断が要る。
- 追跡中のバイナリ・扱いにくいファイル: `docs/test/measurements/ui-e2e/01〜04-*.png`(旧 T-021)、空の `tests/fixtures/claude_cli/invalid_schema.stdout`(旧 T-029)。次の計画で受入基準の `artifact` として宣言しないと、`rein doctor` が coverage 不足を出す。

## Last updated

- 2026-09-29: サイクル 2026-09-26 を閉じる前に、実装済みと引き継ぎを更新。
- 2026-09-25: 初回の onboarding 調査(rein 0.10.0 への移行時)。
