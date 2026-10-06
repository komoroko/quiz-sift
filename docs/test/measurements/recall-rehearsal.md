# 実測ログ: 合成デッキの recall 段までのリハーサル

NFR-7 の合成リハーサル(C-026)。合成デッキ `tests/fixtures/decks/synthetic-nofm/` を使い捨ての作業ルートに写し、
ホストの実 CLI で `cluster --fresh-run` を走らせ、同じ列に `recall --resume` を足した記録である。

この md は `tests/measurements/test_recall_rehearsal_logs.py render` が journal・`meter.jsonl`・recall の成果物の
`report` から作った。LLM は呼んでいない。開始行と終了行は meter.jsonl の行を加工せずに写している。合成デッキの
文は教材ではないが、想起素材の本文は写さず、件数と ID だけを書く。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `synthetic-nofm` |
| デッキのダイジェスト | `sha256:74c4a3d60cacd05b456c45dd0f2368fdafb76e4e6ebe6dd2128c630ce0e00a38` |
| 問題数 | 16 |
| 複数正解の問題数 | 8 |
| CLI | `2.1.291 (Claude Code)` |
| 実行数 | 2 |

## 想起素材の検査

recall の成果物の `report` の件数。成果物は、検査と含意判定をすべて通ったときだけ書かれる
(`recall.generate.generate_recall_set`)。

| 検査 | 件数 |
|---|---|
| `ungrounded_keys` | 0 |
| `missing_options` | 0 |
| `duplicate_keys` | 0 |
| `generic_keys` | 0 |
| `verbatim` | 0 |
| `empty` | 0 |
| `answer_mismatch` | 0 |

## 含意判定

| 項目 | 値 |
|---|---|
| 素材の件数 | 16 |
| 判定に渡る主張の件数(素材から数えた値) | 41 |

判定の結果は製品がログに出さないので、ここには実測した値が無い。recall の成果物は、含意判定が `consistent`
のときだけ書かれる(`safety.entailment.emit_if_consistent`。それ以外は `EntailmentRejected` で止まる)。上の
素材は exit 0 の recall が書いた成果物から数えたもので、判定の通過はそこから推論している。主張の件数も判定器に
渡った件数の実測ではなく、素材ごとの答えの要素と識別ポイント1件を数えた値である。

## 問題ごとの再生成回数

| 問題 | 再生成回数 |
|---|---|
| `db-01` | 0 |
| `db-02` | 0 |
| `db-03` | 0 |
| `db-04` | 0 |
| `k8s-01` | 0 |
| `k8s-02` | 0 |
| `k8s-03` | 0 |
| `k8s-04` | 0 |
| `ml-01` | 0 |
| `ml-02` | 0 |
| `ml-03` | 0 |
| `ml-04` | 0 |
| `net-01` | 0 |
| `net-02` | 0 |
| `net-03` | 0 |
| `net-04` | 0 |

## 呼び出し数とトークン数

トークン数は終了行の `tokens` の4区分(input・cache_creation_input・cache_read_input・output)の和。190 問への
外挿は、cluster と recall の2実行の和を問題数の比 190/16 で伸ばした値である。
cluster 段の分類は全問を1度に見るので問題数に比例しないが、ここでは比例として扱う。

| 項目 | リハーサル | 190 問への外挿 |
|---|---|---|
| 呼び出し数 | 66 | 783.75 |
| プロバイダ呼び出し数 | 66 | 783.75 |
| トークン数 | 161490 | 1917693.75 |

## レート枠

utilization の生の値(終了行の `quota_at_start`・`quota_at_end`)。小数2桁に量子化されているので、差分を外挿には
使わない(D-218)。

| 実行 | execution_id | 枠 | 開始時 | 終了時 | resetsAt |
|---|---|---|---|---|---|
| 1 | `3db712b5ec5d420bbe763e9c366711a3` | five_hour | 0.34 | 0.35 | 1791336000 |
| 1 | `3db712b5ec5d420bbe763e9c366711a3` | seven_day | 0.49 | 0.49 | 1791759600 |
| 2 | `0fd5d3f3943f4d97a50da72af9a60395` | five_hour | 0.36 | 0.41 | 1791336000 |
| 2 | `0fd5d3f3943f4d97a50da72af9a60395` | seven_day | 0.49 | 0.49 | 1791759600 |

前サイクルの列 `591e7bb9ae6145d284a0ec993371c59c`(full-run.md の終了行 4 行、トークン数 3945015)で、
実行ごとの utilization の増分の和をトークン数の和で割った比:

| 枠 | utilization / トークン |
|---|---|
| five_hour | 3.59948e-07 |
| seven_day | 2.78833e-08 |

190 問へ外挿したトークン数にこの比を掛けた、cluster から recall までの枠の使用率の推定:

| 枠 | 推定使用率 |
|---|---|
| five_hour | 0.69027 |
| seven_day | 0.0534716 |

## 実行 1(cluster)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-07T05:41:22.354719+09:00 |
| コミット | `b2e06f25a3ae509ec0b84ce8d7c3f7c2ed5f7ffb` |
| 終了コード | `0` |
| run_id | `34d28c484cf14945a0b4b6b70bb7482a` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
/tmp/t068-rehearsal.TrNz/root
cluster
synthetic-nofm
--fresh-run
```

開始行:

```json
{"record": "start", "run_id": "34d28c484cf14945a0b4b6b70bb7482a", "execution_id": "3db712b5ec5d420bbe763e9c366711a3", "started_at": "2026-10-06T20:41:23.575746+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "NAME", "NoDefaultCurrentDirectoryInExePath", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "34d28c484cf14945a0b4b6b70bb7482a", "execution_id": "3db712b5ec5d420bbe763e9c366711a3", "started_at": "2026-10-06T20:41:23.575746+00:00", "finished_at": "2026-10-06T20:42:30.549270+00:00", "elapsed_sec": 66.97698796800978, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "turns_by_model": {"claude-opus-5-5": 18}, "max_concurrency": 1, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 13913, "cache_read_input_tokens": 5340, "output_tokens": 5397}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 18, "cache_creation_input_tokens": 13913, "cache_read_input_tokens": 5340, "output_tokens": 5397}}, "calls_by_model": {"claude-opus-5-5": {"calls": 9, "input_tokens": 18, "cache_creation_input_tokens": 13913, "cache_read_input_tokens": 5340, "output_tokens": 5397}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.22038400000000002, "status": "recorded", "computed": 0.178645, "relative_diff": 0.23364213943855147, "by_model": {"claude-opus-5-5": 0.178645}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 530.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-06", "cli_version": "2.1.290", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 18}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1791336000, "five_hour_resetsAt": 1791336000, "seven_day_resetsAt": 1791759600}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.34, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.35, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 18}, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 13913, "cache_read_input_tokens": 5340, "output_tokens": 5397}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 18, "cache_creation_input_tokens": 13913, "cache_read_input_tokens": 5340, "output_tokens": 5397}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 66.97698796800978, "wall_clock_sec": 66.973524, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.22038400000000002, "status": "recorded", "computed": 0.178645, "relative_diff": 0.23364213943855147}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "3db712b5ec5d420bbe763e9c366711a3", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.34, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.35, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 2(recall)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-07T05:42:30.601903+09:00 |
| コミット | `b2e06f25a3ae509ec0b84ce8d7c3f7c2ed5f7ffb` |
| 終了コード | `0` |
| run_id | `34d28c484cf14945a0b4b6b70bb7482a` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
/tmp/t068-rehearsal.TrNz/root
recall
synthetic-nofm
--resume
34d28c484cf14945a0b4b6b70bb7482a
```

開始行:

```json
{"record": "start", "run_id": "34d28c484cf14945a0b4b6b70bb7482a", "execution_id": "0fd5d3f3943f4d97a50da72af9a60395", "started_at": "2026-10-06T20:42:31.734262+00:00", "cache_mode": "resume", "stage": "recall", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "NAME", "NoDefaultCurrentDirectoryInExePath", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "34d28c484cf14945a0b4b6b70bb7482a", "execution_id": "0fd5d3f3943f4d97a50da72af9a60395", "started_at": "2026-10-06T20:42:31.734262+00:00", "finished_at": "2026-10-06T20:51:25.136681+00:00", "elapsed_sec": 533.4351653000049, "llm_calls": 57, "provider_invocations": 57, "model_turns": 114, "turns_by_model": {"claude-haiku-4-5": 32, "claude-opus-5-5": 82}, "max_concurrency": 1, "tokens": {"input_tokens": 28501, "cache_creation_input_tokens": 32433, "cache_read_input_tokens": 31201, "output_tokens": 44687}, "tokens_by_model": {"claude-haiku-4-5": {"input_tokens": 28419, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 39404}, "claude-opus-5-5": {"input_tokens": 82, "cache_creation_input_tokens": 32433, "cache_read_input_tokens": 31201, "output_tokens": 5283}}, "calls_by_model": {"claude-haiku-4-5": {"calls": 16, "input_tokens": 28419, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 39404}, "claude-opus-5-5": {"calls": 41, "input_tokens": 82, "cache_creation_input_tokens": 32433, "cache_read_input_tokens": 31201, "output_tokens": 5283}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.5971312, "status": "recorded", "computed": 0.4998322, "relative_diff": 0.19466332901321678, "by_model": {"claude-haiku-4-5": 0.225439, "claude-opus-5-5": 0.2743932}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-haiku-4-5": {"status": "recorded", "per_turn_tokens": 612.1666666666667, "bucket": "input_tokens", "measured_at": "2026-10-06", "cli_version": "2.1.290", "argv_digest": "66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}, "claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 530.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-06", "cli_version": "2.1.290", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 82}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1791336000, "five_hour_resetsAt": 1791336000, "seven_day_resetsAt": 1791759600}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.36, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.41, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 66, "provider_invocations": 66, "model_turns": 132, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 100, "claude-haiku-4-5": 32}, "tokens": {"input_tokens": 28519, "cache_creation_input_tokens": 46346, "cache_read_input_tokens": 36541, "output_tokens": 50084}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 100, "cache_creation_input_tokens": 46346, "cache_read_input_tokens": 36541, "output_tokens": 10680}, "claude-haiku-4-5": {"input_tokens": 28419, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 39404}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 600.4121532680147, "wall_clock_sec": 601.560935, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.8175152, "status": "recorded", "computed": 0.6784772, "relative_diff": 0.2049265620126955}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "3db712b5ec5d420bbe763e9c366711a3", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.34, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.35, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}}, {"execution_id": "0fd5d3f3943f4d97a50da72af9a60395", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.36, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1791336000, "five_hour": {"utilization": 0.41, "resetsAt": 1791336000}, "seven_day": {"utilization": 0.49, "resetsAt": 1791759600}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```
