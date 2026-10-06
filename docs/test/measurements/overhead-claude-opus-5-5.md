# 実測ログ: 固定オーバーヘッドの較正表(`claude-opus-5-5`)

NFR-7(C-019)の「呼び出し経路自身の固定オーバーヘッド」を、既知長の合成ブロックを k = 1, 2, 3 回反復した
入力で測った表。`tests/measurements/measure_overhead.py` がホストで書き、`tests/measurements/test_overhead_logs.py` が表から
切片を再計算して `src/quiz_sift/pipeline/overhead.py` の定数と突き合わせる。

argv は `ClaudeCliLlm` と同じ `build_argv` の形(`--safe-mode` を含む)で、`--system-prompt` と
`--json-schema` の値だけが合成のもの。`material` は合成ブロックの既知長 × k で、合計からの差し引きでは
出していない。入力合計は `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` の和。

## 表

| k | material | input_tokens | cache_creation_input_tokens | cache_read_input_tokens | 入力合計 | num_turns | skills_digest |
|---|---|---|---|---|---|---|---|
| 1 | 800 | 2 | 1718 | 0 | 1720 | 2 | `ed36efc60ca4981b830e199aeebe39322fbc8a3e750e0b39b9daeae6afcd2774` |
| 2 | 1600 | 2 | 2515 | 0 | 2517 | 2 | `ed36efc60ca4981b830e199aeebe39322fbc8a3e750e0b39b9daeae6afcd2774` |
| 3 | 2400 | 2 | 3315 | 0 | 3317 | 2 | `ed36efc60ca4981b830e199aeebe39322fbc8a3e750e0b39b9daeae6afcd2774` |

## 記録

```yaml
model: claude-opus-5-5
measured_at: '2026-09-26T11:25:59+09:00'
cli_version: 2.1.283
collector: tests/measurements/measure_overhead.py
env_keys:
- HOME
- LANG
- PATH
- TZ
metered_vars_in_parent_env: []
argv:
- claude
- -p
- --output-format
- stream-json
- --verbose
- --max-turns
- '3'
- --json-schema
- '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}'
- --model
- claude-opus-5-5
- --system-prompt
- Return ok=true.
- --exclude-dynamic-system-prompt-sections
- --strict-mcp-config
- --mcp-config
- '{"mcpServers":{}}'
- --settings
- '{"permissions":{"deny":["Agent","AskUserQuestion","Bash","BashOutput","CronCreate","CronDelete","CronList","DesignSync","Edit","EnterPlanMode","EnterWorktree","ExitPlanMode","ExitWorktree","Glob","Grep","KillShell","LS","LSP","ListAgents","Monitor","MultiEdit","NotebookEdit","NotebookRead","PushNotification","Read","RemoteTrigger","ReportFindings","ScheduleWakeup","SendMessage","Skill","Task","TaskCreate","TaskGet","TaskList","TaskOutput","TaskStop","TaskUpdate","TodoWrite","ToolSearch","WebFetch","WebSearch","Write"]}}'
- --permission-mode
- default
- --safe-mode
argv_digest: caf33f6f31c8a4b02c627ca95209cc48dc1b4e616984c79549e4bcaa0d773954
block:
  unit: ' the'
  repeat: 800
  tokens: 800
bucket: cache_creation_input_tokens
regression:
  intercept: 921.0
  slope: 798.5
  num_turns: 2
  per_turn_tokens: 460.5
```
