# 実測ログ: キャッシュを迂回した再クラスタリング

R-3(C-025)の「キャッシュを迂回して再クラスタリングした2回の実行について割当一致率が記録され、その2回の
`llm_calls` が1回分の2倍になる」ことを人がホストで測った記録(元の T-047 の受入基準 A-2)。使ったのは
`cluster --fresh-run` の1実行だけで exit 0 まで走った列2本で、再開した列は使っていない(D-142)。この2列は
フル処理の試行の予算の外である(D-181)。判定は分類の呼び出しで行う(D-155): 各列で `classify_clusters` の
`llm_calls` が 1、`provider_invocations` が ⌈N/25⌉。

開始行と終了行は `out/aip-c01/meter.jsonl` の行を加工せずに写している。メソッド別の内訳は、標準エラーにある
`claude` 子プロセス1本ずつの記録を `--system-prompt` の値でメソッドに振り分けて数えた(標準エラーそのものは
写さない)。「出し直し」は、そのうち TRUNCATED で終わり同じ argv と stdin で出し直された子の数(D-231)。
`summarize_cluster` と `check_entailments` は1回の呼び出しが子プロセス1本で、出し直しのほかは再試行しない(D-101)
ので、`llm_calls` は子プロセス数から出し直しを除いた数。`classify_clusters` の `llm_calls` は終了行の
`llm_calls` からその2つを引いた値である。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `aip-c01` |
| 列の数 | 2 |

## 割当一致率

問題の対ごとに「同じクラスタか」が2本で一致する対の割合(Rand 指数。クラスタ ID は列ごとに振り直されるので
ID の対応は取らない)。

`0.9612`

## 列 1

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-23T00:31:16.470808+09:00 |
| コミット | `3149a11e90ac6bb1e09be2b4e090587e21bcd3fa` |
| 終了コード | `0` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
cluster
aip-c01
--fresh-run
```

開始行:

```json
{"record": "start", "run_id": "80bc0d1113c24921bc69c5127dc0dea4", "execution_id": "b69c3d9191124706b9bce51733db0de4", "started_at": "2026-10-22T15:31:18.103139+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "80bc0d1113c24921bc69c5127dc0dea4", "execution_id": "b69c3d9191124706b9bce51733db0de4", "started_at": "2026-10-22T15:31:18.103139+00:00", "finished_at": "2026-10-22T15:37:25.336184+00:00", "elapsed_sec": 367.2605565980775, "llm_calls": 35, "provider_invocations": 42, "model_turns": 84, "turns_by_model": {"claude-opus-5-5": 50, "claude-sonnet-5-5": 34}, "max_concurrency": 1, "tokens": {"input_tokens": 84, "cache_creation_input_tokens": 1389756, "cache_read_input_tokens": 30639, "output_tokens": 27298}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 50, "cache_creation_input_tokens": 830479, "cache_read_input_tokens": 17215, "output_tokens": 25939}, "claude-sonnet-5-5": {"input_tokens": 34, "cache_creation_input_tokens": 559277, "cache_read_input_tokens": 13424, "output_tokens": 1359}}, "calls_by_model": {"claude-opus-5-5": {"calls": 25, "input_tokens": 50, "cache_creation_input_tokens": 830479, "cache_read_input_tokens": 17215, "output_tokens": 25939}, "claude-sonnet-5-5": {"calls": 17, "input_tokens": 34, "cache_creation_input_tokens": 559277, "cache_read_input_tokens": 13424, "output_tokens": 1359}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.418363399999999, "status": "recorded", "computed": 6.0880109000000004, "relative_diff": 0.5470345823460989, "by_model": {"claude-opus-5-5": 4.674818, "claude-sonnet-5-5": 1.4131928999999999}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 532.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 50}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 34}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed_warning", "resetsAt": 1792969200, "five_hour_resetsAt": 1792701000, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.0, "resetsAt": 1792701000}, "seven_day": {"utilization": 0.79, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.69, "resetsAt": 1792701000}, "seven_day": {"utilization": 0.83, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 35, "provider_invocations": 42, "model_turns": 84, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 50, "claude-sonnet-5-5": 34}, "tokens": {"input_tokens": 84, "cache_creation_input_tokens": 1389756, "cache_read_input_tokens": 30639, "output_tokens": 27298}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 50, "cache_creation_input_tokens": 830479, "cache_read_input_tokens": 17215, "output_tokens": 25939}, "claude-sonnet-5-5": {"input_tokens": 34, "cache_creation_input_tokens": 559277, "cache_read_input_tokens": 13424, "output_tokens": 1359}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 367.2605565980775, "wall_clock_sec": 367.233045, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.418363399999999, "status": "recorded", "computed": 6.0880109000000004, "relative_diff": 0.5470345823460989}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "b69c3d9191124706b9bce51733db0de4", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.0, "resetsAt": 1792701000}, "seven_day": {"utilization": 0.79, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.69, "resetsAt": 1792701000}, "seven_day": {"utilization": 0.83, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

メソッド別の内訳:

| メソッド | llm_calls | provider_invocations | 出し直し |
|---|---|---|---|
| classify_clusters | 1 | 8 | 0 |
| summarize_cluster | 17 | 17 | 0 |
| check_entailments | 17 | 17 | 0 |

割当:

```json
{"c001": ["p001-q002", "p001-q013", "p001-q018", "p001-q031", "p001-q039", "p001-q069", "p001-q076", "p001-q113", "p001-q121", "p001-q123", "p001-q126", "p001-q164", "p001-q170", "p001-q171", "p001-q180", "p001-q181"], "c002": ["p001-q005", "p001-q009", "p001-q081", "p001-q116", "p001-q150", "p001-q156", "p001-q174"], "c003": ["p001-q001", "p001-q011", "p001-q015", "p001-q025", "p001-q027", "p001-q037", "p001-q043", "p001-q050", "p001-q051", "p001-q059", "p001-q064", "p001-q079", "p001-q101", "p001-q103", "p001-q104", "p001-q111", "p001-q127", "p001-q132", "p001-q134", "p001-q138", "p001-q142", "p001-q143", "p001-q166", "p001-q168", "p001-q172", "p001-q173", "p001-q175", "p001-q189"], "c004": ["p001-q006", "p001-q007", "p001-q014", "p001-q016", "p001-q021", "p001-q022", "p001-q023", "p001-q024", "p001-q026", "p001-q033", "p001-q035", "p001-q036", "p001-q040", "p001-q062", "p001-q071", "p001-q078", "p001-q084", "p001-q087", "p001-q095", "p001-q100", "p001-q120", "p001-q122", "p001-q124", "p001-q129", "p001-q130", "p001-q135", "p001-q149", "p001-q154", "p001-q158", "p001-q162", "p001-q167", "p001-q176", "p001-q182"], "c005": ["p001-q003", "p001-q010", "p001-q017", "p001-q030", "p001-q045", "p001-q114", "p001-q131", "p001-q165"], "c006": ["p001-q004", "p001-q019", "p001-q020", "p001-q032", "p001-q041", "p001-q052", "p001-q060", "p001-q082", "p001-q089", "p001-q090", "p001-q094", "p001-q096", "p001-q106", "p001-q115", "p001-q144", "p001-q146", "p001-q148", "p001-q153", "p001-q159", "p001-q163", "p001-q169", "p001-q183", "p001-q188"], "c007": ["p001-q008", "p001-q012", "p001-q029", "p001-q047", "p001-q058", "p001-q070", "p001-q091", "p001-q108", "p001-q128", "p001-q145", "p001-q152", "p001-q179", "p001-q186"], "c008": ["p001-q028", "p001-q038", "p001-q048", "p001-q049", "p001-q053", "p001-q066", "p001-q073", "p001-q077", "p001-q080", "p001-q092", "p001-q093", "p001-q110", "p001-q140", "p001-q151", "p001-q155", "p001-q160", "p001-q187"], "c009": ["p001-q034", "p001-q044", "p001-q088", "p001-q099", "p001-q112", "p001-q125", "p001-q136", "p001-q178", "p001-q185"], "c010": ["p001-q042", "p001-q061", "p001-q065", "p001-q074", "p001-q086", "p001-q147"], "c011": ["p001-q046", "p001-q083", "p001-q098", "p001-q109", "p001-q117", "p001-q118", "p001-q119", "p001-q133", "p001-q139", "p001-q177"], "c012": ["p001-q054", "p001-q055", "p001-q085"], "c013": ["p001-q056", "p001-q057", "p001-q063", "p001-q072", "p001-q190"], "c014": ["p001-q067", "p001-q068", "p001-q075", "p001-q097", "p001-q105", "p001-q107", "p001-q137", "p001-q184"], "c015": ["p001-q102"], "c016": ["p001-q141"], "c017": ["p001-q157", "p001-q161"]}
```

## 列 2

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-23T05:31:09.736673+09:00 |
| コミット | `3149a11e90ac6bb1e09be2b4e090587e21bcd3fa` |
| 終了コード | `0` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
cluster
aip-c01
--fresh-run
```

開始行:

```json
{"record": "start", "run_id": "b5096b8bd5064e22ae1e9ba606eb5f80", "execution_id": "ab2a2cfa7a1f496eb3f99521173dadb7", "started_at": "2026-10-22T20:31:10.889813+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "b5096b8bd5064e22ae1e9ba606eb5f80", "execution_id": "ab2a2cfa7a1f496eb3f99521173dadb7", "started_at": "2026-10-22T20:31:10.889813+00:00", "finished_at": "2026-10-22T20:36:19.907354+00:00", "elapsed_sec": 309.03314567694906, "llm_calls": 25, "provider_invocations": 32, "model_turns": 64, "turns_by_model": {"claude-opus-5-5": 40, "claude-sonnet-5-5": 24}, "max_concurrency": 1, "tokens": {"input_tokens": 64, "cache_creation_input_tokens": 1378236, "cache_read_input_tokens": 22729, "output_tokens": 22132}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 40, "cache_creation_input_tokens": 824807, "cache_read_input_tokens": 13500, "output_tokens": 21256}, "claude-sonnet-5-5": {"input_tokens": 24, "cache_creation_input_tokens": 553429, "cache_read_input_tokens": 9229, "output_tokens": 876}}, "calls_by_model": {"claude-opus-5-5": {"calls": 20, "input_tokens": 40, "cache_creation_input_tokens": 824807, "cache_read_input_tokens": 13500, "output_tokens": 21256}, "claude-sonnet-5-5": {"calls": 12, "input_tokens": 24, "cache_creation_input_tokens": 553429, "cache_read_input_tokens": 9229, "output_tokens": 876}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.249882900000001, "status": "recorded", "computed": 5.9453184, "relative_diff": 0.5558263288304293, "by_model": {"claude-opus-5-5": 4.552015, "claude-sonnet-5-5": 1.3933034}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 532.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 40}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 24}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed_warning", "resetsAt": 1792969200, "five_hour_resetsAt": 1792719000, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.0, "resetsAt": 1792719000}, "seven_day": {"utilization": 0.84, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.47, "resetsAt": 1792719000}, "seven_day": {"utilization": 0.87, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 25, "provider_invocations": 32, "model_turns": 64, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 40, "claude-sonnet-5-5": 24}, "tokens": {"input_tokens": 64, "cache_creation_input_tokens": 1378236, "cache_read_input_tokens": 22729, "output_tokens": 22132}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 40, "cache_creation_input_tokens": 824807, "cache_read_input_tokens": 13500, "output_tokens": 21256}, "claude-sonnet-5-5": {"input_tokens": 24, "cache_creation_input_tokens": 553429, "cache_read_input_tokens": 9229, "output_tokens": 876}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 309.03314567694906, "wall_clock_sec": 309.017541, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.249882900000001, "status": "recorded", "computed": 5.9453184, "relative_diff": 0.5558263288304293}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "ab2a2cfa7a1f496eb3f99521173dadb7", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.0, "resetsAt": 1792719000}, "seven_day": {"utilization": 0.84, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.47, "resetsAt": 1792719000}, "seven_day": {"utilization": 0.87, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

メソッド別の内訳:

| メソッド | llm_calls | provider_invocations | 出し直し |
|---|---|---|---|
| classify_clusters | 1 | 8 | 0 |
| summarize_cluster | 12 | 12 | 0 |
| check_entailments | 12 | 12 | 0 |

割当:

```json
{"c001": ["p001-q002", "p001-q005", "p001-q013", "p001-q018", "p001-q031", "p001-q039", "p001-q069", "p001-q076", "p001-q113", "p001-q118", "p001-q121", "p001-q123", "p001-q126", "p001-q164", "p001-q170", "p001-q171", "p001-q180", "p001-q181"], "c002": ["p001-q001", "p001-q011", "p001-q015", "p001-q025", "p001-q027", "p001-q037", "p001-q043", "p001-q050", "p001-q051", "p001-q059", "p001-q064", "p001-q075", "p001-q081", "p001-q101", "p001-q103", "p001-q104", "p001-q111", "p001-q127", "p001-q132", "p001-q134", "p001-q138", "p001-q142", "p001-q143", "p001-q150", "p001-q166", "p001-q168", "p001-q172", "p001-q173", "p001-q175", "p001-q189"], "c003": ["p001-q006", "p001-q007", "p001-q014", "p001-q021", "p001-q022", "p001-q023", "p001-q024", "p001-q026", "p001-q033", "p001-q035", "p001-q036", "p001-q040", "p001-q062", "p001-q071", "p001-q078", "p001-q084", "p001-q087", "p001-q095", "p001-q100", "p001-q120", "p001-q122", "p001-q124", "p001-q129", "p001-q130", "p001-q135", "p001-q149", "p001-q154", "p001-q158", "p001-q162", "p001-q176", "p001-q182"], "c004": ["p001-q003", "p001-q010", "p001-q017", "p001-q030", "p001-q045", "p001-q114", "p001-q131", "p001-q165"], "c005": ["p001-q004", "p001-q019", "p001-q020", "p001-q032", "p001-q041", "p001-q052", "p001-q060", "p001-q067", "p001-q068", "p001-q082", "p001-q089", "p001-q090", "p001-q096", "p001-q105", "p001-q106", "p001-q107", "p001-q115", "p001-q116", "p001-q144", "p001-q146", "p001-q148", "p001-q153", "p001-q159", "p001-q163", "p001-q169", "p001-q183", "p001-q188"], "c006": ["p001-q008", "p001-q009", "p001-q012", "p001-q016", "p001-q029", "p001-q046", "p001-q047", "p001-q058", "p001-q070", "p001-q083", "p001-q098", "p001-q102", "p001-q109", "p001-q117", "p001-q119", "p001-q128", "p001-q133", "p001-q139", "p001-q145", "p001-q152", "p001-q177", "p001-q179", "p001-q186"], "c007": ["p001-q028", "p001-q038", "p001-q048", "p001-q049", "p001-q053", "p001-q066", "p001-q073", "p001-q077", "p001-q080", "p001-q091", "p001-q092", "p001-q093", "p001-q108", "p001-q140", "p001-q141", "p001-q151", "p001-q155", "p001-q160", "p001-q184", "p001-q187"], "c008": ["p001-q034", "p001-q044", "p001-q088", "p001-q099", "p001-q112", "p001-q125", "p001-q136", "p001-q178", "p001-q185"], "c009": ["p001-q042", "p001-q061", "p001-q065", "p001-q074", "p001-q086", "p001-q097", "p001-q137", "p001-q147", "p001-q167"], "c010": ["p001-q054", "p001-q055", "p001-q094", "p001-q110"], "c011": ["p001-q056", "p001-q057", "p001-q063", "p001-q072", "p001-q079", "p001-q190"], "c012": ["p001-q156", "p001-q157", "p001-q161", "p001-q174"], "other": ["p001-q085"]}
```
