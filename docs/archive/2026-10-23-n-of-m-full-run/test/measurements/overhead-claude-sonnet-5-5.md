# 実測ログ: 固定オーバーヘッドの較正表(`claude-sonnet-5-5`)

NFR-7(C-019)の「呼び出し経路自身の固定オーバーヘッド」を、既知長の合成ブロックを k = 1, 2, 3 回反復した
入力で測った表。`tests/measurements/measure_overhead.py` がホストで書き、`tests/measurements/test_overhead_logs.py` が表から
切片を再計算して `src/quiz_sift/pipeline/overhead.py` の定数と突き合わせる。

argv は `ClaudeCliLlm` と同じ `build_argv` の形(`--safe-mode` を含む)で、`--system-prompt` と
`--json-schema` の値だけが合成のもの。`material` は合成ブロックの既知長 × k で、合計からの差し引きでは
出していない。入力合計は `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` の和。

## 表

| k | material | input_tokens | cache_creation_input_tokens | cache_read_input_tokens | 入力合計 | num_turns | skills_digest |
|---|---|---|---|---|---|---|---|
| 1 | 800 | 2 | 1864 | 0 | 1866 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 2 | 1600 | 2 | 2665 | 0 | 2667 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 3 | 2400 | 2 | 3466 | 0 | 3468 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |

## 記録

```yaml
model: claude-sonnet-5-5
measured_at: '2026-10-13T16:22:59+09:00'
cli_version: 2.1.296
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
- claude-sonnet-5-5
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
argv_digest: ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06
block:
  unit: ' the'
  repeat: 800
  tokens: 800
bucket: cache_creation_input_tokens
regression:
  intercept: 1065.0
  slope: 801.0
  num_turns: 2
  per_turn_tokens: 532.5
```
