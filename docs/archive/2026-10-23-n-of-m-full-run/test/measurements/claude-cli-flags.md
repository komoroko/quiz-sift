# 実測ログ: claude CLI のフラグ(第1段)

NFR-8 のフラグの実測の第1段(T-054 / C-023・C-024)。製品の argv を変える前に、ホストの `claude` の
`--help` に現れるフラグのうち、ユーザ設定の読み込み元とツールの有効化に関わるものを列挙し、有効ツールの
許可リストの候補フラグごとに、拒否リスト(`--settings` の `permissions.deny`)を外した argv での
`init.tools` と、`--json-schema` の構造化出力が返ったかを記録する。権限の自動承認のフラグ
(`--allowedTools` など)は許可リストの候補に数えない。
`tests/measurements/collect_claude_cli.py flags` が書き、`tests/measurements/test_claude_cli_flags_logs.py` が
機械判定する。

採取は最小ホーム(ADR-014)を `HOME` に向け、子プロセスへ渡す環境は `HOME` / `PATH` / `LANG` / `TZ` の
4つだけ。送ったのは合成プロンプトだけ。`--help` の本文は載せず、sha256 だけを残す。

ユーザ設定の遮断は、ここでは `--help` にフラグがあるかだけを見る。効いているかの番兵の実測は第2段
(製品の argv を変えた後)で行う。

## 記録

```yaml
collector: tests/measurements/collect_claude_cli.py
collected_at: '2026-10-06T17:22:07+09:00'
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
prompt: 'SYNTHETIC FLAG PROBE (quiz-sift T-054). No study material is included. What
  is 2 + 3?

  '
help_sha256: 550db4f88ae1d97bf7244ca96dc89554aabd3978241ceb69f79001e816c35f09
settings_flags:
- --setting-sources
- --settings
- --safe-mode
- --bare
- --restricted
tool_flags:
- --tools
- --allowedTools
- --disallowedTools
- --restricted
- --bare
- --strict-mcp-config
- --mcp-config
- --disable-slash-commands
allowlist_runs:
- flag: --tools
  value: ''
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
  - --permission-mode
  - default
  - --safe-mode
  - --tools
  - ''
  exit_code: 0
  stderr: ''
  init_tools:
  - StructuredOutput
  result_is_error: false
  structured_output:
    answer: 5
  structured_output_returned: true
  stdout_sha256: 1daa51d8479a66834fe3eafcd6cf1972504bb2c6edc996bba16328e796e2f9df
- flag: --tools
  value: StructuredOutput
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
  - --permission-mode
  - default
  - --safe-mode
  - --tools
  - StructuredOutput
  exit_code: 0
  stderr: ''
  init_tools:
  - StructuredOutput
  result_is_error: false
  structured_output:
    answer: 5
  structured_output_returned: true
  stdout_sha256: 2d3a9db04e2ddec3e573795907319cdf8dcfce4be7c03ee3e4d5bd18d7b373f4
```

## 結論

ユーザ設定の遮断: あり(--setting-sources)
許可リスト: 一致あり(--tools "")
