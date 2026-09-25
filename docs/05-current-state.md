# Current state (baseline of the existing codebase)

> `/onboard` がリポジトリを読み取りだけで調べて作った**持続する基準線**。`/req` と `/design` が最初に読む。
> `rein cycle-close` でアーカイブされず、サイクルの終わりごとに更新する。既存文書はここからリンクするだけで、移さない。

## Architecture overview

- 入口は3つ。
  - CLI `python -m quiz_sift.cli [--root] <段> <deck>`([src/quiz_sift/cli.py](../src/quiz_sift/cli.py))。段は ingest / cluster / recall / glossary / script / audio / review / serve。`pyproject.toml` にコンソールスクリプトは無い。
  - ローカル API サーバ([src/quiz_sift/server/__main__.py](../src/quiz_sift/server/__main__.py))。
  - Web UI([web/](../web/)、Next.js 16 / React 19)。ローカル API を呼ぶだけで、判定は Python 側(ADR-006)。
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
- CI([.github/workflows/ci.yml](../.github/workflows/ci.yml))は `uv sync` のあと同じ6ステップを呼ぶ。
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

旧 T-001〜T-019、T-021〜T-023、T-031 の分。

- R-1、R-2(抽出ロジックと実取得1回)、R-3〜R-8、R-10、R-11、R-12
- NFR-1〜NFR-6
- NFR-7 は一部: `RunMeter` はあるが、overhead の較正値が空。
- NFR-8(CLI 経由・サブスクリプション認証)は未実装。

### In-flight / unfinished work

旧サイクルの残りで、どれも Claude Code CLI への移行(ADR-013〜016)に属する。受入基準はアーカイブの `rein/plan.yaml` と `docs/tasks/T-*.md` にある。

| 旧タスク | 内容 | ツリーにあるもの |
|---|---|---|
| T-029 | 実 CLI の契約フィクスチャと init.tools の実測 | **ほぼ完了**(`cbf7f72`)。フィクスチャ4ファイル、判定テスト3本、`claude-cli-contract.md`。受入は未判定 |
| T-024 | 子プロセスの実行環境(env の固定、Workspace、flock、preflight、タイムアウト、stdin) | 無い |
| T-025 | LLM の契約の共有化(ID の多重集合の検査、`unassigned`、バッチ25、メソッドごとのモデル) | 無い。`llm_anthropic` は旧パーサ・単一モデル・バッチ50のまま |
| T-026 | 失敗種別の判定・呼び出しログ・マスクの拡張・CLI の fake | `FailureKind` の列挙だけ |
| T-027 | `ClaudeCliLlm` アダプタ | 無い |
| T-028 | 組み立て点の一本化・anthropic を読み込まない・コンソールスクリプト | 無い(`composition.py` は設計書にだけある) |
| T-030 | overhead の較正 | 型だけ。`OVERHEAD` は空 |
| T-020 | フル処理100〜200問の実測(不可逆。実 CLI の実行と TTS) | 無い。T-028 と T-030 が前提 |

### Known TODOs / open issues (reference only — Issues are never read back as truth)

- ソースに TODO / FIXME は無い。
- T-029 の採取時、CLI 自身が最小ホームに `settings.json`(theme だけ)・`skills/`・`plugins/`・`projects/` を書いた。旧 T-024 の「settings.json があれば preflight が拒否する」と衝突する見込み。NFR-8 (b) と合わせて判断が要る。
- 追跡中のバイナリ・扱いにくいファイル: `docs/test/measurements/ui-e2e/01〜04-*.png`(旧 T-021)、空の `tests/fixtures/claude_cli/invalid_schema.stdout`(旧 T-029)。次の計画で受入基準の `artifact` として宣言しないと、`rein doctor` が coverage 不足を出す。

## Last updated

- 2026-09-25: 初回の onboarding 調査(rein 0.10.0 への移行時)。
