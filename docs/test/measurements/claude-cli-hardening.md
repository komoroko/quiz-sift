# 実測ログ: claude CLI のフラグ(第2段)

NFR-8 のフラグの実測の第2段(T-060 / C-023・C-024)。採取側は独自の argv を持たず、製品の
`build_argv`(`ClaudeCliLlm` と同じ関数)をそのまま使う。その最終 argv(拒否リスト無し)で:

1. `init.tools` が `{StructuredOutput}` と一致するか(C-024)。
2. SEC-001 の番兵(D-193、D-213): 最小ホームの `.claude/settings.json` をその場で一時的に書き換え、到達できない
   `ANTHROPIC_BASE_URL` を `env` に置く。最終 argv からユーザ設定の遮断(`--setting-sources` とその値)だけを
   外した argv では番兵のアドレスへの接続の失敗で落ち、最終 argv では成功するか(C-023)。起動は中身検査を
   通さない直接起動。後始末は元のバイト列の sha256 の一致で確かめる。資格情報は写さない。

`tests/measurements/collect_claude_cli.py hardening` が書き、`tests/measurements/test_claude_cli_hardening_logs.py`
が機械判定する。採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` /
`LANG` / `TZ` の4つだけ。送ったのは合成プロンプトだけ。接続の失敗は stderr と `result` の本文から分類する
(2.1.290 は stream-json の下で stderr を空にし、`result` の本文に書く)。

## 記録

```yaml
collector: tests/measurements/collect_claude_cli.py
collected_at: '2026-10-06T19:21:48+09:00'
claude_version: 2.1.290 (Claude Code)
model: claude-haiku-4-5
home: /home/koich/.quiz-sift-claude-home
env_keys:
- HOME
- LANG
- PATH
- TZ
metered_vars_checked: 13
metered_vars_in_parent_env: []
prompt: 'SYNTHETIC HARDENING PROBE (quiz-sift T-060). No study material is included.
  What is 2 + 3?

  '
launch: subprocess.run(direct, no preflight)
init_tools_run:
  argv: &id001
  - claude
  - -p
  - --output-format
  - stream-json
  - --verbose
  - --max-turns
  - '3'
  - --json-schema
  - '{"type":"object","properties":{"answer":{"type":"integer"}},"required":["answer"],"additionalProperties":false}'
  - --model
  - claude-haiku-4-5
  - --system-prompt
  - You are a calculator. Return only the requested structured answer.
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
  exit_code: 0
  stderr: ''
  init_tools:
  - StructuredOutput
  result_is_error: false
  result_text: '{"answer":5}'
  structured_output:
    answer: 5
  structured_output_returned: true
  stdout_sha256: 1dc7e1a9dc7be5fa1552641d5f0010ced46afe985aca7ec7c9bca7969648fd43
sentinel:
  path: $HOME/.claude/settings.json
  env:
    ANTHROPIC_BASE_URL: http://127.0.0.1:9
  sha256_original: 27dafb2742d0da69a49cc8d206fc9cc429feff09cc3738addcf590d9c4358f97
  sha256_with_sentinel: 8b3a34f76ed6be2c4b9edc802c784bb4e0f77edb3818c21dd1036c8caa5971f5
  sha256_after_restore: 27dafb2742d0da69a49cc8d206fc9cc429feff09cc3738addcf590d9c4358f97
  restored_content:
    theme: dark
  credentials_copied: false
sentinel_runs:
  without_settings_block:
    argv:
    - claude
    - -p
    - --output-format
    - stream-json
    - --verbose
    - --max-turns
    - '3'
    - --json-schema
    - '{"type":"object","properties":{"answer":{"type":"integer"}},"required":["answer"],"additionalProperties":false}'
    - --model
    - claude-haiku-4-5
    - --system-prompt
    - You are a calculator. Return only the requested structured answer.
    - --exclude-dynamic-system-prompt-sections
    - --strict-mcp-config
    - --mcp-config
    - '{"mcpServers":{}}'
    - --tools
    - ''
    - --permission-mode
    - default
    - --safe-mode
    exit_code: 1
    stderr: ''
    init_tools:
    - StructuredOutput
    result_is_error: true
    result_text: 'API Error: Connection refused — a firewall or proxy may be blocking
      it (ECONNREFUSED)'
    structured_output: null
    structured_output_returned: false
    stdout_sha256: a24b00782c41f6ee82c0a014ceaeb398fd62d77d43696866000c1260074cc3b6
    failure_cause: connection_refused
  final_argv:
    argv: *id001
    exit_code: 0
    stderr: ''
    init_tools:
    - StructuredOutput
    result_is_error: false
    result_text: '{"answer":5}'
    structured_output:
      answer: 5
    structured_output_returned: true
    stdout_sha256: 89a621036dad397bd5a1ace7c606683f44ea74b3f99e2d5cc74e9b54aa2d9878
    failure_cause: null
```

## 結論

init.tools: 一致({StructuredOutput})
番兵: 遮断なしで connection_refused、最終 argv で 成功
後始末: 元のバイト列に戻った
