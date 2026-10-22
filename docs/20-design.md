# Design

> `/design` generates this, starting from `docs/10-requirements.md` (approved).
> Important technical choices are **decided by the human via a `structured-question`** and recorded in `docs/decisions/ADR-*.md`.
> Finalized once a human approves at **the mandate**.

## このサイクルの読み方

- 設計の全文は前サイクルの [20-design.md](archive/2026-10-06-fisrt/20-design.md) と [ADR-001〜019](archive/2026-10-06-fisrt/decisions/) にあり、そのまま基準線として有効。
- ここには、このサイクルの差分要件(R-1、R-2、R-3、R-4、R-6、R-7、R-8、R-12、NFR-1、NFR-2、NFR-5、NFR-7、NFR-8)の設計の差分だけを書く。見出しの無い要件の設計は変えない。
- 根拠は src/・tests/ の実コード。`out/`・`data/` は読んでいない。

## Architecture overview

構成(取り込み → クラスタリング → 想起素材 → 台本 → 音声、と学習記録・Web UI)は前サイクルのまま。変わるのは次の4点。

1. **ドメインの正解が集合になる**: `Question.correct_option_id` を `correct_option_ids` に変え、取り込み・抽出・生成物・採点・ストア・UI まで通す(ADR-020、ADR-021)。
2. **想起素材のスキーマと検査**: 選択肢 ID で構造化し、G-1 の照合先を定数にしてプロンプトと検査で共有し、問題ごとに再生成する(ADR-022)。
3. **子 CLI の堅牢化**: argv(ユーザ設定の遮断・ツールの許可リスト)、起動(プロセスグループ)、ログ(本文を出さない)(ADR-023、ADR-024)。
4. **ホストでの実測とフル処理**: CLI のフラグ、固定オーバーヘッド、合成リハーサル、TTS プローブ、フル処理(ADR-025)。

新しいモジュールは `grounding/targets.py`(照合先の定数)だけ。他は既存モジュールの変更。

## Tech stack (finalized)

前サイクルのまま(Python 3.13 / uv、Next.js、SQLite、Claude Code CLI、CosyVoice)。変更は次のとおり。

| Area | Choice | Rationale (ADR) |
|------|------|-----------|
| 製品のバージョン | 0.1.1 | D-169 |
| 子 CLI の argv | ユーザ設定を読まない指定、有効ツールの許可リスト。拒否リストは外す | ADR-023 |
| 学習記録ストア | スキーマ v2(`user_version = 2`) | ADR-021 |
| メソッドごとのモデル | 分類・クラスタ要約・用語説明は `claude-opus-5-5`、想起素材の生成は `claude-haiku-4-5`、含意判定は `claude-sonnet-5-5` | ADR-027 |

## Implementation approach per requirement

### R-1 → design
- **Required features/modules**: `domain/question.py`、`domain/report.py`、`ingest/reader.py`、`ingest/validate.py`、`cli.py`(deck の JSON)、`extract/writer.py`、`adapters/llm_prompts.render_question`、各テストの `Question` の構築。
- **Implementation method**:
  - `OPTION_COUNT = 4` を `OPTION_COUNTS = frozenset({4, 5, 6})` に、`correct_option_id` を `correct_option_ids: tuple[OptionId, ...]`(選択肢順・重複なし・1 ≤ N < M を `__post_init__` で強制)に変え、`answer_count` を足す。
  - 入力キーは `answers: list[str]` に改名する。旧キー `answer` は未知のキーとして `IngestFormatError` で取り込み全体を止める。
  - 除外理由は `OPTION_COUNT_UNSUPPORTED`(D-188)と、正解の配列が空・重複・未知 ID を表す `ANSWER_UNRESOLVED`。
  - フィクスチャは範囲内の 12 組と範囲外(M=3、M=7、N=0、N=M)。
- **Existing assets to reuse**: `IngestReport` の保存則、`RejectionReason`、`validate` の1件ごとの結果型。
- **Corresponding technical choice**: ADR-020
- **What this requires of a person**: 手書きの入力ファイルがあれば新形式に直すこと。抽出由来の入力は R-2 の再抽出で作り直す。
- **Reversibility**: 可逆。コミットを戻せば元に戻る。

### R-2 → design
- **Required features/modules**: `extract/udemy.py`、`extract/writer.py`、`tests/measurements/test_extraction_logs.py`。
- **Implementation method**:
  - 選択肢の件数が `OPTION_COUNTS` に入るブロックを受け、ID は `ABCDEF` からページ順に振る。3件以下・7件以上は `ExtractionFailure` として列挙する。
  - `_identify_answer`: 正解マークが1件以上あれば全件を `CORRECT_MARK` とする。マークが無いときの `USER_SELECTED` は、選択アイコンの印で単一選択と判別できた問題でだけ使う(D-189)。印の候補は `#icon-checkbox-*` と radio 系だが、保存済みの生 HTML で確かめてから定数にする。判別できなければ `ExtractionFailure(ANSWER)`。
  - `ExtractedQuestion.answer_labels: tuple[str, ...]`、writer は `answers` を書く。
  - ゴールデンセットの道具は集合ラベル(`--label p001-q010=A,C`)を受け、集合の一致で判定し、N-of-M が3件以上あることを判定に加える。
  - オフライン再抽出のサブコマンドを足し、全問数・(N, M) の組ごとの件数・抽出の失敗の件数を出す。件数だけを `docs/test/measurements/` に記録する。
- **Existing assets to reuse**: `extract/html_tree`、`AnswerEvidence`/`SelectorKind`、`ExtractReport` の保存則、`test_extraction_logs.py` の `fetch`/`golden-candidates`/`golden`、`adapters/browser_cdp`。
- **Corresponding technical choice**: ADR-020、ADR-025
- **What this requires of a person**: 保存済みの生 HTML がホストに残っていること。N-of-M 3件以上の正解を人手で確かめてラベルを渡すこと。期待件数に届かない場合だけ、ログイン済みの Chrome をデバッグポート付きで起動し、取り直しに立ち会うこと。
- **Reversibility**: 抽出ロジックと再抽出は可逆。Udemy からの取り直しは不可逆(アカウントのアクセス履歴)で、別タスク(`reversible: false`、rehearsal は再抽出、attempts は max 1)にする。

### R-3 → design
- **Required features/modules**: `adapters/claude_process.py::SubprocessRunner.run`、`tests/adapters/test_claude_process.py`、`tests/support/`。
- **Implementation method**:
  - `Popen(..., start_new_session=True)`。タイムアウトしたら、最初の `TimeoutExpired` の部分出力を保持し、`os.killpg(pid, SIGKILL)`、`communicate(timeout=5)`(D-186)。まだ戻らなければ自分で pipe を閉じて `wait()` し、部分出力で `Outcome.TIMEOUT` を返す。alarm の経路(`except BaseException`)でも killpg する。
  - SIGTERM を挟まず即 SIGKILL にする(local。10 秒の予算を分け合わないため)。
  - 親が SIGHUP/SIGTERM を受けたら、子のプロセスグループに killpg してから終わる(D-221)。`start_new_session` で子が端末のグループから外れるため、親だけが死ぬと子が残り、すぐの `--resume` で2本の `claude` が同じ最小ホームを使うのを防ぐ。
  - テストの fake は、pipe を継承した孫(寿命は 60 秒で打ち切る)を残して自分は眠る子と、孫が `setsid` で抜ける変種。テスト自身が時間の上限を持ち、`run` を別スレッドで走らせて `join(15)` し、11 秒未満に `TIMEOUT` が返ることを確かめる。現行コードでは `communicate()` が戻らず、join の期限切れで赤になる(無限に待たない)。
  - 部分出力が `TimeoutExpired.stdout` に載る CPython 3.13 の挙動は、実装時にテストで確かめる。
- **Existing assets to reuse**: `CommandResult`/`Outcome`、`claude_failure.classify` の TIMEOUT 枝。
- **Corresponding technical choice**: ADR-023
- **What this requires of a person**: なし。
- **Reversibility**: 可逆。新しいセッションに抜けた孫は孤児として残りうる(要件はその終了を求めない)。

### R-4 → design
- **Required features/modules**: 新規 `grounding/targets.py`、`recall/verify.py`、`recall/generate.py`、`ports/llm.py`、`domain/recall.py`、`adapters/llm_prompts.py`、`adapters/llm_contract.py`、`adapters/llm_anthropic.py`、`adapters/llm_claude_cli.py::generate_recall`、`cluster/summary.py`、`safety/entailment.py`、`cli.py`(recall)。
- **Implementation method**:
  - **照合先の定数**: `QuestionField`(stem / options / explanation)と、`RECALL_KEY_FIELDS = ("stem",)`、`OTHER_CLUSTER_FIELDS = SUMMARY_FIELDS = ("stem", "options", "explanation")`(D-198)、`texts_for(question, fields)`。検査はこの定数経由でしか照合先を作らない。重複している `question_texts`/`_texts` は消す。
  - **プロンプト**: `render_recall_system(fields)` という関数で組み立て、`RECALL_SYSTEM = render_recall_system(RECALL_KEY_FIELDS)` とする。「terms の各要素は question.stem から連続した部分をそのまま写す」と指示する。片方だけを変えると赤になるテストは、(i) `RECALL_SYSTEM == render_recall_system(RECALL_KEY_FIELDS)`、(ii) 問題文にだけある語は grounded・選択肢にだけある語は ungrounded のフィクスチャ、(iii) 別の fields を渡すと検査とプロンプトの両方が変わること(関数に渡して確かめ、import 時の定数を monkeypatch しない)。「4択」の文言は `CLASSIFY_SYSTEM`・`SUMMARIZE_SYSTEM` も「多肢選択」に直す。
  - **スキーマ**: `RecallDraft(key, answers: (AnswerElement(option_id, text), ...), discrimination: DiscriminationPoint(text, distractor_ids))`。payload の選択肢は不透明なキーで渡し、`parse_recall` がキーを `OptionId` に写し戻す(D-206)。未知のキーは `SCHEMA` で、例外文には件数だけを載せる(NFR-2)。`adapters/llm_anthropic.py` は削除する(D-216、NFR-8)。
  - **検査(1問単位、純粋関数)**: 空要素、`ungrounded_key`、`answer_mismatch`(要素の ID の集合が正解の集合と一致しない)、`missing_options`(名前は残し、中身は「`distractor_ids` が1件以上で、すべて誤答」。括弧の検査 `_QUOTED`/`referenced_options` は消す)、`generic`、`verbatim`、`option_key`(D-247。term・答えの要素・識別ポイントの本文を NFKC で正規化して小文字にし、ASCII の英数字に挟まれない `o1`…`o6` を含み、そのキーが問題の問題文・選択肢・解説(同じ正規化)に無ければ落とす。作り直しの理由は名前だけ。`RECALL_SYSTEM` に検査の意味を書き、report に `option_keys` の件数を足す。本文でのキーの言及を禁じる文は既にあり、それでも漏れたので検査で落とす)。問題をまたぐのは `duplicate_keys` だけで、クラスタをまたぐ場合だけ数え(D-200)、クラスタ順で後の問題を落ちとする。
  - **再生成ループ**: `cli.Pipeline._recall_drafts` と `_Drafted` を廃止し、`recall/generate.py` に一本化する。1問ごとに `1 + MAX_REGENERATIONS`(= 3)回まで生成 → 検査。`LlmCallError` は再生成しない(D-101。ただし子プロセスの切り詰めはアダプタが出し直す。D-231)。`meter.stop_seen()` での早期停止を保つ(D-144)。上限を超えた問題があっても全問を回し切り、その後に超えた問題をすべて列挙して `RegenerationExhausted(SystemExit)` で止まる(D-217。メッセージは問題 ID・検査名・回数だけ)。
  - **含意判定**: 答えの要素は `reference_for(q, focus=要素の option_id)`、識別ポイントは `reference_for(q, distractors=distractor_ids)`(D-192)。機械検査を通った素材だけを判定し、不合格は検査名 `entailment` として同じ上限の中で作り直す(D-232、ADR-026。当初の「再生成せず `EntailmentRejected`」を改めた)。
  - **reference の形**: 誤答の本文を「正しい情報」として渡すと、「X は当てはまらない」という正しい識別ポイントが contradicted になる。そこで reference を「正解: …」と「誤答選択肢(この問題では正しくない記述): …」の節に分け、`ENTAILMENT_SYSTEM` に「誤答選択肢の節は、この問題の答えとして正しくない記述の一覧である。statement が誤答選択肢を正しくないと述べることは矛盾ではない」と足す。プロンプトの変更は ADR-019 の運用の変更で、実判定での効果は合成リハーサル(C-026)で確かめる。
  - **成果物**: recall の JSON に `answers[{option, text}]` と `discrimination{text, distractors[]}`、report に `regenerations`(問題ごとの回数)を足す(検査ごとの延べ件数は作らない、D-219)。ログは件数だけ。
- **Existing assets to reuse**: `is_present`、`original_ratio`、`RecallKey.term_set`、`judge`、`ContractError`、`parse_proposals` の keyed 方式、`Meter`。
- **Corresponding technical choice**: ADR-022
- **What this requires of a person**: なし(合成リハーサルは NFR-7 に書く)。
- **Reversibility**: 可逆。コミットを戻せば元に戻る。

### R-6 → design
- **Required features/modules**: `script/label_guard.py`、`script/generate.py`、`domain/script.py`、`cli.py`(script)。
- **Implementation method**:
  - `_SERIES` を letter `ABCDEF`・number `123456`・kana `アイウエオカ` に、正規表現の文字クラスを A–F、1–6、①–⑥、ア–カに広げ、`_FULLWIDTH` の正規化に Ｅ・Ｆ・５・６・⑤・⑥ を足す。判定は「系列の先頭から M 件がすべて揃う」(日本語は present、英語は chain/markers。D-201)。判定はトラック単位なので、M はそのトラックに含まれる問題の M の最小値(D-211)。`find_enumerations` に M を渡す。
  - 日本語の記号の系列(D-247): 記号 A〜F の直前がカタカナか漢字で、直前のカタカナ・漢字の連なりと記号を合わせた字面がそのトラックの問題の問題文に現れるときだけ、名前としてラベルに数えない。`find_enumerations` にトラックの問題文を渡す(`script/generate.py` から)。品詞解析は使わない。問題文に無い形と単独の記号は数え、番号とかなの系列は変えない。
  - 台本の識別ポイントのセグメント `Discrimination` に、想起素材から `distractor_ids` を引き継ぐ。台本段は含意判定を呼ばない(D-248、ADR-030。識別ポイントは想起素材の段で同じ本文と同じ誤答の本文で判定済み)。`distractor_ids` は台本の成果物に残す。
  - `Answer(texts: tuple[str, ...])` にし(local)、`question_script` が N 要素の答えから組み立てる。要素数が `answer_count` と違えば `MissingAnswerElement(SystemExit)`。連結の語に「1.」「(1)」「1番」を使わない。
- **Existing assets to reuse**: `track_text`、`plan_tracks`、セグメント型。
- **Corresponding technical choice**: ADR-022(答えの形)
- **What this requires of a person**: なし。
- **Reversibility**: 可逆。

### R-7 → design
- **Required features/modules**: コードの変更は無い見込み。`tests/measurements/` に TTS プローブのサブコマンドを足す。
- **Implementation method**:
  - TTS プローブ: 合成した短い台本1本(文と間を含む)を、製品の audio 段と同じ経路(`audio/synthesize` の合成と間の挿入 → mp3 への符号化 → `decode_mp3` → `has_pause_structure`)で通し、無音判定が真になることを確かめる(D-215)。LLM も教材も使わない。記録は `docs/test/measurements/tts-probe.md`。
  - フル処理の証拠は `test_full_run_logs.py silence` で取り、`docs/test/measurements/audio-silence.md` に記録する。
- **Existing assets to reuse**: `audio/silence`、`audio/synthesize`、`adapters/tts_cosyvoice`、`test_full_run_logs.py`。
- **Corresponding technical choice**: ADR-025
- **What this requires of a person**: CosyVoice のチェックアウトと重みを置き、場所を `--tts-model`/`PYTHONPATH` で示すこと。生成された音声を端末へ転送して聞くこと。
- **Reversibility**: プローブは可逆。フル処理側は不可逆(NFR-7)。

### R-8 → design
- **Required features/modules**: `domain/attempt.py`、`scoring/grade.py`、`scoring/session.py`、`store/location.py`、`store/deck_scope.py`、`server/api.py`。
- **Implementation method**:
  - `chosen: frozenset[OptionId]`。`grade(question, chosen)` は frozenset 以外を TypeError、空・他問題の ID・`len != N` を ValueError にし、`chosen == frozenset(correct_option_ids)` のときだけ正答。
  - ストアはスキーマ v2(ADR-021)。所在は変えない。テーブルの作成と `user_version = 2` は1つのトランザクションで行う(D-222)。旧形式は `LegacyStoreError`。
  - API の `present` は `select_count` を返し、`submit` は `chosen: list[str]` だけを受けて、型違い・重複・N 件以外・未知 ID を 400 にする。応答は `correct_option_ids`。
  - 全問誤答のフィクスチャは、正解1件を誤答1件に入れ替えた集合で作る(D-202)。
- **Existing assets to reuse**: `DeckScopedStore`、`connect`、`shuffle_seed`、`BadRequestError` への写像、`build_report`。
- **Corresponding technical choice**: ADR-021
- **What this requires of a person**: 旧ストアがあれば、エラーを見て別名へ退避すること。
- **Reversibility**: 退避は可逆。製品は旧ストアを消さない。

### R-12 → design
- **Required features/modules**: `web/lib/api.ts`、`web/app/decks/[deck]/sessions/[session]/page.tsx`、`tests/measurements/test_host_check_logs.py`(`run_ui_e2e`)。
- **Implementation method**:
  - `select_count > 1` ならチェックボックス・「N 個選択」の表示・送信ボタン。ボタンは選択数がちょうど N のときだけ有効。N = 1 は現行の1クリックのまま(D-199)。
  - E2E は `E2E_QUESTIONS` を集合に変え、5択2正解の問題を1件足し、N−1 件・N+1 件で送れないこと、N 件で送れることを手順とスクリーンショットで確かめる。
- **Existing assets to reuse**: CDP による E2E、使い捨ての作業ルート、`docs/test/measurements/ui-e2e/`。
- **Corresponding technical choice**: ADR-021
- **What this requires of a person**: ホストで `npm ci` と `next build` ができること。
- **Reversibility**: 可逆。

## How non-functional requirements are met

### NFR-1 → design
- **Implementation method**: 3か所に重複している `_reference`(`recall/generate.py`、`script/generate.py`、`cluster/summary.py`)を `safety/entailment.reference_for(question, focus=None)` に1本化する。`focus=None` は正解 N 件すべて、`focus` を渡すと「この文が説明する正解」を明示し正解 N 件も併記する(local)。識別ポイントは参照先の誤答の本文を加える(D-192)。グロッサリの reference は変えない(D-195)。
- **判定のまとめ**(D-242、ADR-029): ポートを `check_entailments(reference, statements)` の1本にする。主張は `id`・本文・任意の `focus`(その要素が説明する正解の本文)・任意の `distractors`(識別ポイントが参照する誤答の本文)を持ち、応答は `{"verdicts": [{"id", "verdict"}]}`。ID の欠けや余りは `INCOMPLETE_IDS`。recall は1問の答えの要素と識別ポイントを1回で聞く。script 段は判定しない(D-248)。reference は `reference_for(question)`(正解 N 件と解説)を1回だけ作り、`focus` と誤答の節は主張の側に移す(`ENTAILMENT_SYSTEM` が意味を説明する)。`judge` は判定値の列を集約し、規則は変えない。クラスタ要約・グロッサリ・ミューテーションは1件の呼び出しにする。
- **生成物の作り直し**(D-232、ADR-026): 想起素材・クラスタ要約・グロッサリの説明は、含意判定(クラスタ要約は実在性の検査も)に落ちた項目だけを `MAX_REGENERATIONS`(= 2、recall の D-171 と同じ)回まで作り直し、上限を超えたら従来どおり成果物を出さずに止める。recall は含意判定を1問の検査の最後に置き、検査名 `entailment` で同じ上限を共有する(D-191 を改める)。台本は文を生成しないので変えない。ログは ID と検査名・回数だけ。
- **台本段は判定しない**(D-248、ADR-030): `generate_scripts` は判定器を受け取らず、ラベル列挙・分割・答えの要素の欠けの検査を通れば成果物を書く。台本は想起素材の本文を並べ直すだけで、識別ポイントと答えの要素は想起素材の段で同じ reference と誤答の本文で判定済み(想起素材の成果物は全問が `consistent` のときだけ書かれる)。判定し直しは判定器のゆれで偽の停止の機会を増やし、台本段には作り直しが無いので試行を1回使う(試行6の c008-5、2026-10-20)。代償として、想起素材の段の偽陰性を台本段で拾い直す機会は無くなる。トラックが0本なら成果物を書かずに止める(従来は「主張0件なら uncertain」で止めていた)。
- **作り直しへの理由の受け渡し**(D-245): ポートの `summarize_cluster(members, retry=None)` と `generate_recall(question, language, retry=None)` に、前回の出力と落ちた検査(`Retry(previous, problems)`、`problems` は検査の名前とその対象の語句・主張の列)を渡す。アダプタは stdin に `retry` を足し、`SUMMARIZE_SYSTEM`・`RECALL_SYSTEM` に「前回の出力の問題を直して書き直す」ことと各検査の名前の意味を書く。`problems` はそれまでの全回の分を累積する。recall の検査の対象は `QuestionCheck` が持ち(検査を二重にしない)、選択肢は不透明キー(D-206)で渡す。recall の途中結果(ADR-028)は、作り直しの前に落ちた素材と理由を残し、再開しても同じ理由で作り直す。理由の本文はログと例外文に出さず、`Retry` は `repr=False`。
- **Tests**: 判定器のスタブが要素2だけに contradicted を返し続けると成果物が書かれずに非0終了する(recall は上限まで作り直してから `RegenerationExhausted`)。参照先の誤答と矛盾する識別ポイントでも止まる(想起素材の段で)。台本段は判定器を呼ばない。言い換えのフィクスチャでは誤検出 0。
- **What this requires of a person**: なし(判定能力そのものは測らない。D-043/D-044 のまま)。
- **Reversibility**: 可逆。

### NFR-2 → design
- **Implementation method**: `safety/invocation_log.InvocationRecord` から `stdout_masked`/`stderr_masked` を削除する。**子の出力から作った値を例外文に載せない**という規則を全パーサに掛け、例外文は種別と件数だけにする。対象は `llm_contract.parse_entailment`(判定語)、`parse_proposals`/`parse_recall`(未知のキー)、`claude_failure._tool_problem`(ツール名)、TRUNCATED と is_error の `subtype`/`stop_reason`。`cli.py` の warning は失敗種別だけを出す。`MaterialTextFilter` は残す(ADR-024)。
- **Tests**: `tests/support/claude_fake.py` が番兵の本文を返し、成功・SCHEMA・PARSE・TIMEOUT・未知の判定語・未知のキー・PROVIDER_ERROR(許可外のツール名)・INCOMPLETE_IDS・TRUNCATED を流す。
  - プロセス内: `cli.main` は root logger を INFO に設定する(`cli.py:717`)ので、テストは `cli` のログ設定関数を差し替えて DEBUG にし、NOTSET のハンドラで全レコードを集める。番兵が 0 件、必須の項目が残ることを確かめる。
  - 子プロセス: `python -m quiz_sift` をサブプロセスで走らせ、`main` の外へ抜けた例外が `_masked_excepthook` を通って stderr に出る経路でも、番兵が 0 件であることを確かめる。
- **What this requires of a person**: 診断が要るときは、合成の入力で再現を試みる(実教材を手で流さない、D-214)。
- **Reversibility**: 可逆。

### NFR-5 → design
- **Implementation method**: `pyproject.toml`、`src/quiz_sift/__init__.py`、`uv.lock` の quiz-sift の項目、`tests/test_cli.py` を 0.1.1 にする。受入テストは tomllib で pyproject と uv.lock の quiz-sift 項目を読み、`__version__` と `--version` の出力と突き合わせる。旧版の文字列はテスト内で組み立てて走査し、テスト自身が赤にならないようにする。uv.lock はホストで `uv lock` し、オフラインで通らなければ手で直して `uv lock --check` で確かめる。
- **What this requires of a person**: なし。
- **Reversibility**: 可逆(公開はしない)。

### NFR-7 → design
- **固定オーバーヘッド**: NFR-8 のフラグの実測と argv の変更の後、ホストで `tests/measurements/measure_overhead.py`(k = 1, 2, 3)を走らせ、`pipeline/overhead.OVERHEAD` の定数を差し替える。記録に argv を残す。合成プロンプトだけなので可逆。
- **合成リハーサル**: 合成デッキ(日英・複数正解を含む 10〜20 問)を `tests/fixtures/decks/` に置き、使い捨ての作業ルートに写して実 CLI で recall 段まで通す。exit 0、全検査の件数 0、含意判定の通過、再生成回数を `docs/test/measurements/recall-rehearsal.md` に記録する。レート枠は、utilization の差分が小数2桁に量子化されるので外挿に使わず、呼び出し数とトークン数を 190 問に外挿し、前サイクルの列の utilization とトークン数の比から使用率を推定する(D-218)。utilization の生の値も併記する。可逆。
- **フル処理**: 人の指示を受けた対話セッションのエージェントがホストで `test_full_run_logs.py exec` をバックグラウンドで走らせ、枠で止まれば `resetsAt` の後に同じ行を `--resume`(D-190、D-241)。記録の整形(`render`)も同じエージェントが同じ作業ルートに対して走らせ、件数だけの記録を渡す。ループは `out/` を読まず、記録の判定だけを行う(D-220)。受入基準は元の T-047 の A-1〜A-6 に戻し、定数は新しい列の値にする。再現性の2列は `reproducibility.md`。
- **途中結果の保持**(D-235、ADR-028): `StageCache` に、段のキャッシュ鍵ごとの途中結果の場所 `out/<deck>/<stage>/<inputs_digest>.progress/` を足す。項目1件を1ファイルにし、ファイル名は項目の通し番号にする(本文を名前に使わない)。一時パスから `os.replace` で確定し、段の成果物を確定したら場所ごと消す。
  - recall: 問題ごとに、出力のあった生成の回数と状態(「通過」「失格」「途中」)を残す。途中は、使った回数と、判定中の生成物と、判定が済んだ主張の判定値。再開時は、通過した問題を呼び出さずに使い、クラスタをまたぐ重複の検査では前の問題として数える。途中の問題は、残っている主張だけを判定し、回数の続きから数える。失格が1件でもあれば、その問題を作り直さずに `RegenerationExhausted` で止める(1回で走った場合と同じ)。
  - script: 途中結果を持たない(D-248、ADR-030。LLM を呼ばないので、再開は台本を最初から組み立て直す)。段のキャッシュ鍵から含意判定のプロンプトとモデルを外す。
  - 途中結果を使うのは実行の列(鍵に `run_id` が入る)だけ。通常モードは読みも書きもしない(AR-29)。
  - 呼び出しが失敗した生成(出力が無いもの)は回数に数えない。再開できるのは列が枠切れの停止だけで終わった場合で、他の失敗は従来どおり `not_completable`(`run_sequence`)。
  - ログは再開で使った件数と残りの件数だけ。
  - cluster 段は対象にしない(分類はデッキ全体の1回の処理、D-237)。作り直した cluster 段が枠切れで2回続けて止まったら、人は再開をやめて `/revise` に戻る。
- **glossary をフル処理から外す**(D-234): `cli.Pipeline.audio` はグロッサリを作らず、台本の音声だけを書く。グロッサリの音声は新しい段 `glossary-audio`(`--tts-model` と `--voice` は audio と同じ)にする。R-5 の基準と glossary 段の実装は変えない。フル処理は cluster → recall → script → audio。
- **フル処理の記録**(AR-13、AR-24): `render` は、列の中で**最後に**成功した cluster の実行の割当を記録に使う(試行3の列は判定器の変更で cluster を作り直すので、最初の成功は下流が使った割当と違う)。再現性の列の classify の `llm_calls` は、終了行の `llm_calls` から、他メソッドの出し直しでない子の数を引いて出す(他メソッドの子プロセス数をそのまま引く形は、TRUNCATED の出し直しで 0 になる)。メソッド別の内訳の比較も、出し直しを除いた数で行う。classify の分割呼び出しで出し直しが起きた列は、子プロセス数が ⌈N/25⌉ でなくなり赤になる。再現性の列は予算の外なので取り直す。
- **モデル**(D-236、ADR-027): `llm_prompts.MODELS` の含意判定を `claude-sonnet-5-5`、用語説明を `claude-opus-5-5` にする。`meter.PRICES` に Sonnet 5.5 を足す。`overhead.OVERHEAD` の Sonnet 5.5 の定数は、既存の2モデルと同じ較正(`measure_overhead.py`、k = 1, 2, 3)で別のタスクが足す。T-071 の判定は overhead の状態を読まないので、較正はフル処理を待たせない。
- **What this requires of a person**: 最小ホームと資格情報、CosyVoice を用意し、週次枠のリセット後に始め、止まれば `--resume` すること。
- **Reversibility**: フル処理は不可逆(教材本文が最小ホームのセッション記録に残る、レート枠の消費)。`reversible: false`、rehearsal は合成リハーサルと TTS プローブ、attempts は max 8(1回 = `--fresh-run` の列1本。exec を通さずに起動した列も数える。D-241 で 4、D-246 で 5、D-247 で 6、D-248 で 7、D-249 で数え落とした列 9bb4b32b を入れて 8)。較正とリハーサルは可逆。

### NFR-8 → design
- **切り詰めの出し直し**(D-231): `ClaudeCliLlm` の子プロセスを起動する1か所で、`TRUNCATED` で終わった子だけを同じ argv と stdin で最大 `MAX_REISSUES`(= 2)回出し直す。他の種別は出し直さない(D-101)。出し直しは `provider_invocations` に入り `llm_calls` には入らないので、2語の差はチャンク分割と出し直しで説明できる。ログは件数だけ。`RunMeter._ended_by_rate_limit` は、出し直された子(同じ `_invoke` の中で後に別の子が続いたもの)を判定から外す。アダプタは出し直しの子を `begin_invocation(..., reissue=True)` で知らせ、meter は直前の子を出し直し済みと印す。`classify_clusters` のように1回の `begin_call` が複数のバッチを持っても、バッチごとの最後の子が判定に入る。回復した子の失敗は `failures_by_kind` には残す(D-241、T-081)。
- **入力と出力の削減**(D-242、ADR-029): `llm_prompts.render_question` の分類用の形から `explanation` を外す(分類専用の関数にし、要約は今の形のまま)。子の環境は `workspace.child_env()`(HOME・PATH・LANG・TZ)のままで、どのメソッドも思考を止めない(D-246。D-242 で置いた `generate_recall` だけの `MAX_THINKING_TOKENS=0` と、そのためのメソッドごとの追加の環境の表は消す。`SubprocessRunner` の環境の鍵の検査は `CHILD_ENV_KEYS` との完全一致に戻す。NFR-8 (b2))。Haiku 4.5 の固定オーバーヘッドは今の環境と CLI で取り直し、較正の記録に環境の鍵を残す。合成デッキのリハーサル(`test_recall_rehearsal_logs.py`)は段を cluster → recall → script に広げ、script 段の exit 0 と呼び出し数・トークン数を記録する。
- **Implementation method**: `adapters/llm_claude_cli.py::build_argv` にユーザ設定を読まない指定(候補 `--setting-sources`)と有効ツールの許可リスト(候補 `--tools`、空のものを含む)を足し、拒否リストを外す(local。二重実装を避ける)。どちらのフラグもこのホストの CLI での存在は未確認。
- **ホストでの実測**(`tests/measurements/collect_claude_cli.py` にサブコマンド)。2段に分ける。
  - 第1段(argv を変える前、DAG の先頭): `claude --version` と `--help` にあるフラグの列挙と、許可リストの各候補の下での `init.tools`(StructuredOutput が消えて `--json-schema` が壊れないかを含む)。ここで argv の形を決める。SEC-001 の手段が無いと分かったら `/revise`(D-178)。
  - 第2段(argv を変えた後): 採取側は独自の argv を持たず、製品の `build_argv` をそのまま使う。記録の argv と `build_argv` の一致をテストで機械判定する。その最終 argv で、
    1. `init.tools` が `{StructuredOutput}` と一致する(拒否リスト無し、C-024)。
    2. SEC-001 の番兵(D-193、D-213): 中身検査を通さない直接起動。最小ホームの `.claude/settings.json` をその場で一時的に書き換え、到達できない `ANTHROPIC_BASE_URL`(`http://127.0.0.1:9`)を番兵にする。指定を外した argv では番兵のアドレスへの接続の失敗で落ち(stderr の分類で確かめる)、最終 argv では成功する。後始末は元のバイト列の sha256 の一致で確かめる。資格情報は写さない。
- argv が変わるので、契約フィクスチャ・home-probe の記録・`BARE_FLAGS` を第2段の後に取り直す。
- **(a) SDK の経路の削除**: `adapters/llm_anthropic.py` を削除する(D-216)。`tests/nfr8/` と `tests/adapters/test_llm_contract.py` の該当部分を直し、消えたアダプタに代わる陰性対照(anthropic を import する合成のアダプタ)を置く。依存 `anthropic` の削除は、イメージの作り直しと再ピンが要るので次サイクルに繰り越す(D-223)。
- **What this requires of a person**: ホストで最小ホームとログイン状態を保つこと。番兵の実測の間、最小ホームの設定が一時的に書き換わる(実測の道具が戻す)。
- **Reversibility**: 可逆(合成プロンプトだけ。D-108 と同じ扱い。最小ホームの書き換えは sha256 で戻ったことを確かめる)。

## Risks / trade-offs

1. 想起キーの照合先が問題文だけになり、検査は今より厳しくなる。汎用語キーの判定はプロンプトから他クラスタが見えず、再生成は偶然に頼る。合成リハーサルは実データの他クラスタとの重なりを再現しないので、上限超過で止まるリスクはフル処理まで残る。
2. CosyVoice が torch 2.10 で動くかは未確認。プローブで先に確かめる。
3. CLI のフラグが無い、または許可リストの下で StructuredOutput が消える場合、SEC-001 は `/revise`、SEC-002 は受容に回る。
4. 1試行で週次枠の約 15% を使う(試行6の実測は recall の終わりまでで 0.14)。試行8回(D-241、D-246、D-247、D-248、D-249)は数週にまたがりうる。
5. `Question` の型変更の波及が広い(src 約 12、tests 10 以上)。foundation のタスクが大きくなる。`cli.py` と `llm_claude_cli.py` を複数のタスクが触るので、`/tasks` で DAG の辺を引いて直列にする。
6. 通常モードの段キャッシュは、成果物の形が変わっても upstream が変わらない段で古い成果物を読みうる。フル処理は `--fresh-run` なので影響しない。
7. cluster 段は途中結果を残さない。判定を Sonnet に替えると cluster 段のキャッシュ鍵が動き、試行3の列は cluster から作り直す。1つの枠に収まらなければ、cluster 段も再開のたびに最初からになる(試行3では Opus の判定で 0.01 → 0.79 に収まった。Sonnet の判定が要約の作り直しを増やせば消費は上がる)。2回続けて止まったら打ち切る(D-237)。
8. 判定器が Opus 5.5 から Sonnet 5.5 に変わる。合成リハーサル(T-068)は Opus の判定で通したもので、Sonnet の判定と途中結果からの再開は、実 CLI ではフル処理で初めて通る(人の判断でリハーサルを足さない)。
9. 再開できるのは枠切れの停止だけ。recall の約 700 本の呼び出しのうち1本でも TIMEOUT などで落ちれば、列は `not_completable` になり、最後の試行が終わる(従来と同じ)。
10. script 段のラベル列挙の検査は、試行6と試行7で実データの全トラックを通った(D-247 の2つの形を直した後、列挙 0)。
11. 判定器(Sonnet 5.5)の判定は同じ主張でも回ごとに変わりうる。試行6では、recall 段で `consistent` だった識別ポイント1件が script 段で `contradicted` になり、判定し直した6回はすべて `consistent` だった。D-248 で台本段の判定をやめたが、recall 段の約 190 問の判定のゆれは残る。recall 段では `consistent` でない判定は作り直しになり(上限 2)、偽の停止ではなく作り直しの回数に表れる。台本段の2回目の判定が拾っていたかもしれない偽陰性は、拾われなくなる(NFR-1 の代償)。
12. 試行の予算は使い切っている(上限 8、使用済み 8。D-249 の上限は、数え落とした列を入れた後追いの承認)。exec を通さない起動が1本でも増えると、T-071 A-3 は取り返しのつかない赤になる。再現性の列の取り直しは予算の外だが、5時間枠の使用率が高い状態で始めると枠切れで止まり、1回の実行の列にならない(D-142)。

## Open questions
- なし。

## Adversarial review
> Findings from the independent `adversarial-reviewer` round before the mandate (procedure: design.md step 6),
> with the lead's disposition per finding. Blockers must be `fixed` or `disputed` (with the reason)
> before the gate; the human sees this table — and settles any unresolved dispute — at the mandate.

| ID | Severity (blocker/major/minor) | Finding (with counterexample) | Disposition (revise_* / request_expert / run_experiment / reduce_scope / dispute_finding: why) |
|----|--------------------------------|-------------------------------|-----------------------------------------------------|
| AR-D1 | major | ラベル列挙の判定はトラック単位で、1トラックに M の違う問題が混ざる。どの M を使うか書かれていない | revise_design: 人の決定でトラック内の最小 M(D-211)。R-6・C-006 も直した |
| AR-D2 | major | 拒否リストを外すと `--settings` が argv に残らず、C-023 の「フラグで渡す設定が効いたまま」の対象が無い | revise_requirement: 人の決定で条項を削った(D-212) |
| AR-D3 | major | 台本の識別ポイントが `distractor_ids` を持たず、台本段の reference に誤答の本文が入らない | revise_design: `Discrimination` に ID を引き継ぎ、台本段も `reference_for(q, distractors=…)` を使う |
| AR-D4 | major | 誤答の本文を「正しい情報」として reference に足すと、正しい識別ポイントが contradicted になる | revise_design: reference を正解と誤答の節に分け、ENTAILMENT_SYSTEM に誤答節の意味を足す。実判定の効果は合成リハーサルで確かめる |
| AR-D5 | major | `parse_entailment` 以外にも、子の出力から作った値が例外文に載る経路(未知キー、ツール名、subtype/stop_reason)がある | revise_design: 全パーサに「例外文は種別と件数だけ」を掛け、対象を列挙した |
| AR-D6 | major | `cli.main` が INFO に戻すので DEBUG を検査できず、excepthook の経路と一部の失敗経路が試されない | revise_design: ログ設定関数を差し替えて全レコードを集め、サブプロセスでも走らせ、失敗経路を足した |
| AR-D7 | major | 現行コードでは孫が pipe を持つ限り `communicate()` が戻らず、テストが赤にならず無限に待つ | revise_design: 孫の寿命を 60 秒に打ち切り、テスト側で `join(15)` の期限を持つ |
| AR-D8 | major | 番兵と init.tools の実測が argv を変える前に取られ、最終 argv での実測と `build_argv` との一致の判定が無い | revise_design: 実測を2段に分け、第2段は `build_argv` をそのまま使い一致をテストする |
| AR-D9 | major | utilization は小数2桁で、10〜20 問のリハーサルの差分からの外挿は 0 か過大になる | revise_design: 呼び出し数とトークン数から外挿する(D-218) |
| AR-D10 | major | 最初の上限超過で止めると、1試行で分かる不合格が1問だけになり試行予算を浪費する | revise_design: 全問を回し切ってから列挙して止める(D-217) |
| AR-D11 | minor | `_FULLWIDTH` が Ｅ・Ｆ・５・６・⑤・⑥ を正規化しない | revise_design |
| AR-D12 | minor | import 時の定数 `RECALL_SYSTEM` は monkeypatch で変わらず、テストが成り立たない | revise_design: `render_recall_system(fields)` を関数にし、関数で確かめる |
| AR-D13 | minor | 使い捨ての HOME には資格情報を写す必要があり refresh token が回転しうる。陰性対照の失敗の原因を区別する条件が無い | revise_requirement: 人の決定で最小ホームをその場で書き換えて戻す(D-213)。接続の失敗を stderr の分類で確かめる |
| AR-D14 | minor | `attempt_failures` と `llm_anthropic.py` の追従はどの要件も求めない | reduce_scope: `attempt_failures` は作らない(D-219)。`llm_anthropic.py` は人の決定で削除(D-216) |
| AR-D15 | minor | `render` は人の作業ルートの out/ を読むので、ループに教材の置き場所を読ませることになる | revise_design: render も人が走らせ、ループは out/ を読まない(D-220) |
| AR-D16 | major(提案レンズ L-DES-FAILURE-WALK) | `start_new_session` で、親が SIGHUP/SIGTERM で死ぬと子が残り、すぐの `--resume` で同じ最小ホームを2本が使う | revise_design: 親でシグナルを受けて killpg する(D-221) |
| AR-D17 | major(提案レンズ L-DES-ADR) | ADR-024 の欠点(診断に実教材の手流しが要る)が軽く書かれている | request_expert → 人が決め直さないと判断(D-214)。欠点を ADR-024 に明記した |
| AR-D18 | minor(提案レンズ L-DES-FAILURE-WALK) | ストアの作成と版の書き込みが別だと、途中のクラッシュで旧形式と誤判定される | revise_design: 1トランザクション(D-222) |
| AR-D19 | minor(提案レンズ L-DES-FAILURE-WALK) | 1文の TTS プローブは無音判定に意味が無く、間の挿入・mp3 の経路も通らない | request_expert → 人の決定で、合成の台本1本を audio 段と同じ経路で通す(D-215) |

**集計**: blocker 0、major 12(提案レンズ由来 2 を含む)、minor 7(提案レンズ由来 2 を含む)。すべて fixed(revise_design / revise_requirement / reduce_scope)、未解決の dispute は無い。

**要件の blocker 修正の再確認**: AR-R1 は解消済み(G-1 の32行目の字面も直した)。AR-R2 は要件の段で解消済み。設計の段に残っていた2点(実測 argv と `build_argv` の一致、陰性対照の失敗原因の区別)は AR-D8・AR-D13 で直した。

**提案レンズ**(採否はゲートで人が決める): L-DES-ADR、L-DES-FAILURE-WALK はこの回で適用し、指摘を上の表に入れた。

### 2026-10-14 の改訂(D-241・D-242、ADR-029)のレビュー

| ID | 重大度 | 指摘 | 対応 |
|---|---|---|---|
| AR-E1 | blocker | 子の環境の鍵は HOME・PATH・LANG・TZ に固定(`SubprocessRunner` が強制)で、思考を止める環境変数を足すと generate_recall の起動が落ちる | revise_requirement: NFR-8 (b2) に generate_recall だけの例外を書き、T-083 の scope に `claude_process.py` を足して、実物の runner の受入を加えた |
| AR-E2 | major | 1問1本の reference に誤答の節を置くと、答えの要素の判定に誤答が混ざり、C-013 の検査と R-4 と衝突する | revise_design: `focus` と誤答の本文を主張の側に移し、共通の reference は正解 N 件と解説だけにした。書き換える検査を T-084 A-4 に列挙した |
| AR-E3 | major | `classify_clusters` は1回の `begin_call` で複数のバッチを呼ぶので、`begin_call` の単位で最後の子を見ると、途中のバッチの失敗が隠れる | revise_design: 出し直された子だけを外す(アダプタが出し直しを meter に知らせる)。T-081 の scope と A-1 に分類の形を加えた |
| AR-E4 | major | render が journal の全実行の標準エラーを今のプロンプトで振り分けるので、プロンプトが変わると旧試行の記録で落ちる | revise_design: render は記録する列の標準エラーだけを読む(T-084 A-5) |
| AR-E5 | major | まとめた判定の精度の劣化は、通過の記録では見えない | T-068 A-4: 合成のミューテーションの検出数を記録する(合格ラインなし。D-044) |
| AR-E6 | major | リハーサルの記録が新しいコードで取られたことを判定できない | T-068 A-2: 記録のプロンプトとスキーマのダイジェストと子の環境の鍵を、今のコードと突き合わせる |
| AR-E7 | minor | T-081 と T-084 が同じテストファイルを触るのに辺が無い | T-082 に T-081 への辺を足した(T-081 → T-082 → T-083 → T-084) |
| AR-E8 | minor | 固定オーバーヘッドの定数の鍵に環境が入らない | 制約として ADR-029 と T-083 に書いた。環境はコードの表で決まり、記録の環境の鍵が表と一致することを T-083 A-2 で見る |
| AR-F1 | major | recall の検査は対象を返さない | `QuestionCheck` に対象を持たせ、`verify.py` を T-086 の scope に足した |
| AR-F2 | major | 前回の素材を元のラベルで渡すと D-206 の経路が開く | 不透明キーで渡すと明記し、A-2 に入れた |
| AR-F3 | major | プロンプトの変更を見る受入が無い | T-086 A-4 を足した |
| AR-F4 | major | T-068 の記録の照合が要約・想起素材のプロンプトを見ない | 照合に加えた |
| AR-F5 | minor | ログの検査が修正前から緑、`Retry` の repr | `repr=False` と、A-1・A-2 にログの検査を入れた |
| AR-F6 | minor | T-086 の scope の cli.py に理由が無い | 外した |
| AR-E9 | minor | T-071 の blocked_by の重複と、Notes の「リハーサルは足さない」が改訂と矛盾する | 直した |

### 2026-10-15 の改訂(D-246)のレビュー

| ID | 重大度 | 指摘 | 対応 |
|---|---|---|---|
| AR-G1 | blocker | 改訂の途中で T-083 を defer した記録が state と監査ログに残り、理由文(「T-068 が戻す」)が今の計画と食い違う | rein に defer を戻す手段は無い。T-083 は計画に戻し、rein が todo として frontier に置くことを確かめた(`rein dag`)。理由文の誤りは acceptance で示す |
| AR-G2 | major | 「16 問中 2 問以下」は 16 問では判定がぶれる(試行3の率でも約3割が赤、効果が無くても約6%が緑)。取り直しを繰り返しても記録に残らない | 合格ラインを外し、数を記録して T-071 の通過の承認で人が読む(T-083 A-4) |
| AR-G3 | major | 試行5の本当の失敗要因は、190 問のうち1問でも上限を使い切ること。合成デッキはその端を再現しない | 残るリスクとして mandate の承認と T-071 の通過の承認で示す。記録に上限まで使った問題の数を書く |
| AR-G4 | major | 閾値がどの claim にも無く、NFR-7 の「合格ラインは置かない」と矛盾する | AR-G2 の対応で閾値を外した |
| AR-G5 | major | 「上限を使い切った問題が無い」は、上限超過で recall が止まると記録自体ができないので空振りする | 「作り直しを上限まで使った問題の数」を記録する形にした |
| AR-G6 | minor | 思考なしの記録を前提にした陰性テスト(`test_record_taken_without_the_recall_env_is_red`)が、取り直しの後に赤になる | T-083 の To do に、向きを逆にする(MAX_THINKING_TOKENS を含む記録が赤)と書いた |
| AR-G7 | minor | 子の環境はキャッシュ鍵に入らないので、試行4の列を再開すると思考なしの途中結果を読み戻す。ADR-029 の「環境が変わるので鍵が動く」は誤り | ADR-029 の Consequences を直し、試行4の列は再開しないと T-071 に書いた |
| AR-G8 | minor | ADR-029・T-068・T-071・D-243・設計の古い記述 | 直した |
| AR-G9 | minor | done のタスクの上流に todo のタスクがある | T-081 は開き直さず(上限の変更は T-083 A-5)、T-083 だけが todo。`rein dag --validate` は通る |

### 2026-10-19 の改訂(D-247)のレビュー

| ID | 重大度 | 指摘 | 対応 |
|---|---|---|---|
| AR-H1 | major | プロンプトは既に本文でのキーの言及を禁じており、禁止を書き足しても対処にならない。作り直しの指示が原因の可能性 | 試行5の成果物で確かめた。漏れは 190 問中 34 問で、作り直しのあった問題はそのうち3問。作り直しの指示から漏れたのではない。プロンプトの禁止の書き足しをやめ、検査で落とす形にした |
| AR-H2 | major | 漏れ方の変種(「o1.」「O1）」「（ｏ１）」)を拾う規則が決まっていない | NFKC と小文字化のうえで ASCII の英数字に挟まれない `o1`…`o6` と決め、試行5で見つかった全部の形を A-1 に書いた |
| AR-H3 | major | 記録の option_keys の件数は最終の素材についての数で、常に 0 | 記録には recall のログの作り直しの行から、option_key で作り直した問題の数を書く(A-4) |
| AR-H4 | major | カタカナ・漢字に続く記号をすべて外すと「肢A」「オプションA」が素通りする | 外すのは、直前の連なりと合わせた字面がトラックの問題の問題文に現れる記号だけにした(A-3) |
| AR-H5 | major | 試行6の前の事前検査の道具がどこにも無い | T-087 に `precheck` サブコマンドを置いた(A-6)。T-071 の承認の前に走らせる |
| AR-H6 | minor | 「英数字に挟まれない」の書き方が文書ごとに違う | ASCII の英数字と統一した |
| AR-H7 | minor | 「正解はA」「答えA」の但し書きは何もしていない | 問題文の字面で決める規則に替えたので、但し書きを消した |
| AR-H8 | minor | フィクスチャが半角の「(o1)」だけでは試行5の形でない。作り直しの証拠が純粋関数のテストに無い | 全角を含む形を A-1 に書き、作り直しは test_generate.py のノードで見る(A-2) |
| AR-H9 | minor | 問題の本文に同じ字面のキーがあると、その漏れを落とせない | aip-c01 には該当が無いことを確かめた。T-087 の Notes に残す |
| AR-H10 | minor | 要求の所要の見積もりが古い | 試行5の実測に合わせた |

### 2026-10-20 の改訂(D-248)のレビュー

| ID | 重大度 | 指摘 | 対応 |
|---|---|---|---|
| AR-I1 | major | 判定のゆれを根拠にするなら、台本段の2回目の判定が recall の偽陰性を拾う確率も 0 ではない。代償が書かれていない | NFR-1・D-248・ADR-030・リスク 11 に代償として明記した |
| AR-I2 | major | 残した「要素の連結」の判定も判定済みの要素を並べただけで、同じ偽の停止の機会が残る | 人の決定で台本段の判定をやめた(ADR-030) |
| AR-I3 | major | 再開の受入基準が、テストを消しても緑になる。ハーネスが scope の外 | 台本段の途中結果をなくしたので、再開の基準は外した。A-2 は途中結果を持たないことを確かめる CLI のテストにし、`tests/support/` を scope に入れた |
| AR-I4 | major | T-071 の手順書が試行6のまま。blockedBy に T-088 が無い | 直した |
| AR-I5 | minor | 台本段の鍵が動かず、旧コードの途中結果を取り違えて読む | 途中結果をなくし、鍵から判定のプロンプトとモデルを外すので鍵が動く(A-2) |
| AR-I6 | minor | トラック0本の扱いが「主張0件」と区別されない | トラック0本なら成果物を書かずに止める(A-1) |
| AR-I7 | minor | 要求・ADR-022・設計の Tests とリスク 10 に古い記述が残る | 直した |
| AR-I8 | minor | MAX_ATTEMPTS に依存する断言が A-4 に入っていない | A-4 に `test_person_record_nodes_skip_until_the_record_exists` と exec を通さない列を足した |
| AR-I9 | minor | リハーサルの記録の検査が T-088 の変更を検出しない | script 段の呼び出し 0 と、記録のコミット ID が T-088 を含むことを A-3 にした |
| AR-I10 | minor | 誤答の ID の検証とそのテストが使われなくなる | 消す(recall の `_statements` に同じ検証がある) |

### 2026-10-22 の改訂(D-249)のレビュー

| ID | 重大度 | 指摘 | 対応 |
|---|---|---|---|
| AR-J1 | blocker | 再現性の列を1回の実行で通す条件(D-142)が要件にも T-071 にも無く、手順書の「枠切れなら同じ行を再実行」が再現性の列にも読める。取り直しの本数に上限が無い | NFR-7 と T-071 A-2 に条件を書いた。T-071 の To do に取り直しの手順(枠のリセット直後に起動、枠切れの列は捨てる、最大 4 本、超えたら人に報告)を書いた |
| AR-J2 | major | 数え落としは D-241 からあり、試行3〜7は起動時点の上限を1本ずつ超えていた。上限 8 が後追いの承認で残り 0 本であることが書かれていない | D-249・T-071・T-089・要件の Q ログ・リスク 12 に書いた |
| AR-J3 | major | T-089 の docstring の修正に基準が無い | T-089 A-2 を足し、C-013 を紐付けた |
| AR-J4 | major | 古い上限で render した記録(「上限は 7 本」で8行)が検査を通る | T-089 A-1 に上限の文言の一致を足した。render は T-089 の取り込み後と T-071 に書き、古い render は捨てた |
| AR-J5 | minor | 要件の所要の節が「試行7回」のまま、Q ログに D-249 が無い | 直した |
| AR-J6 | minor | T-089 の To do が直す箇所を挙げていない | 7 を前提にした断言と混ぜた組を列挙した |
| AR-J7 | minor | ADR-025 が「max 3」のまま | 改めた経緯と今の値の在り処を追記した |
| AR-J8 | minor | T-071 の手順書・Confidence・リスク 10 が試行7の前のまま | 直した |

## Self-assessment (assumptions, confidence)
- **Assumptions made**:
  - 子の CLI に `--setting-sources` と `--tools` に当たるフラグがある(未確認。実測の第1段を DAG の先頭に置く)。
  - 保存済みの生 HTML に N-of-M の15問が含まれ、選択アイコンの印で単一選択と複数選択を判別できる(未確認)。
  - 部分出力が `TimeoutExpired.stdout` に載る(CPython 3.13。実装時にテストで確かめる)。
  - reference を正解と誤答の節に分けたプロンプトで、実判定器(Opus)が正しい識別ポイントを contradicted にしない(未確認。合成リハーサルで確かめる)。
  - `uv lock` がホストで通る(未確認)。
- **Confidence**: アーキテクチャ=高(既存構成の延長) / データモデルの波及=中(範囲は広いが機械的) / 想起素材の実データでの通過=低(Risks 1) / 子 CLI のフラグ=低(未確認) / フル処理の所要=低(推定のみ)。
- **Open questions / points for the human to decide**: 提案レンズ L-DES-ADR・L-DES-FAILURE-WALK の採否(適用済み)。
