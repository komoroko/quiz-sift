# 実測ログ: フル処理の実行の列

R-3・R-7・NFR-7・NFR-8(C-025、C-007)の実デッキのフル処理を、1つの `run_id` の実行の列として人がホストで
走らせた記録(元の T-047 の受入基準 A-1・A-4・A-5・A-6 と試行の予算)。列は `cluster --fresh-run` で始め、
続く段と週次枠で止まった段を `--resume <run_id>` で足した(D-190)。

各実行は `tests/measurements/test_full_run_logs.py exec` が製品の CLI を子プロセスとして走らせ、その argv・
実行時刻・コミット ID・終了コードを記録した。この md は人が同じ作業ルートに対して `render` で作った(D-220)。
開始行と終了行は `out/aip-c01/meter.jsonl` の行を加工せずに写している。行にあるのは件数・ID・ダイジェスト・
環境変数名・レート枠の値だけで、教材本文は無い(NFR-2)。`tests/measurements/test_full_run_logs.py` がこの記録を
機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| デッキ | `aip-c01` |
| run_id | `384e3e4258d544a4a7b17fc05a6d9db5` |
| 実行数 | 5 |

## 試行

meter.jsonl の開始行のうち journal の最初の実行より後のものを run_id ごとに数えた(exec を通さずに起動した実行も
入る)。`--fresh-run` で始めた列1本を1回と数え、同じ列の `--resume` は数えない。再現性の列(journal の
`--kind repro`)は予算の外で、ここには入れない(D-181)。上限は 8 本。最後の行が上の列である。

| run_id | --fresh-run | --resume |
|---|---|---|
| `f72c0ea9d29d4ab49d7991bb3077e412` | 1 | 2 |
| `9bb4b32b862d452b8de7fbd55ce0c6cf` | 1 | 0 |
| `47a478dc038549bf8fe8e58b26055edb` | 1 | 0 |
| `876150982ad54c5395c299ac94fde264` | 1 | 7 |
| `1a8dc37dc7e04c079d68e367cfbf2b5b` | 1 | 1 |
| `cee943ce8595406a84a6c557f6482cf9` | 1 | 3 |
| `ed3dabc5d6824ca582588f76428a77da` | 1 | 3 |
| `384e3e4258d544a4a7b17fc05a6d9db5` | 1 | 4 |

## 圧縮の指標

cluster 段の成果物の割当から計算した値。合格ラインは置かない(D-004)。

| 項目 | 値 |
|---|---|
| N | 190 |
| M | 31 |
| M/N | 0.1632 |
| 最大クラスタ比率 | 0.0895 |
| 「その他」比率 | 0.0000 |

cluster 段の標準エラーの実行サマリ:

```text
clustering summary: n=190 m=31 m_over_n=0.1632 other_ratio=0.0000 max_cluster_ratio=0.0895 failure_count=0
```

## クラスタの割当

クラスタ ID → 問題 ID の一覧(要約は教材から作った文なので写さない)。

```json
{"c001": ["p001-q001", "p001-q011", "p001-q027", "p001-q037", "p001-q043", "p001-q050", "p001-q059", "p001-q103", "p001-q111", "p001-q132", "p001-q134", "p001-q142", "p001-q143", "p001-q168", "p001-q172", "p001-q173", "p001-q189"], "c002": ["p001-q002", "p001-q013", "p001-q018", "p001-q031", "p001-q039", "p001-q069", "p001-q076", "p001-q123", "p001-q126", "p001-q164", "p001-q170", "p001-q171", "p001-q180", "p001-q181"], "c003": ["p001-q003", "p001-q010", "p001-q030", "p001-q114", "p001-q131", "p001-q165"], "c004": ["p001-q004", "p001-q019", "p001-q089", "p001-q096", "p001-q115", "p001-q144", "p001-q148", "p001-q159", "p001-q163", "p001-q169"], "c005": ["p001-q005", "p001-q009", "p001-q081", "p001-q150", "p001-q156", "p001-q174"], "c006": ["p001-q006", "p001-q007", "p001-q023", "p001-q035", "p001-q036", "p001-q040", "p001-q078", "p001-q087", "p001-q120", "p001-q124", "p001-q130", "p001-q149", "p001-q162", "p001-q176", "p001-q182"], "c007": ["p001-q008", "p001-q012", "p001-q047", "p001-q109", "p001-q113", "p001-q128", "p001-q145", "p001-q152", "p001-q179", "p001-q186"], "c008": ["p001-q014", "p001-q024", "p001-q118", "p001-q122", "p001-q129", "p001-q158"], "c009": ["p001-q015", "p001-q025", "p001-q051", "p001-q063", "p001-q079", "p001-q101", "p001-q104", "p001-q127", "p001-q166"], "c010": ["p001-q016", "p001-q021", "p001-q022", "p001-q084", "p001-q098"], "c011": ["p001-q017", "p001-q020", "p001-q032", "p001-q045", "p001-q082", "p001-q090", "p001-q116"], "c012": ["p001-q026", "p001-q033", "p001-q135", "p001-q154", "p001-q167"], "c013": ["p001-q028", "p001-q064", "p001-q141"], "c014": ["p001-q029"], "c015": ["p001-q034", "p001-q044", "p001-q062", "p001-q071", "p001-q088", "p001-q095", "p001-q099", "p001-q112", "p001-q136", "p001-q178", "p001-q185"], "c016": ["p001-q038", "p001-q048", "p001-q049", "p001-q053", "p001-q077", "p001-q091", "p001-q108", "p001-q187"], "c017": ["p001-q041", "p001-q146", "p001-q183"], "c018": ["p001-q042", "p001-q061", "p001-q074", "p001-q086", "p001-q147"], "c019": ["p001-q046", "p001-q083", "p001-q106", "p001-q117", "p001-q119", "p001-q133", "p001-q139", "p001-q175", "p001-q177"], "c020": ["p001-q052", "p001-q060", "p001-q153"], "c021": ["p001-q054", "p001-q055"], "c022": ["p001-q056", "p001-q057", "p001-q072", "p001-q190"], "c023": ["p001-q058", "p001-q070"], "c024": ["p001-q065"], "c025": ["p001-q066", "p001-q073", "p001-q080", "p001-q092", "p001-q093", "p001-q110", "p001-q125", "p001-q151", "p001-q155", "p001-q160"], "c026": ["p001-q067", "p001-q068", "p001-q075", "p001-q097", "p001-q105", "p001-q107", "p001-q137", "p001-q140", "p001-q184", "p001-q188"], "c027": ["p001-q085"], "c028": ["p001-q094", "p001-q102", "p001-q138"], "c029": ["p001-q100"], "c030": ["p001-q121"], "c031": ["p001-q157", "p001-q161"]}
```

## 列の集約

最後の終了行の `sequence` から。

| 項目 | 値 |
|---|---|
| status | `completed` |
| overage_check | `passed` |
| overage_invocations | 0 |
| invocations_without_rate_limit_event | 0 |
| quota_missing.at_start | 2 |
| quota_missing.at_end | 2 |

## 実行 1(cluster)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T10:51:46.375185+09:00 |
| コミット | `827e67151f6c26a0224431fdda73c28f6c137998` |
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
{"record": "start", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "4a36d0c75cba4ae98b0810751428d365", "started_at": "2026-10-21T01:51:48.172708+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "4a36d0c75cba4ae98b0810751428d365", "started_at": "2026-10-21T01:51:48.172708+00:00", "finished_at": "2026-10-21T02:01:31.433887+00:00", "elapsed_sec": 583.3169732640963, "llm_calls": 63, "provider_invocations": 70, "model_turns": 140, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 62}, "max_concurrency": 1, "tokens": {"input_tokens": 140, "cache_creation_input_tokens": 1411098, "cache_read_input_tokens": 52787, "output_tokens": 39624}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 62, "cache_creation_input_tokens": 570374, "cache_read_input_tokens": 25170, "output_tokens": 2339}}, "calls_by_model": {"claude-opus-5-5": {"calls": 39, "input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"calls": 31, "input_tokens": 62, "cache_creation_input_tokens": 570374, "cache_read_input_tokens": 25170, "output_tokens": 2339}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.784854400000006, "status": "recorded", "computed": 6.407121400000001, "relative_diff": 0.5271841735354045, "by_model": {"claude-opus-5-5": 4.955155400000001, "claude-sonnet-5-5": 1.451966}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 532.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 78}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 62}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed_warning", "resetsAt": 1792969200, "five_hour_resetsAt": 1792563600, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 63, "provider_invocations": 70, "model_turns": 140, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 62}, "tokens": {"input_tokens": 140, "cache_creation_input_tokens": 1411098, "cache_read_input_tokens": 52787, "output_tokens": 39624}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 62, "cache_creation_input_tokens": 570374, "cache_read_input_tokens": 25170, "output_tokens": 2339}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 583.3169732640963, "wall_clock_sec": 583.261179, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.784854400000006, "status": "recorded", "computed": 6.407121400000001, "relative_diff": 0.5271841735354045}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4a36d0c75cba4ae98b0810751428d365", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 2(recall)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T11:01:53.945519+09:00 |
| コミット | `827e67151f6c26a0224431fdda73c28f6c137998` |
| 終了コード | `1` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
recall
aip-c01
--resume
384e3e4258d544a4a7b17fc05a6d9db5
```

開始行:

```json
{"record": "start", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "dc3487aee4d44e638348a2053718d09f", "started_at": "2026-10-21T02:01:55.235300+00:00", "cache_mode": "resume", "stage": "recall", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "dc3487aee4d44e638348a2053718d09f", "started_at": "2026-10-21T02:01:55.235300+00:00", "finished_at": "2026-10-21T02:21:38.659827+00:00", "elapsed_sec": 1183.524814230972, "llm_calls": 62, "provider_invocations": 62, "model_turns": 125, "turns_by_model": {"claude-haiku-4-5": 72, "claude-sonnet-5-5": 53}, "max_concurrency": 1, "tokens": {"input_tokens": 418, "cache_creation_input_tokens": 305541, "cache_read_input_tokens": 31676, "output_tokens": 146199}, "tokens_by_model": {"claude-haiku-4-5": {"input_tokens": 366, "cache_creation_input_tokens": 191470, "cache_read_input_tokens": 9862, "output_tokens": 142944}, "claude-sonnet-5-5": {"input_tokens": 52, "cache_creation_input_tokens": 114071, "cache_read_input_tokens": 21814, "output_tokens": 3255}}, "calls_by_model": {"claude-haiku-4-5": {"calls": 35, "input_tokens": 366, "cache_creation_input_tokens": 191470, "cache_read_input_tokens": 9862, "output_tokens": 142944}, "claude-sonnet-5-5": {"calls": 27, "input_tokens": 52, "cache_creation_input_tokens": 114071, "cache_read_input_tokens": 21814, "output_tokens": 3255}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 1.5901316000000003, "status": "recorded", "computed": 1.2754226, "relative_diff": 0.24674880310259545, "by_model": {"claude-haiku-4-5": 0.9554096999999999, "claude-sonnet-5-5": 0.32001290000000004}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-haiku-4-5": {"status": "recorded", "per_turn_tokens": 612.1666666666667, "bucket": "input_tokens", "measured_at": "2026-10-15", "cli_version": "2.1.300", "argv_digest": "66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 72}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 53}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 1}, "failed_invocations": 1, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 1, "ended_by_rate_limit": {"value": true, "status": "rejected", "resetsAt": 1792563600, "five_hour_resetsAt": 1792563600, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "quota_at_end": {"status": "rejected", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 1.0, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 1, "exit_reason": "RecallCallsFailed", "kind": "rate_limit"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 125, "provider_invocations": 132, "model_turns": 265, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 115, "claude-haiku-4-5": 72}, "tokens": {"input_tokens": 558, "cache_creation_input_tokens": 1716639, "cache_read_input_tokens": 84463, "output_tokens": 185823}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 114, "cache_creation_input_tokens": 684445, "cache_read_input_tokens": 46984, "output_tokens": 5594}, "claude-haiku-4-5": {"input_tokens": 366, "cache_creation_input_tokens": 191470, "cache_read_input_tokens": 9862, "output_tokens": 142944}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 1}, "max_concurrency": 1, "elapsed_sec_sum": 1766.8417874950683, "wall_clock_sec": 1790.487119, "estimated_cost": {"notional": true, "currency": "USD", "declared": 11.374986000000007, "status": "recorded", "computed": 7.682544000000001, "relative_diff": 0.4806275108870194}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4a36d0c75cba4ae98b0810751428d365", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}}, {"execution_id": "dc3487aee4d44e638348a2053718d09f", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "at_end": {"status": "rejected", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 1.0, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 3(recall)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T18:29:21.117105+09:00 |
| コミット | `827e67151f6c26a0224431fdda73c28f6c137998` |
| 終了コード | `0` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
recall
aip-c01
--resume
384e3e4258d544a4a7b17fc05a6d9db5
```

開始行:

```json
{"record": "start", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "88d5a72ea71645108c66fe5a6cd4252b", "started_at": "2026-10-21T09:29:22.520951+00:00", "cache_mode": "resume", "stage": "recall", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "88d5a72ea71645108c66fe5a6cd4252b", "started_at": "2026-10-21T09:29:22.520951+00:00", "finished_at": "2026-10-21T11:22:56.735573+00:00", "elapsed_sec": 6814.668817723985, "llm_calls": 363, "provider_invocations": 363, "model_turns": 743, "turns_by_model": {"claude-sonnet-5-5": 332, "claude-haiku-4-5": 411}, "max_concurrency": 1, "tokens": {"input_tokens": 14236, "cache_creation_input_tokens": 1800340, "cache_read_input_tokens": 235077, "output_tokens": 795274}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 332, "cache_creation_input_tokens": 718446, "cache_read_input_tokens": 138435, "output_tokens": 19088}, "claude-haiku-4-5": {"input_tokens": 13904, "cache_creation_input_tokens": 1081894, "cache_read_input_tokens": 96642, "output_tokens": 776186}}, "calls_by_model": {"claude-sonnet-5-5": {"calls": 166, "input_tokens": 332, "cache_creation_input_tokens": 718446, "cache_read_input_tokens": 138435, "output_tokens": 19088}, "claude-haiku-4-5": {"calls": 197, "input_tokens": 13904, "cache_creation_input_tokens": 1081894, "cache_read_input_tokens": 96642, "output_tokens": 776186}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 9.147457700000004, "status": "recorded", "computed": 7.2583682000000005, "relative_diff": 0.26026366367030035, "by_model": {"claude-sonnet-5-5": 2.0015025, "claude-haiku-4-5": 5.2568657000000005}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 332}, "claude-haiku-4-5": {"status": "recorded", "per_turn_tokens": 612.1666666666667, "bucket": "input_tokens", "measured_at": "2026-10-15", "cli_version": "2.1.300", "argv_digest": "66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 411}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed_warning", "resetsAt": 1792592400, "five_hour_resetsAt": 1792592400, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.03, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed_warning", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792592400, "five_hour": {"utilization": 0.92, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.62, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 488, "provider_invocations": 495, "model_turns": 1008, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 447, "claude-haiku-4-5": 483}, "tokens": {"input_tokens": 14794, "cache_creation_input_tokens": 3516979, "cache_read_input_tokens": 319540, "output_tokens": 981097}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 446, "cache_creation_input_tokens": 1402891, "cache_read_input_tokens": 185419, "output_tokens": 24682}, "claude-haiku-4-5": {"input_tokens": 14270, "cache_creation_input_tokens": 1273364, "cache_read_input_tokens": 106504, "output_tokens": 919130}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 1}, "max_concurrency": 1, "elapsed_sec_sum": 8581.510605219053, "wall_clock_sec": 34268.562865, "estimated_cost": {"notional": true, "currency": "USD", "declared": 20.52244370000001, "status": "recorded", "computed": 14.940912200000001, "relative_diff": 0.37357367644527145}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4a36d0c75cba4ae98b0810751428d365", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}}, {"execution_id": "dc3487aee4d44e638348a2053718d09f", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "at_end": {"status": "rejected", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 1.0, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}}, {"execution_id": "88d5a72ea71645108c66fe5a6cd4252b", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.03, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792592400, "five_hour": {"utilization": 0.92, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.62, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 4(script)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T20:23:51.553835+09:00 |
| コミット | `827e67151f6c26a0224431fdda73c28f6c137998` |
| 終了コード | `0` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
script
aip-c01
--resume
384e3e4258d544a4a7b17fc05a6d9db5
```

開始行:

```json
{"record": "start", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "2888b4d4acb44444809474e34a2b27cf", "started_at": "2026-10-21T11:23:52.786471+00:00", "cache_mode": "resume", "stage": "script", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "2888b4d4acb44444809474e34a2b27cf", "started_at": "2026-10-21T11:23:52.786471+00:00", "finished_at": "2026-10-21T11:23:53.326209+00:00", "elapsed_sec": 0.539737366954796, "llm_calls": 0, "provider_invocations": 0, "model_turns": 0, "turns_by_model": {}, "max_concurrency": 1, "tokens": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}, "tokens_by_model": {}, "calls_by_model": {}, "estimated_cost": {"notional": true, "currency": "USD", "declared": null, "status": "recorded", "computed": 0, "relative_diff": null, "by_model": {}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": null, "resetsAt": null, "five_hour_resetsAt": null, "seven_day_resetsAt": null}, "quota_at_start": null, "quota_at_end": null, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 488, "provider_invocations": 495, "model_turns": 1008, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 447, "claude-haiku-4-5": 483}, "tokens": {"input_tokens": 14794, "cache_creation_input_tokens": 3516979, "cache_read_input_tokens": 319540, "output_tokens": 981097}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 446, "cache_creation_input_tokens": 1402891, "cache_read_input_tokens": 185419, "output_tokens": 24682}, "claude-haiku-4-5": {"input_tokens": 14270, "cache_creation_input_tokens": 1273364, "cache_read_input_tokens": 106504, "output_tokens": 919130}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 1}, "max_concurrency": 1, "elapsed_sec_sum": 8582.050342586008, "wall_clock_sec": 34325.153501, "estimated_cost": {"notional": true, "currency": "USD", "declared": 20.52244370000001, "status": "recorded", "computed": 14.940912200000001, "relative_diff": 0.37357367644527145}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4a36d0c75cba4ae98b0810751428d365", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}}, {"execution_id": "dc3487aee4d44e638348a2053718d09f", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "at_end": {"status": "rejected", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 1.0, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}}, {"execution_id": "88d5a72ea71645108c66fe5a6cd4252b", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.03, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792592400, "five_hour": {"utilization": 0.92, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.62, "resetsAt": 1792969200}}}, {"execution_id": "2888b4d4acb44444809474e34a2b27cf", "at_start": null, "at_end": null}], "quota_missing": {"at_start": 1, "at_end": 1}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 5(audio)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T20:24:55.813389+09:00 |
| コミット | `827e67151f6c26a0224431fdda73c28f6c137998` |
| 終了コード | `0` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
-m
quiz_sift.cli
--root
.
audio
aip-c01
--resume
384e3e4258d544a4a7b17fc05a6d9db5
--tts-model
/home/koich/CosyVoice/pretrained_models/CosyVoice-300M-SFT
```

開始行:

```json
{"record": "start", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "1d7bd020f91342cab7cd5fac342c4229", "started_at": "2026-10-21T11:24:57.009588+00:00", "cache_mode": "resume", "stage": "audio", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "384e3e4258d544a4a7b17fc05a6d9db5", "execution_id": "1d7bd020f91342cab7cd5fac342c4229", "started_at": "2026-10-21T11:24:57.009588+00:00", "finished_at": "2026-10-21T12:51:54.574457+00:00", "elapsed_sec": 5217.862004462979, "llm_calls": 0, "provider_invocations": 0, "model_turns": 0, "turns_by_model": {}, "max_concurrency": 1, "tokens": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}, "tokens_by_model": {}, "calls_by_model": {}, "estimated_cost": {"notional": true, "currency": "USD", "declared": null, "status": "recorded", "computed": 0, "relative_diff": null, "by_model": {}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": null, "resetsAt": null, "five_hour_resetsAt": null, "seven_day_resetsAt": null}, "quota_at_start": null, "quota_at_end": null, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "completed", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 488, "provider_invocations": 495, "model_turns": 1008, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 78, "claude-sonnet-5-5": 447, "claude-haiku-4-5": 483}, "tokens": {"input_tokens": 14794, "cache_creation_input_tokens": 3516979, "cache_read_input_tokens": 319540, "output_tokens": 981097}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 78, "cache_creation_input_tokens": 840724, "cache_read_input_tokens": 27617, "output_tokens": 37285}, "claude-sonnet-5-5": {"input_tokens": 446, "cache_creation_input_tokens": 1402891, "cache_read_input_tokens": 185419, "output_tokens": 24682}, "claude-haiku-4-5": {"input_tokens": 14270, "cache_creation_input_tokens": 1273364, "cache_read_input_tokens": 106504, "output_tokens": 919130}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 1}, "max_concurrency": 1, "elapsed_sec_sum": 13799.912347048987, "wall_clock_sec": 39606.401749, "estimated_cost": {"notional": true, "currency": "USD", "declared": 20.52244370000001, "status": "recorded", "computed": 14.940912200000001, "relative_diff": 0.37357367644527145}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4a36d0c75cba4ae98b0810751428d365", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 0.33, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.48, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}}, {"execution_id": "dc3487aee4d44e638348a2053718d09f", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.84, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.53, "resetsAt": 1792969200}}, "at_end": {"status": "rejected", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792563600, "five_hour": {"utilization": 1.0, "resetsAt": 1792563600}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}}, {"execution_id": "88d5a72ea71645108c66fe5a6cd4252b", "at_start": {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792969200, "five_hour": {"utilization": 0.03, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.54, "resetsAt": 1792969200}}, "at_end": {"status": "allowed_warning", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792592400, "five_hour": {"utilization": 0.92, "resetsAt": 1792592400}, "seven_day": {"utilization": 0.62, "resetsAt": 1792969200}}}, {"execution_id": "2888b4d4acb44444809474e34a2b27cf", "at_start": null, "at_end": null}, {"execution_id": "1d7bd020f91342cab7cd5fac342c4229", "at_start": null, "at_end": null}], "quota_missing": {"at_start": 2, "at_end": 2}, "overage_check": "passed", "metered_env_names": []}}
```
