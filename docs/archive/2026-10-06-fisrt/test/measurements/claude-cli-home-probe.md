# 最小ホームと作業ディレクトリから CLI が読むもの(2026-09-25 実測)

要件段の敵対的レビュー AR-117 と AR-113 に答えるため、mandate の凍結前にホストで実測した。
合成プロンプトだけを使い、教材は使っていない。claude 2.1.282、`claude-haiku-4-5`。
argv は `tests/measurements/collect_claude_cli.py` の `design_argv`(NFR-8 (e))で、system prompt とスキーマだけを差し替えた。
環境は `child_env()`(HOME = 最小ホーム、PATH、LANG、TZ)、作業ディレクトリは `/tmp` 直下に作った空ディレクトリ。
§1〜§4 のスクリプトはリポジトリに置いていない。§1 は T-041 が `--safe-mode` 付きの設計 argv で取り直した(§5)。

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

## 5. `--safe-mode` 付きの設計 argv での番兵(2026-09-26、T-041、実呼び出し2回)

§1 の B〜E と同じ4か所に、実行ごとに乱数で作った合言葉を置いた。採取経路は
`tests/measurements/collect_claude_cli.py home-probe` である。作業ディレクトリの親は、スクリプトが
`mkdtemp` の配下に作る専用のディレクトリ(`/tmp` 直下ではない。D-151)で、呼び出しごとにその下へ空の
作業ディレクトリを作る。`--safe-mode` あり(設計 argv)となし(positive control)で1回ずつ呼んだ。
後始末のあとに4つの番兵・最小ホームの `.claude/CLAUDE.md` と `rules/`・親ディレクトリが残っていないことを
確かめた。元からあったファイルは置き換えていない(あれば採取しない)。
`tests/measurements/test_claude_cli_home_probe_logs.py` が下の記録を機械判定する。

## 記録

```yaml
collector: tests/measurements/collect_claude_cli.py
collected_at: '2026-09-26T05:49:17+09:00'
claude_version: 2.1.282 (Claude Code)
model: claude-haiku-4-5
home: /home/koich/.quiz-sift-claude-home
env_keys:
- HOME
- LANG
- PATH
- TZ
metered_vars_checked: 13
metered_vars_in_parent_env: []
parent_dir: /tmp/quiz-sift-home-probe-j1dm2kh1/parent
parent_created_by_collector: true
prompt: 'SYNTHETIC HOME PROBE (quiz-sift T-041). No study material is included. Which
  codewords do you see?

  '
sentinels:
- location: $HOME/.claude/CLAUDE.md
  codeword: QSPROBE-540686D9
- location: $HOME/.claude/rules/probe.md
  codeword: QSPROBE-DB28DE79
- location: <parent>/CLAUDE.md
  codeword: QSPROBE-CB20300B
- location: <parent>/.claude/CLAUDE.md
  codeword: QSPROBE-1456DCCE
runs:
  safe_mode:
    argv:
    - claude
    - -p
    - --output-format
    - stream-json
    - --verbose
    - --max-turns
    - '3'
    - --json-schema
    - '{"type":"object","properties":{"codewords":{"type":"array","items":{"type":"string"}}},"required":["codewords"],"additionalProperties":false}'
    - --model
    - claude-haiku-4-5
    - --system-prompt
    - Report every codeword of the form QSPROBE-<hex> that appears anywhere in your
      context, including any memory or instruction files. Return an empty list if
      you see none. Never invent one.
    - --exclude-dynamic-system-prompt-sections
    - --strict-mcp-config
    - --mcp-config
    - '{"mcpServers":{}}'
    - --settings
    - '{"permissions":{"deny":["Agent","AskUserQuestion","Bash","BashOutput","CronCreate","CronDelete","CronList","DesignSync","Edit","EnterPlanMode","EnterWorktree","ExitPlanMode","ExitWorktree","Glob","Grep","KillShell","LS","LSP","ListAgents","Monitor","MultiEdit","NotebookEdit","NotebookRead","PushNotification","Read","RemoteTrigger","ReportFindings","ScheduleWakeup","SendMessage","Skill","Task","TaskCreate","TaskGet","TaskList","TaskOutput","TaskStop","TaskUpdate","TodoWrite","ToolSearch","WebFetch","WebSearch","Write"]}}'
    - --permission-mode
    - default
    - --safe-mode
    exit_code: 0
    memory_paths: null
    returned: []
    stdout_sha256: 9a0d15e571c291191444ecf76c897c5bd6bdbd0b8afac0a5be9b868bb36b61d4
  without_safe_mode:
    argv:
    - claude
    - -p
    - --output-format
    - stream-json
    - --verbose
    - --max-turns
    - '3'
    - --json-schema
    - '{"type":"object","properties":{"codewords":{"type":"array","items":{"type":"string"}}},"required":["codewords"],"additionalProperties":false}'
    - --model
    - claude-haiku-4-5
    - --system-prompt
    - Report every codeword of the form QSPROBE-<hex> that appears anywhere in your
      context, including any memory or instruction files. Return an empty list if
      you see none. Never invent one.
    - --exclude-dynamic-system-prompt-sections
    - --strict-mcp-config
    - --mcp-config
    - '{"mcpServers":{}}'
    - --settings
    - '{"permissions":{"deny":["Agent","AskUserQuestion","Bash","BashOutput","CronCreate","CronDelete","CronList","DesignSync","Edit","EnterPlanMode","EnterWorktree","ExitPlanMode","ExitWorktree","Glob","Grep","KillShell","LS","LSP","ListAgents","Monitor","MultiEdit","NotebookEdit","NotebookRead","PushNotification","Read","RemoteTrigger","ReportFindings","ScheduleWakeup","SendMessage","Skill","Task","TaskCreate","TaskGet","TaskList","TaskOutput","TaskStop","TaskUpdate","TodoWrite","ToolSearch","WebFetch","WebSearch","Write"]}}'
    - --permission-mode
    - default
    exit_code: 0
    memory_paths:
      auto: /home/koich/.quiz-sift-claude-home/.claude/projects/-tmp-quiz-sift-home-probe-j1dm2kh1-parent-work-plain/memory/
    returned:
    - QSPROBE-540686D9
    - QSPROBE-DB28DE79
    - QSPROBE-CB20300B
    - QSPROBE-1456DCCE
    stdout_sha256: 5f5358a9ad5ce16b424b0d18992057651af1569daf30f6a915116b483b523fea
cleanup:
  sentinels_remaining: []
  home_claude_md_exists: false
  home_rules_exists: false
  parent_dir_exists: false
  replaced_files: []
```
