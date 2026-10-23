# 実測ログ: recall 生成の出力トークンと思考(2026-10-14)

試行3の recall 段で、Haiku 4.5 の生成1回あたりの出力が約 3,600 トークンあり、成果物(約 2KB)に比べて多かった。
原因を確かめるため、製品と同じ `build_argv("claude-haiku-4-5", RECALL_SYSTEM, RECALL_SCHEMA)` と最小ホーム(`Workspace.child_env`)で、
合成デッキ `tests/fixtures/decks/synthetic-nofm` の1問(db-01)を3通りに呼んだ。教材の本文は使っていない。可逆。
収集はスクラッチのスクリプトで、リポジトリには置いていない。

## 実行条件
| 項目 | 値 |
|---|---|
| CLI | 2.1.299 (Claude Code) |
| モデル | claude-haiku-4-5 |
| 入力 | db-01 を `recall_payload` と同じ形にしたもの |
| 判定 | stream-json の assistant メッセージの content ブロックの種別と、result の usage |

## 結果
| 条件 | content ブロック | output_tokens | 入力合計 | num_turns |
|---|---|---|---|---|
| そのまま | thinking, tool_use | 1658 | 1810 | 2 |
| `--effort low` を足す | thinking, tool_use | 1745 | 1809 | 2 |
| 環境変数 `MAX_THINKING_TOKENS=0` | text, tool_use | 315 | 1634 | 2 |

thinking ブロックの本文は空で返る(伏せられている)ので、思考の量は output_tokens の差でしか見えない。

## 結論
- Haiku 4.5 は既定で思考し、それが出力の大半を占める(この1問で 1,658 中 約 1,340)。
- `--effort low` では止まらない。`MAX_THINKING_TOKENS=0` で止まる。
- 試行3の呼び出しログでは、Sonnet 5.5 の含意判定の出力は1回 約 70 トークン、Opus 5.5 の要約は 約 860 トークンで、思考で膨らんでいるのは recall の生成だけである。
