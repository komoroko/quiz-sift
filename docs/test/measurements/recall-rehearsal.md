# 実測ログ: 合成デッキの script 段までのリハーサル

NFR-7 の合成リハーサル(C-026)。合成デッキ `tests/fixtures/decks/synthetic-nofm/` を使い捨ての作業ルートに写し、
ホストの実 CLI で `cluster --fresh-run` を走らせ、同じ列に `recall --resume` と `script --resume` を足した記録である。
続けて、合成デッキから作ったミューテーションを、1問1回のまとめた判定で実 CLI に聞いた。

この md は `tests/measurements/test_recall_rehearsal_logs.py render` が journal・`meter.jsonl`・recall と script の
成果物・`mutations.json` から作った。LLM は呼んでいない。開始行と終了行は meter.jsonl の行を加工せずに写している。
合成デッキの文は教材ではないが、想起素材・台本・主張の本文は写さず、件数と ID だけを書く。

## 実行条件

ダイジェストと環境の鍵は、rehearse のときのコードから取った値である(`code_conditions`)。

| 項目 | 値 |
|---|---|
| デッキ | `synthetic-nofm` |
| デッキのダイジェスト | `sha256:74c4a3d60cacd05b456c45dd0f2368fdafb76e4e6ebe6dd2128c630ce0e00a38` |
| 問題数 | 16 |
| 複数正解の問題数 | 8 |
| CLI | `2.1.303 (Claude Code)` |
| 実行数 | 3 |
| 分類のプロンプトとスキーマのダイジェスト | `sha256:32a577dd7a5e1bdd838150276e22fa63d4cf97fe8d5a15c1eeade54f2685dcf3` |
| 要約のプロンプトとスキーマのダイジェスト | `sha256:683327c90bc244ff2529a2f7dd7def0729a589d2d0f3d803a880f125d21f63dc` |
| 想起素材のプロンプトとスキーマのダイジェスト | `sha256:7de67ac51f72577aa3c72f410323f31adc81badd02e3bbdf8402751528f36292` |
| 判定のプロンプトとスキーマのダイジェスト | `sha256:4b6a6bb75eb391fcdde59d8c955ad22af1f1cb34fd48b95615d2e78bd5418cb0` |
| generate_recall の子の環境の鍵 | `HOME,LANG,PATH,TZ` |

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
| `option_keys` | 0 |
| `empty` | 0 |
| `answer_mismatch` | 0 |

## 含意判定

| 項目 | 値 |
|---|---|
| 素材の件数 | 16 |
| 判定に渡る主張の件数(素材から数えた値) | 41 |
| 台本のトラック数 | 6 |
| 台本の問題数 | 16 |

判定の結果は製品がログに出さないので、ここには実測した値が無い。recall と script の成果物は、含意判定が
`consistent` のときだけ書かれる(それ以外は `EntailmentRejected` か `RegenerationExhausted` で止まる)。上の
素材と台本は exit 0 の recall と script が書いた成果物から数えたもので、判定の通過はそこから推論している。主張の
件数も判定器に渡った件数の実測ではなく、素材ごとの答えの要素と識別ポイント1件を数えた値である。

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
| `ml-02` | 1 |
| `ml-03` | 0 |
| `ml-04` | 0 |
| `net-01` | 0 |
| `net-02` | 0 |
| `net-03` | 0 |
| `net-04` | 0 |

| 項目 | 値 |
|---|---|
| 再生成のあった問題の数 | 1 |
| 作り直しを上限(2 回)まで使った問題の数 | 0 |
| option_key で作り直した問題の数 | 1 |

option_key で作り直した問題の数は、recall の標準エラーの作り直しの行(`recall: 問題 … を作り直す(…)`)のうち、
落ちた検査に `option_key` を含む問題を数えた値である(D-247)。合格ラインは置かない(16 問では作り直し率の差を
判定できない。D-246)。比較の値は、同じ合成デッキで思考を止めた 2026-10-15 の記録が 6/16、思考ありの
2026-10-15 の記録(run f8165be8)が 0/16 である。

## 呼び出し数とトークン数

トークン数は終了行の `tokens` の4区分(input・cache_creation_input・cache_read_input・output)の和。190 問への
外挿は、問題数の比 190/16 で伸ばした値である。cluster 段の分類は全問を1度に見るので
問題数に比例しないが、ここでは比例として扱う。

段ごと:

| 段 | 呼び出し数 | プロバイダ呼び出し数 | トークン数 | 呼び出し数(外挿) | トークン数(外挿) |
|---|---|---|---|---|---|
| cluster | 9 | 9 | 26793 | 106.875 | 318166.875 |
| recall | 33 | 33 | 123833 | 391.875 | 1470516.875 |
| script | 16 | 16 | 29918 | 190.000 | 355276.250 |

cluster から script までの合計:

| 項目 | リハーサル | 190 問への外挿 |
|---|---|---|
| 呼び出し数 | 58 | 688.750 |
| プロバイダ呼び出し数 | 58 | 688.750 |
| トークン数 | 180544 | 2143960.000 |

## レート枠

utilization の生の値(終了行の `quota_at_start`・`quota_at_end`)。小数2桁に量子化されているので、差分を外挿には
使わない(D-218)。

| 実行 | execution_id | 枠 | 開始時 | 終了時 | resetsAt |
|---|---|---|---|---|---|
| 1 | `4d5d9896004e4c30bb34f59744c5151e` | five_hour | 0.17 | 0.19 | 1792439400 |
| 1 | `4d5d9896004e4c30bb34f59744c5151e` | seven_day | 0.19 | 0.19 | 1792969200 |
| 2 | `bb2dbc0a1c284008bb2104dbb6d8c1ce` | five_hour | 0.2 | 0.26 | 1792439400 |
| 2 | `bb2dbc0a1c284008bb2104dbb6d8c1ce` | seven_day | 0.19 | 0.2 | 1792969200 |
| 3 | `0057c40e2d624838b1e7d0a7518614cb` | five_hour | 0.26 | 0.27 | 1792439400 |
| 3 | `0057c40e2d624838b1e7d0a7518614cb` | seven_day | 0.2 | 0.2 | 1792969200 |

前サイクルの列 `591e7bb9ae6145d284a0ec993371c59c`(full-run.md の終了行 4 行、トークン数 3945015)で、
実行ごとの utilization の増分の和をトークン数の和で割った比:

| 枠 | utilization / トークン |
|---|---|
| five_hour | 3.59948e-07 |
| seven_day | 2.78833e-08 |

190 問へ外挿したトークン数にこの比を掛けた、cluster から script までの枠の使用率の推定:

| 枠 | 推定使用率 |
|---|---|
| five_hour | 0.771714 |
| seven_day | 0.0597807 |

## ミューテーション

各問題の誤答1件につき「この問題の正解は「誤答の本文」である。」という主張を作り、1問の主張をまとめて1回の
判定(`ClaudeCliLlm.check_entailments`、reference は `reference_for`)で実 CLI に聞いた件数。合格ラインは置かない
(D-044)。

| 項目 | 値 |
|---|---|
| ミューテーションの件数 | 47 |
| contradicted | 47 |
| uncertain | 0 |
| consistent | 0 |

| 問題 | 件数 | contradicted | uncertain | consistent |
|---|---|---|---|---|
| `db-01` | 3 | 3 | 0 | 0 |
| `db-02` | 3 | 3 | 0 | 0 |
| `db-03` | 2 | 2 | 0 | 0 |
| `db-04` | 3 | 3 | 0 | 0 |
| `net-01` | 3 | 3 | 0 | 0 |
| `net-02` | 3 | 3 | 0 | 0 |
| `net-03` | 3 | 3 | 0 | 0 |
| `net-04` | 3 | 3 | 0 | 0 |
| `ml-01` | 3 | 3 | 0 | 0 |
| `ml-02` | 3 | 3 | 0 | 0 |
| `ml-03` | 3 | 3 | 0 | 0 |
| `ml-04` | 3 | 3 | 0 | 0 |
| `k8s-01` | 3 | 3 | 0 | 0 |
| `k8s-02` | 3 | 3 | 0 | 0 |
| `k8s-03` | 3 | 3 | 0 | 0 |
| `k8s-04` | 3 | 3 | 0 | 0 |

判定の呼び出しの終了行(`mutation-meter.jsonl`):

```json
{"record": "end", "run_id": "f339b89e12e54e2689fd2bd26d0810c0", "execution_id": "85353a8625d54c499d64225b53705173", "started_at": "2026-10-19T15:13:07.300026+00:00", "finished_at": "2026-10-19T15:14:04.093136+00:00", "elapsed_sec": 56.79553491901606, "llm_calls": 16, "provider_invocations": 16, "model_turns": 32, "turns_by_model": {"claude-sonnet-5-5": 32}, "max_concurrency": 1, "tokens": {"input_tokens": 32, "cache_creation_input_tokens": 12919, "cache_read_input_tokens": 13424, "output_tokens": 1773}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 12919, "cache_read_input_tokens": 13424, "output_tokens": 1773}}, "calls_by_model": {"claude-sonnet-5-5": {"calls": 16, "input_tokens": 32, "cache_creation_input_tokens": 12919, "cache_read_input_tokens": 13424, "output_tokens": 1773}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.07081240000000001, "status": "recorded", "computed": 0.051433900000000005, "relative_diff": 0.37676512961295966, "by_model": {"claude-sonnet-5-5": 0.051433900000000005}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792439400, "five_hour_resetsAt": 1792439400, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.27, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.28, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 16, "provider_invocations": 16, "model_turns": 32, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-sonnet-5-5": 32}, "tokens": {"input_tokens": 32, "cache_creation_input_tokens": 12919, "cache_read_input_tokens": 13424, "output_tokens": 1773}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 12919, "cache_read_input_tokens": 13424, "output_tokens": 1773}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 56.79553491901606, "wall_clock_sec": 56.79311, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.07081240000000001, "status": "recorded", "computed": 0.051433900000000005, "relative_diff": 0.37676512961295966}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "85353a8625d54c499d64225b53705173", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.27, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.28, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 1(cluster)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-20T00:02:20.109183+09:00 |
| コミット | `dd92f35deafb68bfc37b92e103a57d7a0be7805d` |
| 終了コード | `0` |
| run_id | `f9aafcf4d2d84277b1892f3e1623707f` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-087/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t087-rehearsal-20261020000218
cluster
synthetic-nofm
--fresh-run
```

開始行:

```json
{"record": "start", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "4d5d9896004e4c30bb34f59744c5151e", "started_at": "2026-10-19T15:02:21.305973+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "4d5d9896004e4c30bb34f59744c5151e", "started_at": "2026-10-19T15:02:21.305973+00:00", "finished_at": "2026-10-19T15:03:43.576136+00:00", "elapsed_sec": 82.27469332399778, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 8}, "max_concurrency": 1, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 15276, "cache_read_input_tokens": 4746, "output_tokens": 6753}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6461}, "claude-sonnet-5-5": {"input_tokens": 8, "cache_creation_input_tokens": 5300, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "calls_by_model": {"claude-opus-5-5": {"calls": 5, "input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6461}, "claude-sonnet-5-5": {"calls": 4, "input_tokens": 8, "cache_creation_input_tokens": 5300, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.2339015, "status": "recorded", "computed": 0.1960235, "relative_diff": 0.19323193392628957, "by_model": {"claude-opus-5-5": 0.1795858, "claude-sonnet-5-5": 0.0164377}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 532.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 10}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 8}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792439400, "five_hour_resetsAt": 1792439400, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.17, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.19, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 8}, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 15276, "cache_read_input_tokens": 4746, "output_tokens": 6753}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6461}, "claude-sonnet-5-5": {"input_tokens": 8, "cache_creation_input_tokens": 5300, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 82.27469332399778, "wall_clock_sec": 82.270163, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.2339015, "status": "recorded", "computed": 0.1960235, "relative_diff": 0.19323193392628957}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4d5d9896004e4c30bb34f59744c5151e", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.17, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.19, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 2(recall)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-20T00:03:43.645614+09:00 |
| コミット | `dd92f35deafb68bfc37b92e103a57d7a0be7805d` |
| 終了コード | `0` |
| run_id | `f9aafcf4d2d84277b1892f3e1623707f` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-087/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t087-rehearsal-20261020000218
recall
synthetic-nofm
--resume
f9aafcf4d2d84277b1892f3e1623707f
```

開始行:

```json
{"record": "start", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "bb2dbc0a1c284008bb2104dbb6d8c1ce", "started_at": "2026-10-19T15:03:44.889153+00:00", "cache_mode": "resume", "stage": "recall", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "bb2dbc0a1c284008bb2104dbb6d8c1ce", "started_at": "2026-10-19T15:03:44.889153+00:00", "finished_at": "2026-10-19T15:11:59.500683+00:00", "elapsed_sec": 494.6377823780058, "llm_calls": 33, "provider_invocations": 33, "model_turns": 66, "turns_by_model": {"claude-haiku-4-5": 34, "claude-sonnet-5-5": 32}, "max_concurrency": 1, "tokens": {"input_tokens": 36616, "cache_creation_input_tokens": 15466, "cache_read_input_tokens": 13424, "output_tokens": 58327}, "tokens_by_model": {"claude-haiku-4-5": {"input_tokens": 36584, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 56379}, "claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 15466, "cache_read_input_tokens": 13424, "output_tokens": 1948}}, "calls_by_model": {"claude-haiku-4-5": {"calls": 17, "input_tokens": 36584, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 56379}, "claude-sonnet-5-5": {"calls": 16, "input_tokens": 32, "cache_creation_input_tokens": 15466, "cache_read_input_tokens": 13424, "output_tokens": 1948}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.40122940000000007, "status": "recorded", "computed": 0.3780304, "relative_diff": 0.06136808045067296, "by_model": {"claude-haiku-4-5": 0.318479, "claude-sonnet-5-5": 0.059551400000000004}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-haiku-4-5": {"status": "recorded", "per_turn_tokens": 612.1666666666667, "bucket": "input_tokens", "measured_at": "2026-10-15", "cli_version": "2.1.300", "argv_digest": "66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 34}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792439400, "five_hour_resetsAt": 1792439400, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.2, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.26, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 42, "provider_invocations": 42, "model_turns": 84, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 40, "claude-haiku-4-5": 34}, "tokens": {"input_tokens": 36634, "cache_creation_input_tokens": 30742, "cache_read_input_tokens": 18170, "output_tokens": 65080}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6461}, "claude-sonnet-5-5": {"input_tokens": 40, "cache_creation_input_tokens": 20766, "cache_read_input_tokens": 15941, "output_tokens": 2240}, "claude-haiku-4-5": {"input_tokens": 36584, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 56379}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 576.9124757020036, "wall_clock_sec": 578.19471, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.6351309, "status": "recorded", "computed": 0.5740539, "relative_diff": 0.1063959325073831}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4d5d9896004e4c30bb34f59744c5151e", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.17, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.19, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}}, {"execution_id": "bb2dbc0a1c284008bb2104dbb6d8c1ce", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.2, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.26, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 3(script)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-20T00:11:59.566164+09:00 |
| コミット | `dd92f35deafb68bfc37b92e103a57d7a0be7805d` |
| 終了コード | `0` |
| run_id | `f9aafcf4d2d84277b1892f3e1623707f` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-087/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t087-rehearsal-20261020000218
script
synthetic-nofm
--resume
f9aafcf4d2d84277b1892f3e1623707f
```

開始行:

```json
{"record": "start", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "0057c40e2d624838b1e7d0a7518614cb", "started_at": "2026-10-19T15:12:00.689275+00:00", "cache_mode": "resume", "stage": "script", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "f9aafcf4d2d84277b1892f3e1623707f", "execution_id": "0057c40e2d624838b1e7d0a7518614cb", "started_at": "2026-10-19T15:12:00.689275+00:00", "finished_at": "2026-10-19T15:13:06.839693+00:00", "elapsed_sec": 66.15433476504404, "llm_calls": 16, "provider_invocations": 16, "model_turns": 32, "turns_by_model": {"claude-sonnet-5-5": 32}, "max_concurrency": 1, "tokens": {"input_tokens": 32, "cache_creation_input_tokens": 14676, "cache_read_input_tokens": 13424, "output_tokens": 1786}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 14676, "cache_read_input_tokens": 13424, "output_tokens": 1786}}, "calls_by_model": {"claude-sonnet-5-5": {"calls": 16, "input_tokens": 32, "cache_creation_input_tokens": 14676, "cache_read_input_tokens": 13424, "output_tokens": 1786}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.0779704, "status": "recorded", "computed": 0.0559564, "relative_diff": 0.39341344332373046, "by_model": {"claude-sonnet-5-5": 0.0559564}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792439400, "five_hour_resetsAt": 1792439400, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.26, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.27, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 58, "provider_invocations": 58, "model_turns": 116, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 72, "claude-haiku-4-5": 34}, "tokens": {"input_tokens": 36666, "cache_creation_input_tokens": 45418, "cache_read_input_tokens": 31594, "output_tokens": 66866}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6461}, "claude-sonnet-5-5": {"input_tokens": 72, "cache_creation_input_tokens": 35442, "cache_read_input_tokens": 29365, "output_tokens": 4026}, "claude-haiku-4-5": {"input_tokens": 36584, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 56379}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 643.0668104670476, "wall_clock_sec": 645.53372, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.7131013, "status": "recorded", "computed": 0.6300103, "relative_diff": 0.1318883199211188}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "4d5d9896004e4c30bb34f59744c5151e", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.17, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.19, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}}, {"execution_id": "bb2dbc0a1c284008bb2104dbb6d8c1ce", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.2, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.19, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.26, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}}, {"execution_id": "0057c40e2d624838b1e7d0a7518614cb", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.26, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792439400, "five_hour": {"utilization": 0.27, "resetsAt": 1792439400}, "seven_day": {"utilization": 0.2, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```
