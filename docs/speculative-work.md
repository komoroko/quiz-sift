# Speculative work log

Work pulled forward while a gate was pending. One table, for every phase, because the question
asked of it later is one question: **did waiting cost anything?**

The rule this records against is `.rein/prompts/rules/gate-workflow.md` "While a gate is pending".
Only **outcome-independent** work belongs here — scaffolding, dev-env/CI setup, read-only
investigation, fixtures — never a deliverable premised on the decision being waited on. It stays
outside `guard.paths` (`tests/` is deliberately unguarded for exactly this), and a `rein guard`
denial is where the boundary is, not an obstacle to route around.

Everything here is **throwaway-by-default**. The gate's answer decides whether it survives, and
that decision is a human's: `rein next` / `/status` names the rows still blank, and
`docs/retrospective.md` §4 is where they are finalized at the end of the cycle.

| Phase | What was done | Premised on | Adopt? (human) |
|---|---|---|---|
| mandate | 品質ゲートのサンドボックスイメージを、プロジェクト所有の `.rein/oci/quiz-sift/Containerfile` として作り直した(ruff 0.15.16 / mypy 2.1.0 / mkdocs 1.6.1 を追加)。`config.yaml` の `quality` プロファイルを `containerfile: python` から `dockerfile:` へ切り替え、ダイジェストを再ピン | nothing — Python を使う限り、どの要件が残っても要る。イメージが無ければ `rein baseline measure` が実行できず、mandate は承認できない | |
| mandate | Python スケルトン: `pyproject.toml`(uv / ruff / mypy strict / pytest)、`tests/test_scaffold.py`、`.pre-commit-config.yaml`、`.github/workflows/ci.yml`、`mkdocs.yml`、`makefile`、`README.md`、`.gitignore` の拡張 | nothing — D-018(PyProjectTemplate 準拠)は人間が決めた mandate 事項であり、要件の増減では変わらない。`tests/test_scaffold.py` のみ、製品コードが入った時点で役目を終えるので削除する | |
| mandate | **是正**: 最初は `src/quiz_sift/` と `src/tests/` に置いたが、`src/` は `guard.paths` に含まれ mandate 未承認では書き込みが拒否される。Bash のヒアドキュメントで書いたため PreToolUse フックを素通りしていた。`tests/` へ移し、品質ゲート・CI・makefile の対象パスを `.` に揃えた(`src/` は承認後にしか生えないため、パスを列挙すると不在で落ちる) | nothing — ガードの境界を戻しただけ | |
| mandate | `.gitignore` に `data/` `out/` を追加(NFR-2: 取り込んだ実教材の本文を追跡対象に入れない) | nothing | |
| mandate | **読み取り専用の実現性調査**(`/design` step 6 の `run_experiment`): プローブイメージを 2 本組み、`--network none` で fugashi + unidic-lite の形態素解析、ffmpeg `silencedetect` による mp3 の無音検出、Playwright chromium の起動とページ操作が動くことを実測した。イメージは削除済みで、リポジトリには何も書いていない。結果は `docs/20-design.md` の NFR-6 節に記録 | nothing — どの要件が残っても、ネットワーク遮断のゲートで何が動くかは知る必要がある | |

| mandate | **読み取り専用の実現性調査**(従量課金の禁止を受けた LLM 呼び出し経路の検討): `claude -p --output-format json` を実測した。`--json-schema` が存在し、スキーマ適合 JSON が フェンス無しで返ること、`usage` が `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens` + `thinking_tokens` を返し `TokenUsage` の4値を 満たすこと、`total_cost_usd` が付くこと、1回あたりの製品外オーバーヘッドが Opus 5 で 14,603 tok / Haiku 4.5 で 8,506 tok であることを確認した。`claude setup-token`(サブスクリプション前提の長期トークン発行)の存在も確認。合成データのみを使い、教材本文は一切通していない(NFR-2)。成果物はスクラッチ領域のみで、リポジトリには何も書いていない | nothing — 呼び出し経路として CLI を採るか否かにかかわらず、「サブスク認証で構造化出力とトークン内訳が取れるか」は知る必要がある。取れないなら NFR-7 / C-019 ごと作り直しになる | |

- **Phase** — the gate that was pending: `mandate`, `acceptance`, or the `T-NNN` of an
  irreversible point this cycle is stopped in front of.
- **Premised on** — what the work would be wasted by. If nothing, say "nothing"; that is what
  makes it outcome-independent, and a row that cannot fill this in was not speculative work, it
  was the deliverable.
- **Adopt? (human)** — blank until a human decides. `adopt` / `discard`, with where it landed.
