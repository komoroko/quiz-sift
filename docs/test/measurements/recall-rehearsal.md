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
| CLI | `2.1.304 (Claude Code)` |
| 実行数 | 3 |
| 分類のプロンプトとスキーマのダイジェスト | `sha256:32a577dd7a5e1bdd838150276e22fa63d4cf97fe8d5a15c1eeade54f2685dcf3` |
| 要約のプロンプトとスキーマのダイジェスト | `sha256:683327c90bc244ff2529a2f7dd7def0729a589d2d0f3d803a880f125d21f63dc` |
| 想起素材のプロンプトとスキーマのダイジェスト | `sha256:7de67ac51f72577aa3c72f410323f31adc81badd02e3bbdf8402751528f36292` |
| 判定のプロンプトとスキーマのダイジェスト | `sha256:4b6a6bb75eb391fcdde59d8c955ad22af1f1cb34fd48b95615d2e78bd5418cb0` |
| generate_recall の子の環境の鍵 | `HOME,LANG,PATH,TZ` |
| script 段の鍵のプロンプトとモデルのダイジェスト | `sha256:9dcc7079268e0a820685e3c59ce199656fd9ab0f41ab7e1b98a7109f72c6f21e` |

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
| 台本のトラック数 | 5 |
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
| `db-03` | 1 |
| `db-04` | 0 |
| `k8s-01` | 0 |
| `k8s-02` | 0 |
| `k8s-03` | 0 |
| `k8s-04` | 0 |
| `ml-01` | 0 |
| `ml-02` | 0 |
| `ml-03` | 0 |
| `ml-04` | 1 |
| `net-01` | 0 |
| `net-02` | 0 |
| `net-03` | 0 |
| `net-04` | 0 |

| 項目 | 値 |
|---|---|
| 再生成のあった問題の数 | 2 |
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
| cluster | 9 | 9 | 26816 | 106.875 | 318440.000 |
| recall | 34 | 34 | 136581 | 403.750 | 1621899.375 |
| script | 0 | 0 | 0 | 0.000 | 0.000 |

cluster から script までの合計:

| 項目 | リハーサル | 190 問への外挿 |
|---|---|---|
| 呼び出し数 | 43 | 510.625 |
| プロバイダ呼び出し数 | 43 | 510.625 |
| トークン数 | 163397 | 1940339.375 |

## レート枠

utilization の生の値(終了行の `quota_at_start`・`quota_at_end`)。小数2桁に量子化されているので、差分を外挿には
使わない(D-218)。レート枠を1回も観測しなかった実行(LLM を呼ばない script 段。D-248)は載せない。

| 実行 | execution_id | 枠 | 開始時 | 終了時 | resetsAt |
|---|---|---|---|---|---|
| 1 | `a811463a4f734d379c791510662e55d0` | five_hour | 0.39 | 0.42 | 1792525200 |
| 1 | `a811463a4f734d379c791510662e55d0` | seven_day | 0.42 | 0.42 | 1792969200 |
| 2 | `f664aeb0f77548d386af2c28e6ba277a` | five_hour | 0.42 | 0.49 | 1792525200 |
| 2 | `f664aeb0f77548d386af2c28e6ba277a` | seven_day | 0.42 | 0.42 | 1792969200 |

前サイクルの列 `591e7bb9ae6145d284a0ec993371c59c`(full-run.md の終了行 4 行、トークン数 3945015)で、
実行ごとの utilization の増分の和をトークン数の和で割った比:

| 枠 | utilization / トークン |
|---|---|
| five_hour | 3.59948e-07 |
| seven_day | 2.78833e-08 |

190 問へ外挿したトークン数にこの比を掛けた、cluster から script までの枠の使用率の推定:

| 枠 | 推定使用率 |
|---|---|
| five_hour | 0.698421 |
| seven_day | 0.054103 |

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
{"record": "end", "run_id": "9404c9ed1c274b4eaf5d13e850e73d2c", "execution_id": "a5e7d269135e4b32a13ca56132375bcf", "started_at": "2026-10-20T15:15:03.671164+00:00", "finished_at": "2026-10-20T15:16:10.978881+00:00", "elapsed_sec": 67.31206950696651, "llm_calls": 16, "provider_invocations": 16, "model_turns": 32, "turns_by_model": {"claude-sonnet-5-5": 32}, "max_concurrency": 1, "tokens": {"input_tokens": 32, "cache_creation_input_tokens": 12923, "cache_read_input_tokens": 13424, "output_tokens": 1905}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 12923, "cache_read_input_tokens": 13424, "output_tokens": 1905}}, "calls_by_model": {"claude-sonnet-5-5": {"calls": 16, "input_tokens": 32, "cache_creation_input_tokens": 12923, "cache_read_input_tokens": 13424, "output_tokens": 1905}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.0721484, "status": "recorded", "computed": 0.0527639, "relative_diff": 0.3673818652525685, "by_model": {"claude-sonnet-5-5": 0.0527639}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792525200, "five_hour_resetsAt": 1792525200, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.5, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.5, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.43, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 16, "provider_invocations": 16, "model_turns": 32, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-sonnet-5-5": 32}, "tokens": {"input_tokens": 32, "cache_creation_input_tokens": 12923, "cache_read_input_tokens": 13424, "output_tokens": 1905}, "tokens_by_model": {"claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 12923, "cache_read_input_tokens": 13424, "output_tokens": 1905}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 67.31206950696651, "wall_clock_sec": 67.307717, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.0721484, "status": "recorded", "computed": 0.0527639, "relative_diff": 0.3673818652525685}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "a5e7d269135e4b32a13ca56132375bcf", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.5, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.5, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.43, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 1(cluster)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T00:03:51.163429+09:00 |
| コミット | `2ada8c2b58159ab3e305e76a93fb782f4d92fd84` |
| 終了コード | `0` |
| run_id | `472043f4517244d5a2c64b675fce62ee` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-088/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t088-rehearsal-20261021000348
cluster
synthetic-nofm
--fresh-run
```

開始行:

```json
{"record": "start", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "a811463a4f734d379c791510662e55d0", "started_at": "2026-10-20T15:03:52.159093+00:00", "cache_mode": "fresh", "stage": "cluster", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "a811463a4f734d379c791510662e55d0", "started_at": "2026-10-20T15:03:52.159093+00:00", "finished_at": "2026-10-20T15:05:26.477794+00:00", "elapsed_sec": 94.32374774396885, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 8}, "max_concurrency": 1, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 15279, "cache_read_input_tokens": 4746, "output_tokens": 6773}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6481}, "claude-sonnet-5-5": {"input_tokens": 8, "cache_creation_input_tokens": 5303, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "calls_by_model": {"claude-opus-5-5": {"calls": 5, "input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6481}, "claude-sonnet-5-5": {"calls": 4, "input_tokens": 8, "cache_creation_input_tokens": 5303, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.2343135, "status": "recorded", "computed": 0.196431, "relative_diff": 0.19285397925989287, "by_model": {"claude-opus-5-5": 0.1799858, "claude-sonnet-5-5": 0.0164452}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-opus-5-5": {"status": "recorded", "per_turn_tokens": 532.1666666666667, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "7be15268ad5b8c2990489105260475a37ed97b7305bfc1a6706663709d9fde86", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 10}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 8}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792525200, "five_hour_resetsAt": 1792525200, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.39, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 9, "provider_invocations": 9, "model_turns": 18, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 8}, "tokens": {"input_tokens": 18, "cache_creation_input_tokens": 15279, "cache_read_input_tokens": 4746, "output_tokens": 6773}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6481}, "claude-sonnet-5-5": {"input_tokens": 8, "cache_creation_input_tokens": 5303, "cache_read_input_tokens": 2517, "output_tokens": 292}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 94.32374774396885, "wall_clock_sec": 94.318701, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.2343135, "status": "recorded", "computed": 0.196431, "relative_diff": 0.19285397925989287}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "a811463a4f734d379c791510662e55d0", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.39, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 2(recall)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T00:05:26.538955+09:00 |
| コミット | `2ada8c2b58159ab3e305e76a93fb782f4d92fd84` |
| 終了コード | `0` |
| run_id | `472043f4517244d5a2c64b675fce62ee` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-088/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t088-rehearsal-20261021000348
recall
synthetic-nofm
--resume
472043f4517244d5a2c64b675fce62ee
```

開始行:

```json
{"record": "start", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "f664aeb0f77548d386af2c28e6ba277a", "started_at": "2026-10-20T15:05:27.455327+00:00", "cache_mode": "resume", "stage": "recall", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "f664aeb0f77548d386af2c28e6ba277a", "started_at": "2026-10-20T15:05:27.455327+00:00", "finished_at": "2026-10-20T15:15:02.233801+00:00", "elapsed_sec": 574.8129837460001, "llm_calls": 34, "provider_invocations": 34, "model_turns": 68, "turns_by_model": {"claude-haiku-4-5": 36, "claude-sonnet-5-5": 32}, "max_concurrency": 1, "tokens": {"input_tokens": 39028, "cache_creation_input_tokens": 15481, "cache_read_input_tokens": 13424, "output_tokens": 68648}, "tokens_by_model": {"claude-haiku-4-5": {"input_tokens": 38996, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 66692}, "claude-sonnet-5-5": {"input_tokens": 32, "cache_creation_input_tokens": 15481, "cache_read_input_tokens": 13424, "output_tokens": 1956}}, "calls_by_model": {"claude-haiku-4-5": {"calls": 18, "input_tokens": 38996, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 66692}, "claude-sonnet-5-5": {"calls": 16, "input_tokens": 32, "cache_creation_input_tokens": 15481, "cache_read_input_tokens": 13424, "output_tokens": 1956}}, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.4553464, "status": "recorded", "computed": 0.43212490000000003, "relative_diff": 0.05373793549041018, "by_model": {"claude-haiku-4-5": 0.372456, "claude-sonnet-5-5": 0.059668900000000004}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {"claude-haiku-4-5": {"status": "recorded", "per_turn_tokens": 612.1666666666667, "bucket": "input_tokens", "measured_at": "2026-10-15", "cli_version": "2.1.300", "argv_digest": "66c1b01769d3385b32422acb877cdeef860ff266d144d816e701b7a7bab0dcb2", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 36}, "claude-sonnet-5-5": {"status": "recorded", "per_turn_tokens": 532.5, "bucket": "cache_creation_input_tokens", "measured_at": "2026-10-13", "cli_version": "2.1.296", "argv_digest": "ecad8eb0cb14906e78a8fe8f23cc29b8b1dc210e2f5034458ce1dee2046a4e06", "skills_digest": "348e49f34f1c7aecaeb9ed1ea5c254e6a5e2f28c94a03e98e1bd09cb47e04f14", "turns": 32}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": "allowed", "resetsAt": 1792525200, "five_hour_resetsAt": 1792525200, "seven_day_resetsAt": 1792969200}, "quota_at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "quota_at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.49, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 43, "provider_invocations": 43, "model_turns": 86, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 40, "claude-haiku-4-5": 36}, "tokens": {"input_tokens": 39046, "cache_creation_input_tokens": 30760, "cache_read_input_tokens": 18170, "output_tokens": 75421}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6481}, "claude-sonnet-5-5": {"input_tokens": 40, "cache_creation_input_tokens": 20784, "cache_read_input_tokens": 15941, "output_tokens": 2248}, "claude-haiku-4-5": {"input_tokens": 38996, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 66692}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 669.136731489969, "wall_clock_sec": 670.074708, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.6896599, "status": "recorded", "computed": 0.6285559000000001, "relative_diff": 0.097213310701562}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "a811463a4f734d379c791510662e55d0", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.39, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}}, {"execution_id": "f664aeb0f77548d386af2c28e6ba277a", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.49, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}}], "quota_missing": {"at_start": 0, "at_end": 0}, "overage_check": "passed", "metered_env_names": []}}
```

## 実行 3(script)

| 項目 | 値 |
|---|---|
| 実行時刻 | 2026-10-21T00:15:02.308004+09:00 |
| コミット | `2ada8c2b58159ab3e305e76a93fb782f4d92fd84` |
| 終了コード | `0` |
| run_id | `472043f4517244d5a2c64b675fce62ee` |

argv:

```text
/mnt/d/prog_works/quiz-sift/.worktrees/T-088/.venv/bin/python3
-m
quiz_sift.cli
--root
/tmp/t088-rehearsal-20261021000348
script
synthetic-nofm
--resume
472043f4517244d5a2c64b675fce62ee
```

開始行:

```json
{"record": "start", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "a558c5ae838a4c94a2fd555147f72a25", "started_at": "2026-10-20T15:15:03.610485+00:00", "cache_mode": "resume", "stage": "script", "env_names": ["AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENABLE_AUTO_MODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID", "COLORTERM", "COREPACK_ENABLE_AUTO_PIN", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "GIT_EDITOR", "HERDR_BIN_PATH", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_STARTUP_CWD", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HOME", "HOSTTYPE", "LANG", "LESSCLOSE", "LESSOPEN", "LOGNAME", "LS_COLORS", "MOTD_SHOWN", "NAME", "NoDefaultCurrentDirectoryInExePath", "OLDPWD", "PATH", "PNPM_HOME", "PULSE_SERVER", "PWD", "PYTHONPATH", "REIN_CAPABILITY_TOKEN", "REIN_CONTROL_SOCKET", "REIN_ROLE", "REIN_RUN_ID", "REIN_SANDBOX", "REIN_TASK_ID", "SHELL", "SHLVL", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "USER", "UV", "UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "WAYLAND_DISPLAY", "WSL2_GUI_APPS_ENABLED", "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP", "XDG_DATA_DIRS", "XDG_RUNTIME_DIR", "_"], "metered_env_names": []}
```

終了行:

```json
{"record": "end", "run_id": "472043f4517244d5a2c64b675fce62ee", "execution_id": "a558c5ae838a4c94a2fd555147f72a25", "started_at": "2026-10-20T15:15:03.610485+00:00", "finished_at": "2026-10-20T15:15:03.614403+00:00", "elapsed_sec": 0.003915209963452071, "llm_calls": 0, "provider_invocations": 0, "model_turns": 0, "turns_by_model": {}, "max_concurrency": 1, "tokens": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}, "tokens_by_model": {}, "calls_by_model": {}, "estimated_cost": {"notional": true, "currency": "USD", "declared": null, "status": "recorded", "computed": 0, "relative_diff": null, "by_model": {}}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "prices": {"claude-opus-5-5": {"input_per_mtok": 4.0, "cache_write_per_mtok": 5.0, "cache_read_per_mtok": 0.2, "output_per_mtok": 20.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-sonnet-5-5": {"input_per_mtok": 2.0, "cache_write_per_mtok": 2.5, "cache_read_per_mtok": 0.1, "output_per_mtok": 10.0, "priced_at": "2026-10-13", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}, "claude-haiku-4-5": {"input_per_mtok": 1.0, "cache_write_per_mtok": 1.25, "cache_read_per_mtok": 0.1, "output_per_mtok": 5.0, "priced_at": "2026-09-25", "source": "https://platform.claude.com/docs/en/about-claude/pricing"}}, "overhead": {}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "failed_invocations": 0, "invocations_without_usage": 0, "invocations_without_turns": 0, "rate_limit_stopped_invocations": 0, "ended_by_rate_limit": {"value": false, "status": null, "resetsAt": null, "five_hour_resetsAt": null, "seven_day_resetsAt": null}, "quota_at_start": null, "quota_at_end": null, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "overage_check": "passed", "stop": {"exit_code": 0, "exit_reason": null, "kind": "completed"}, "sequence": {"status": "resumable", "not_completable_reasons": [], "unreadable_lines_before": 0, "llm_calls": 43, "provider_invocations": 43, "model_turns": 86, "invocations_without_usage": 0, "invocations_without_turns": 0, "overage_invocations": 0, "invocations_without_rate_limit_event": 0, "turns_by_model": {"claude-opus-5-5": 10, "claude-sonnet-5-5": 40, "claude-haiku-4-5": 36}, "tokens": {"input_tokens": 39046, "cache_creation_input_tokens": 30760, "cache_read_input_tokens": 18170, "output_tokens": 75421}, "tokens_by_model": {"claude-opus-5-5": {"input_tokens": 10, "cache_creation_input_tokens": 9976, "cache_read_input_tokens": 2229, "output_tokens": 6481}, "claude-sonnet-5-5": {"input_tokens": 40, "cache_creation_input_tokens": 20784, "cache_read_input_tokens": 15941, "output_tokens": 2248}, "claude-haiku-4-5": {"input_tokens": 38996, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 66692}}, "failures_by_kind": {"spawn": 0, "timeout": 0, "parse": 0, "schema": 0, "truncated": 0, "incomplete_ids": 0, "provider_error": 0}, "max_concurrency": 1, "elapsed_sec_sum": 669.1406466999324, "wall_clock_sec": 671.45531, "estimated_cost": {"notional": true, "currency": "USD", "declared": 0.6896599, "status": "recorded", "computed": 0.6285559000000001, "relative_diff": 0.097213310701562}, "disclaimer": "estimated_cost は公開単価表から自算した名目値(notional)であり、支払った金額を表さない。", "quota": [{"execution_id": "a811463a4f734d379c791510662e55d0", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.39, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}}, {"execution_id": "f664aeb0f77548d386af2c28e6ba277a", "at_start": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.42, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}, "at_end": {"status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": false, "overageStatus": "rejected", "resetsAt": 1792525200, "five_hour": {"utilization": 0.49, "resetsAt": 1792525200}, "seven_day": {"utilization": 0.42, "resetsAt": 1792969200}}}, {"execution_id": "a558c5ae838a4c94a2fd555147f72a25", "at_start": null, "at_end": null}], "quota_missing": {"at_start": 1, "at_end": 1}, "overage_check": "passed", "metered_env_names": []}}
```
