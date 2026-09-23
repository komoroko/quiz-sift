# Retrospective

> `/verify` generates/updates this at acceptance approval / reaching `done`. It recovers the process metacognition and
> leaves learning for the next cycle (next product / next iteration).
> It **closes the open items** of each log in the phase deliverables (escalation, speculative work).

## 1. Where rework originated
Classify needs-revision / blocked by origin. Upstream defects are material for improving the next requirements/design.

| Task/gate | Event | Class (upstream defect / implementation convenience / external factor) | Root cause | Countermeasure for next time |
|---------------|------|------------------------------------------|----------|------------|
| | | | | |

## 2. Lenses this cycle should have had
The other direction of §1. `rein lens --stats` only ever argues for *removing* a lens — it counts the
ones you have and can never see the one nobody wrote — so a library read through it alone only shrinks.
This section is where a lens gets in, and there is no verb for it: **a human writes it, by hand.**

Put the two sides next to each other. `rein lens` prints what was being watched for this cycle; §1 above
holds what actually went wrong. **For each root cause in §1, ask whether any applied lens was watching for
it.** A cause nothing was watching for is a lens to write; a cause a lens *was* watching for and missed is
a lens to narrow, not a new one. Neither is automatic: no identifier ties a cause in this cycle to a cause
in the last one, so recurrence is your judgement, made here, with both lists in front of you.

The library is `lenses.yaml` under your user config (`rein lens --stats` prints the path). It is shared by
every repository you use, so a lens written here applies to all of them — that is the point of a library,
and the reason to write the `when:` condition rather than leaving it unconditional.

| Root cause (from §1) | Was a lens watching for it? | Lens to add or narrow (id / when / what to look for) | Written to library? |
|---|---|---|---|
| | | | |

## 3. Recovering the escalation log
Conclude every open escalation in the event log: `rein events --render` lists them. The log
is append-only and has **no `resolve` verb** — a record an operator can close by hand is not
evidence — so the conclusion lives here, one line each.

-

## 4. Adoption of speculative work
For each row in `docs/speculative-work.md`, finalize adopt / discard (fill the "Adopt? (human)" column).

-

## 5. Lessons for upstream
When building something similar next time, what should have been firmed up first at the requirements/design stage.
Durable ones are promoted the same way as §6 (`upstream` included) — record where each landed.

| Lesson | Promote? | Promoted to (file) |
|--------|----------|--------------------|
| **実データの 7.9% は N-of-M の複数選択問題で、R-1 の「4択・正解1件」が実物と合っていない。** T-019 の実取得(演習テスト4、190問)の内訳は 4択・正解1つが175問、5択・正解2つが13問、6択・正解3つが2問。15問は要件どおり `OPTION_COUNT_NOT_4` として件数付きで除外されており、これはバグではなく仕様の限界。次サイクルの `/req` で「N-of-M をどう採点し、どう音声化するか」を正面から扱う。影響範囲は実測済み: `Question.correct_option_id: OptionId` は `__post_init__` が強制するドメイン不変条件で、参照は src 10本 / tests 12本 / web 2本、`OPTION_COUNT` の参照が src 4本、`RejectionReason.OPTION_COUNT_NOT_4` は ingest レポートの公開 enum 値。要件側では R-8 の部分点の定義、R-6 の「ラベル列挙禁止」判定(4件前提)、R-4 の「誤答選択肢」の定義がいずれも開く。このサイクルで `/revise --to mandate` して押し込むと done 済み20タスクのほぼ全部が impacted closure に入るため、デルタサイクルとして次に回す(2026-09-24、人の判断) | next cycle | |
| **実ページの DOM はタスク着手前には誰も見ておらず、T-009 の抽出規則は合成フィクスチャに対してだけ緑だった。** 7つのセレクタ定数のうち5つが実物と不一致で、実取得のタスク(T-019)まで誰も気づけなかった。Udemy の結果ページは CSS Modules のハッシュ付きクラス(`result-pane--question-result-pane--sIcOh` 等)で、`data-purpose` を持つのは `answer` と `answer-body` の2つだけ。次に外部ページのスクレイピングを含む要件を切るときは、**抽出規則を決める前に実ページの構造を1回読む**タスクを DAG の先頭に置く(読み取り専用なので不可逆点にはならない)。接頭辞照合にしたのは再デプロイでハッシュが変わるため | next cycle | |

## 6. Process / template improvement proposals
Improvement ideas for how this loop is run, the gates, self-assessment, and deterministic orchestration
(feedback to the template itself is welcome too). **Before `cycle-close` archives this file, promote any keeper
into the always-loaded template (`AGENTS.md` / `.rein/prompts/**` / the per-agent wrappers / the lens library)
and record where** — a durable lesson must not stay only here. A proposal for the upstream template itself gets
`Promote? = upstream`; the human files it on the template repository by hand (issue/PR) and records the URL in
"Promoted to (file)".

| Proposal | Promote? | Promoted to (file) |
|----------|----------|--------------------|
| | | |

## 7. What went well (ways worth keeping)
-
