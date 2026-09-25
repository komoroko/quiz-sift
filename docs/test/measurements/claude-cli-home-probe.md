# 最小ホームと作業ディレクトリから CLI が読むもの(2026-09-25 実測)

要件段の敵対的レビュー AR-117 と AR-113 に答えるため、mandate の凍結前にホストで実測した。
合成プロンプトだけを使い、教材は使っていない。claude 2.1.282、`claude-haiku-4-5`。
argv は `tests/measurements/collect_claude_cli.py` の `design_argv`(NFR-8 (e))で、system prompt とスキーマだけを差し替えた。
環境は `child_env()`(HOME = 最小ホーム、PATH、LANG、TZ)、作業ディレクトリは `/tmp` 直下に作った空ディレクトリ。
スクリプトはリポジトリに置いていない。再現はタスク側で行う。

## 1. 番兵による読み込み場所の判定(実呼び出し2回)

候補の場所に `The codeword is QSPROBE-<文字>.` だけを書いたファイルを置き、見えている合言葉をすべてスキーマで返させた。
呼び出しが終わると番兵はすべて消した。

| 場所 | 設計 argv | 設計 argv + `--safe-mode` |
|---|---|---|
| A `$HOME/CLAUDE.md` | 読まれない | 読まれない |
| B `$HOME/.claude/CLAUDE.md` | **読まれる** | 読まれない |
| C `$HOME/.claude/rules/probe.md` | **読まれる** | 読まれない |
| D `/tmp/CLAUDE.md`(作業ディレクトリの親) | **読まれる** | 読まれない |
| E `/tmp/.claude/CLAUDE.md` | **読まれる** | 読まれない |

- `--system-prompt` と `--exclude-dynamic-system-prompt-sections` では、メモリの注入は止まらない。
- `--safe-mode` のとき、`init.memory_paths` は `null`(自動メモリも無効)で、`apiKeySource` は `none` のまま成功した(OAuth は使える)。
- `init.tools` はどちらも `["StructuredOutput"]`、`mcp_servers` は `[]`、`plugins` は builtin の2件だった。
- `--bare` は OAuth を読まず API キーだけで認証する(`claude --help`)。NFR-8 に反するので使えない。

## 2. `--safe-mode` の下での `settings.json` の `env`(実呼び出し1回)

最小ホームの `.claude/settings.json` を `{"theme": "dark", "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}` に一時的に変え、`--safe-mode` 付きで呼んだ。
終わってから元のバイト列に戻し、sha256 が一致することを確かめた。

- 終了コード 1。`result` は `is_error: true`・`api_error_status: null`・`result: "API Error: Connection refused ..."`。
- **`--safe-mode` の下でも `settings.json` の `env` は効く**。NFR-8 (b) の `settings.json` の中身検査(D-122)が要る。

## 3. レート枠が尽きたときの形(実行ファイルの読み取り。実呼び出しなし)

claude 2.1.282 の実行ファイルに埋め込まれたスキーマ定義を読んだ。**実行時に観測したものではない**。

- `rate_limit_info.status` は `allowed` / `allowed_warning` / `rejected` の3値。説明文は `rejected` を枠の使い切り(hard exhaustion)としている。
- `rateLimitType` は `five_hour` / `seven_day` / `seven_day_opus` / `seven_day_sonnet` / `seven_day_overage_included` / `overage`。
- `rate_limit_event` は、レート枠の状態が変わるたびに出す作りになっている(リスナーが状態の変化を受けて出す)。
- `result` イベントは、API エラーでターンが終わったとき `api_error_status`(HTTP の状態コード)を持ちうる。
- 枠切れのときに `status: rejected` のイベントが実際に届くかどうか、`result` がどの形になるかは**未観測**。

## 4. 作業ディレクトリの祖先の `settings.json` の `env`(2026-09-26、実呼び出し1回、AR-128)

`/tmp/.claude/settings.json` と `/tmp/.claude/settings.local.json` を置いた。どちらも中身は `{"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}` である。そのうえで、`/tmp` 配下の空の一時ディレクトリを cwd にして、`--safe-mode` 付きの設計 argv を haiku で呼んだ。事前に `/tmp/.claude` が無いことを確かめた。終わったあとはディレクトリごと消した。

- 終了コード 0。`result` は `subtype: success`・`is_error: false`・`num_turns: 2`。つまり向け替えは効いていない。
- **祖先の `.claude/settings.json` と `settings.local.json` は、`--safe-mode` の下では読まれない**。`env` の経路として残るのは、HOME 側の `settings.json`(§2。D-122 の中身検査で塞ぐ)と管理設定だけである。
- `init`:
  - `tools` は `["StructuredOutput"]`
  - `memory_paths` は `null`
  - `apiKeySource` は `none`
  - `claude_code_version` は `2.1.282`
  - **`skills` は `--safe-mode` の下でも存在した**(名前が16件並ぶ)
- `rate_limit_event` は、この呼び出しでも届いた(`status: allowed`・`isUsingOverage: false`)。
- 採取スクリプトはスクラッチにだけあり、リポジトリには置いていない。§1〜§2 と同じ扱いである。
