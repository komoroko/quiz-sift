# 実測ログ: 固定オーバーヘッドの較正表(`claude-haiku-4-5`)

NFR-7(C-019)の「呼び出し経路自身の固定オーバーヘッド」を、既知長の合成ブロックを k = 1, 2, 3 回反復した
入力で測った表。`tests/measurements/measure_overhead.py` がホストで書き、`tests/measurements/test_overhead_logs.py` が表から
切片を再計算して `src/quiz_sift/pipeline/overhead.py` の定数と突き合わせる。

argv は `ClaudeCliLlm` と同じ `build_argv` の形(`--safe-mode` を含む)で、`--system-prompt` と
`--json-schema` の値だけが合成のもの。`material` は合成ブロックの既知長 × k で、合計からの差し引きでは
出していない。入力合計は `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` の和。

## 表

| k | material | input_tokens | cache_creation_input_tokens | cache_read_input_tokens | 入力合計 | num_turns | skills_digest |
|---|---|---|---|---|---|---|---|
| 1 | 800 | 2025 | 0 | 0 | 2025 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 2 | 1600 | 2823 | 0 | 0 | 2823 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 3 | 2400 | 3625 | 0 | 0 | 3625 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |

## 記録

```yaml
model: claude-haiku-4-5
measured_at: '2026-10-06T19:26:38+09:00'
cli_version: 2.1.290
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
- claude-haiku-4-5
- --system-prompt
- Return ok=true.
- --exclude-dynamic-system-prompt-sections
- --strict-mcp-config
- --mcp-config
- '{"mcpServers":{}}'
- --setting-sources
- ''
- --tools
- ''
- --permission-mode
- default
- --safe-mode
argv_digest: 66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2
block:
  unit: ' the'
  repeat: 800
  tokens: 800
bucket: input_tokens
regression:
  intercept: 1224.3333333333335
  slope: 800.0
  num_turns: 2
  per_turn_tokens: 612.1666666666667
```
