# Retrospective

> `/verify` generates/updates this at acceptance approval / reaching `done`. It recovers the process metacognition and
> leaves learning for the next cycle (next product / next iteration).
> It **closes the open items** of each log in the phase deliverables (escalation, speculative work).

## 1. Where rework originated
Classify needs-revision / blocked by origin. Upstream defects are material for improving the next requirements/design.

| Task/gate | Event | Class (upstream defect / implementation convenience / external factor) | Root cause | Countermeasure for next time |
|---------------|------|------------------------------------------|----------|------------|
| T-060・T-064・T-065・T-070・T-076・T-088 | scope の外のファイルに触る必要が出て needs-revision・scope_violation。/revise 4回(D-238 系、seq 238・458・478・757) | 上流(tasks) | 設計の型変更や関数の削除が必ず触るテスト・呼び出し元を、タスクの scope を切るときに grep で洗っていなかった | 型・シグネチャを変えるタスクは、変える名前の参照元を全部 scope に入れる。`guard.scope_additions` の `tests/` は最初から置く |
| T-071(試行1〜7) | フル処理が1回ごとに別の理由で止まり、/revise 9回、試行の上限は 3→8。止まった理由: 切り詰めを作り直さない(T-072)、contradicted の要約で段ごと停止(T-073)、段単位のキャッシュで再開が最初から(T-075・T-076)、meter が回復した切り詰めを失敗に数える(T-081)、作り直しに理由を渡さない(T-086)、思考の停止で作り直し率が上がる(T-083)、選択肢キーの漏れとラベルの誤検出(T-087)、台本段の判定し直しのゆれ(T-088) | 上流(design) | 1呼び出しあたり数%で起きる確率的な失敗が、数百呼び出しの列全体を止める設計だった。合成リハーサル(数問)ではその率が見えなかった | 呼び出しが N 本ある段は「1本が落ちたら列はどうなるか」を設計で書く。リハーサルは率が読める本数で取る |
| T-071 試行の数え(D-249) | 試行の表が `--fresh-run` の列を 8 本と数え、上限 7 を超えた。2026-10-12 の列 9bb4b32b を D-241 から数え落としていた | 実装の都合 | 予算を人とエージェントが手で数え、記録の側の機械的な数えと突き合わせていなかった | 予算は最初から記録を読む検査(`check_attempt_budget`)で数える |
| T-069 | ロックした環境で CosyVoice が import できない(setuptools>=83 で pkg_resources が無い) | 上流(design) | 脆弱性対策の制約(D-165)が、ゲートの外の tts extra を壊すことを確かめていなかった | 依存の制約を変えたら、extra を含めてホストで全テストを走らせる |
| T-066 | 成果物をディレクトリで宣言し、PNG 7 枚が coverage から外れた(seq 655) | 上流(tasks) | `artifact` はファイル単位で宣言するという規則を知らずに書いた | 成果物はファイルごとに名指す |
| T-089・T-090 | D-248(台本段は含意判定を呼ばない)の後、docstring 2 か所に古い記述が残り、それぞれ別タスクで直した | 実装の都合 | 振る舞いを外すとき、その振る舞いを述べる文書・docstring を洗わなかった | 振る舞いを外すタスクは、その名前の言及を grep し受入基準に入れる |
| T-082・T-086・T-088・T-089・T-090 | reviewer が findings を worktree 側の `.rein/work/` に書き、loop が読めず blocked。毎回 transcript から読み戻して todo に戻し、build をやり直した(1回あたり約 $18〜19) | 外部要因(rein) | reviewer の書き先が worktree の相対パスに解決される | rein 側で直す(§6) |
| T-056・T-057 | reviewer の簡素化の指摘(死んだ分岐、読まれない Enum 値)が2ラウンドで閉じず blocked | 実装の都合 | 実装者が後備経路と型を広めに書いた | — |
| 全体 | 5時間枠・週次枠の到達で run_aborted 58 回 | 外部要因(サブスクリプション枠) | 実装者・レビュアーの起動が枠を使い切った | — |
| acceptance | 依存の監査で既知の脆弱性 38 件。修正版が1週間のクールダウン内だった | 外部要因 | 監査を受け入れの直前にしか走らせていなかった | mandate のときにも監査を走らせる(このサイクルは 10-06 に走らせて直した) |

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
| scope が型変更・削除の参照元を含まない | L-TASK-SCOPE が見ていて逃した | 狭める: L-TASK-SCOPE に「タスクが型・シグネチャ・公開関数を変える/消すとき、その名前の参照元(tests を含む)が scope にあるか」を足す | 未(人が書く) |
| 確率的な失敗が数百呼び出しの列を止める | L-DES-FAILURE-WALK・L-REQ-FAILURE-MODES が見ていて逃した | 足す: when = 1回の実行で外部の確率的な呼び出しが 100 本以上ある。what = 1本の失敗・ゆれ・枠切れが列全体に何をするか、作り直し・再開の単位はどこか | 未(人が書く) |
| 振る舞いを外したあと文書に古い記述が残る | 無し | 足す: when = タスクが振る舞いを外す。what = その振る舞いを述べる docstring・設計・計画の文を洗ったか | 未(人が書く) |
| 依存の制約が extra を壊す | L-CODE-DEP-SURFACE が見ていたが、ゲートに入らない extra への影響は問わなかった | 狭める: L-CODE-DEP-SURFACE に「ゲートに入らない extra への影響」を足す | 未(人が書く) |

## 3. Recovering the escalation log
Conclude every open escalation in the event log: `rein events --render` lists them. The log
is append-only and has **no `resolve` verb** — a record an operator can close by hand is not
evidence — so the conclusion lives here, one line each.

- T-056・T-057 の blocked(seq 112・233): 指摘を直して done。
- T-060・T-064・T-065・T-070 の needs-revision(seq 224〜225・444〜447・470〜471・493): /revise で scope を足して done(seq 238・458・478)。
- T-069 の needs-revision(seq 609〜611): pyworld を git タグ v0.3.6 に固定する /revise(seq 616)で done。
- T-076・T-088 の scope_violation(seq 754・954): T-076 は /revise で scope を足して done(seq 757)。T-088 は範囲外の変更を戻し、残りを T-089 に回した。
- T-068 の blocked(seq 828): 作り直しに理由を渡す T-086 を足して(seq 831)リハーサルを取り直し done。
- T-082・T-086・T-088・T-089・T-090 の blocked(seq 796・854・936・981・1017): reviewer の findings の置き場所。transcript から読み戻し、todo に戻して done。
- T-071・T-058・T-059 の awaiting_operator(seq 635〜999 の 8 件): T-058 は再抽出の記録(`extraction-rerun.md`)、T-059 は「取り直し不要」の記録(`extraction-refetch.md`)、T-071 は試行7と再現性の列2本で done。
- フル処理の試行が止まった件(/revise seq 640・662・704・776・875・900・922・967): それぞれ T-072・T-073・T-075/T-076・T-081・T-083・T-087・T-088・T-089 で直し、試行7で完走。
- 依存の脆弱性と docstring の消し漏れ(seq 1007): D-250 で T-090 を足し、依存は 2026-10-23 に更新して監査 clean。

## 4. Adoption of speculative work
For each row in `docs/speculative-work.md`, finalize adopt / discard (fill the "Adopt? (human)" column).

- 行は無い。

## 5. Lessons for upstream
When building something similar next time, what should have been firmed up first at the requirements/design stage.
Durable ones are promoted the same way as §6 (`upstream` included) — record where each landed.

| Lesson | Promote? | Promoted to (file) |
|--------|----------|--------------------|
| 外部の LLM を数百回呼ぶ段は、1回の失敗・ゆれ・枠切れで列が止まらない作りを要件・設計の段で決める(項目ごとの作り直し、理由を渡す、項目単位の再開、判定は1回だけ) | 未定(人) | |
| 不可逆な実行の予算は、記録を機械的に数える検査を先に作ってから起動する | 未定(人) | |
| 依存の制約は、ゲートに入らない extra を含めてホストで確かめる | 未定(人) | |

## 6. Process / template improvement proposals
Improvement ideas for how this loop is run, the gates, self-assessment, and deterministic orchestration
(feedback to the template itself is welcome too). **Before `cycle-close` archives this file, promote any keeper
into the always-loaded template (`AGENTS.md` / `.rein/prompts/**` / the per-agent wrappers / the lens library)
and record where** — a durable lesson must not stay only here. A proposal for the upstream template itself gets
`Promote? = upstream`; the human files it on the template repository by hand (issue/PR) and records the URL in
"Promoted to (file)".

| Proposal | Promote? | Promoted to (file) |
|----------|----------|--------------------|
| タスク単位の reviewer が findings を worktree 側に書き、loop が読めない(5 タスクで発生)。書き先を絶対パスで渡すか、loop が worktree 側も読む | upstream | |
| `knowledge_gap` の awaiting_operator が同じ内容で 8 回記録される。待ちが続く間の再記録は 1 回にまとめる | upstream | |
| 受け入れ後に依存を直すと product digest が動き、Decision Card の回答が消える。依存の監査を mandate の readiness にも入れる | upstream | |

## 7. What went well (ways worth keeping)
- フル処理が止まるたびに、止まった理由を記録から特定し、修正を受入基準つきのタスクにして /revise で通した。試行7で実デッキ 190 問を完走し、再現性の列2本も1回の実行で通った。
- 合成デッキのリハーサル(T-068・T-083・T-087・T-088)で、実デッキの予算を使う前に修正を確かめた。
- 実教材の本文を出さない道具(`precheck`、`render`、ID と件数だけのログ)で、実データの失敗を調べられた。
