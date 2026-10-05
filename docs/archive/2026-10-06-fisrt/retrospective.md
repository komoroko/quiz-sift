# Retrospective

> `/verify` generates/updates this at acceptance approval / reaching `done`. It recovers the process metacognition and
> leaves learning for the next cycle (next product / next iteration).
> It **closes the open items** of each log in the phase deliverables (escalation, speculative work).

## 1. Where rework originated
Classify needs-revision / blocked by origin. Upstream defects are material for improving the next requirements/design.

| Task/gate | Event | Class (upstream defect / implementation convenience / external factor) | Root cause | Countermeasure for next time |
|---------------|------|------------------------------------------|----------|------------|
| T-036 | needs-revision → blocked(送り返し予算切れ、#155〜#260) | 上流の欠陥 | C-015 を赤にする原因(`cli.main` の未捕捉例外のトレースバックが認証情報を stderr に出す)は `cli.py` にあり、どのタスクも scope に持っていなかった。受入テストが正しく赤になっても、直す場所が mandate の外だった | 横断 NFR(秘密・ログ)の受入テストを切るとき、それを赤にしうる出口(例外ハンドラ・ログ出力)を持つファイルを、同じタスクの scope に入れる |
| T-047(cluster 段) | needs-revision(#344)→ `/revise --to mandate`(#348) | 上流の欠陥 | R-3 の要約の実在判定が「主要な語句」でなく「全名詞句」を見ていた。合成データでは通り、実データの最初のクラスタで止まった | 実在判定や含意判定のような LLM 出力の検査は、実データの小さな列(数クラスタ)で通す reversible なリハーサルを不可逆タスクの前に置く |
| T-047(含意判定) | blocked(#638)→ T-051 | 上流の欠陥 | 含意判定が Haiku・2文のプロンプトで、正しい要約を contradicted と誤判定(32件中1件)。1件で列全体が止まる設計(D-042)と組み合わさった | 「1件で全体が止まる」判定器は、誤判定率を実測してからモデルとプロンプトを決める |
| T-051 | needs-revision(#650)→ `/revise`(#654) | 実装の都合(計画の漏れ) | モデル名を assert している既存テスト `tests/ports/test_protocols.py` が scope に無かった | 定数を変えるタスクは、その定数を参照するテストを `grep` してから scope を切る |
| T-047(運用) | blocked ×5(#298・#442・#453・#538・#638) | 外部要因 | 週次・5時間のレート枠、auto mode 分類器の拒否、実装エージェントのセッション終了による子プロセスの停止 | 長い実行は `operate` に寄せ、エージェントのセッションに乗せない。レート枠の残りを前提条件として持つ |
| T-047(recall 段) | blocked(#871)→ 次サイクルへ(#875、DEF-1) | 上流の欠陥 | `RECALL_SYSTEM` が想起キーを本文の表記どおりに抜き出すよう指示しておらず、`recall/verify.py` の実在検査と噛み合わない(ungrounded_keys 175/175) | 生成プロンプトと、その出力を拒否する検査は同じタスクで一緒に設計し、合成データでなく実データの1件で突き合わせる |
| mandate(依存の監査) | `/revise`(#896)→ T-052 | 外部要因 | setuptools 81 に PYSEC-2026-3447。torch 2.11 が setuptools<82 を要求 | 依存の監査は `/verify` でなく mandate の baseline で一度走らせる |
| mandate(loose-rein 0.12) | `/revise`(#912)→ C-017/C-018 赤 →(#919)T-053 | 実装の都合(私の手順違反) | 0.12 への移行で `quality_gate` の `kind` 行を外したが、テストを走らせずにゲートの準備完了と報告した | ハーネスの設定を変えたら、提示の前に品質ゲート全ステップをサンドボックスで走らせる |
| T-039・T-045 | task_failed(test・lint、各1回)→ 再試行で緑 | 実装の都合 | 1回目の実装の取りこぼし | 対策不要(送り返し予算の範囲で解消) |

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
| 受入テストを赤にする出口が、どのタスクの scope にもない(T-036) | いいえ | 追加候補: when = 横断 NFR(秘密・ログ・PII)の claim がある / 見るもの = その claim を破りうる出口(例外ハンドラ・ログ・stderr)のファイルが、受入テストを持つタスクの scope に入っているか | 未(人の判断) |
| LLM 出力の検査が合成データでだけ緑(R-3、recall) | いいえ | 追加候補: when = LLM の出力を決定的な検査で拒否する経路がある / 見るもの = 検査とプロンプトが同じ基準を言っているか、実データの1件で通したか | 未(人の判断) |
| 定数を参照するテストが scope 外(T-051) | いいえ | 一度きり。lens にはしない(2回目で書く) | — |
| | | | |

## 3. Recovering the escalation log
Conclude every open escalation in the event log: `rein events --render` lists them. The log
is append-only and has **no `resolve` verb** — a record an operator can close by hand is not
evidence — so the conclusion lives here, one line each.

- #155・#157(T-036 needs-revision / blocked): `/revise --to mandate`(#262)で `cli.py` を scope に足し、T-036 は done。解消。
- #260(no_runnable、T-036 の予算切れ): 上と同じ `/revise` で解消。acceptance の前に人が処置を記録した。解消。
- #295・#667(T-047 awaiting_operator): 人が前提(CosyVoice、レート枠)を用意して再開した。解消。
- #299・#304・#443・#454・#466・#539・#639(T-047 agent_blocked): レート枠・分類器の拒否・セッション終了・含意判定の誤判定。含意判定は T-051 で直した。残りは運用の問題で、T-047 は記録タスクに作り直した(#875)。解消。
- #344・#345(T-047 cluster 段の欠陥): `/revise`(#348)で要約の実在判定を直した。解消。
- #650・#651(T-051 scope 漏れ): `/revise`(#654)で scope を足した。解消。
- #870・#872(recall 段 RecallRejected): **未解消のまま次サイクルへ**(DEF-1、D-164)。フル処理の完走と NFR-7 の完走での判定も一緒に持ち越す。

## 4. Adoption of speculative work
For each row in `docs/speculative-work.md`, finalize adopt / discard (fill the "Adopt? (human)" column).

- 8行すべてが mandate 承認前の準備(品質ゲートのイメージ、Python スケルトン、`.gitignore`、読み取り専用の実現性調査)で、mandate の承認と acceptance でそのまま製品に入っている。「Adopt? (human)」の欄は空のまま書庫に入ったので、人の記入が残っている。
- 3行目の是正(`src/` に Bash のヒアドキュメントで書き、PreToolUse フックを素通りした件)は §6 の提案に回した。

## 5. Lessons for upstream
When building something similar next time, what should have been firmed up first at the requirements/design stage.
Durable ones are promoted the same way as §6 (`upstream` included) — record where each landed.

| Lesson | Promote? | Promoted to (file) |
|--------|----------|--------------------|
| **実データの 7.9% は N-of-M の複数選択問題で、R-1 の「4択・正解1件」が実物と合っていない。** T-019 の実取得(演習テスト4、190問)の内訳は 4択・正解1つが175問、5択・正解2つが13問、6択・正解3つが2問。15問は要件どおり `OPTION_COUNT_NOT_4` として件数付きで除外されており、これはバグではなく仕様の限界。次サイクルの `/req` で「N-of-M をどう採点し、どう音声化するか」を正面から扱う。影響範囲は実測済み: `Question.correct_option_id: OptionId` は `__post_init__` が強制するドメイン不変条件で、参照は src 10本 / tests 12本 / web 2本、`OPTION_COUNT` の参照が src 4本、`RejectionReason.OPTION_COUNT_NOT_4` は ingest レポートの公開 enum 値。要件側では R-8 の部分点の定義、R-6 の「ラベル列挙禁止」判定(4件前提)、R-4 の「誤答選択肢」の定義がいずれも開く。このサイクルで `/revise --to mandate` して押し込むと done 済み20タスクのほぼ全部が impacted closure に入るため、デルタサイクルとして次に回す(2026-09-24、人の判断) | next cycle | |
| **生成プロンプトと、その出力を拒否する検査が別々に書かれ、実データで初めて噛み合わないと分かった。** R-3 の要約の実在判定と recall の実在検査(DEF-1)の2度。要件で「実在」の定義(表記どおりか、正規化するか、どの語を対象にするか)を1つ決め、プロンプトと検査の両方をそこから書く | next cycle | |
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
| ハーネス(`config.yaml`・loose-rein の版)を変えたら、提示の前に品質ゲート全ステップをサンドボックスで実測する。0.12 移行で私が省いて C-017/C-018 が赤になった | 未(人の判断) | |
| Bash のヒアドキュメントやリダイレクトでの書き込みは PreToolUse の Write/Edit フックを通らない(speculative-work 3行目)。`rein guard` の Bash 側の検査で塞げるか | upstream 候補(人の判断) | |
| loose-rein の破壊的な版上げ(0.12、0.13)はサイクルの途中で入れない。0.13.0 は旧形式のリポジトリと旧イベント名の監査ログを読まず、途中適用を試みて戻した | 未(人の判断) | |
| 依存の監査を mandate の baseline で一度走らせ、`/verify` で初めて赤になるのを避ける | upstream 候補(人の判断) | |

## 7. What went well (ways worth keeping)
- 実データの列が止まるたびに、原因を mandate の外か内かで分け、外なら `/revise` で人に返した。黙って scope を広げた修正はない。
- 品質ゲートをネットワーク遮断のサンドボックスで実測し、エージェントの「緑」を証拠にしなかった。T-053 の不具合もこの実測で見つかった。
- 教材本文(NFR-2)はリポジトリ・コミット・ログのどこにも入れず、診断は人の端末で行った。
