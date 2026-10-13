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
| 1 | 800 | 2 | 1862 | 0 | 1864 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 2 | 1600 | 2 | 2661 | 0 | 2663 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |
| 3 | 2400 | 2 | 3461 | 0 | 3463 | 2 | `348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14` |

## 記録

```yaml
model: claude-opus-5-5
measured_at: '2026-10-13T16:22:49+09:00'
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
- claude-opus-5-5
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
argv_digest: 7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86
block:
  unit: ' the'
  repeat: 800
  tokens: 800
bucket: cache_creation_input_tokens
regression:
  intercept: 1064.3333333333335
  slope: 799.5
  num_turns: 2
  per_turn_tokens: 532.1666666666667
```
