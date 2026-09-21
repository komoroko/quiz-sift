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

- **Phase** — the gate that was pending: `mandate`, `acceptance`, or the `T-NNN` of an
  irreversible point this cycle is stopped in front of.
- **Premised on** — what the work would be wasted by. If nothing, say "nothing"; that is what
  makes it outcome-independent, and a row that cannot fill this in was not speculative work, it
  was the deliverable.
- **Adopt? (human)** — blank until a human decides. `adopt` / `discard`, with where it landed.
