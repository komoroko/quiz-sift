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
  - **検査(1問単位、純粋関数)**: 空要素、`ungrounded_key`、`answer_mismatch`(要素の ID の集合が正解の集合と一致しない)、`missing_options`(名前は残し、中身は「`distractor_ids` が1件以上で、すべて誤答」。括弧の検査 `_QUOTED`/`referenced_options` は消す)、`generic`、`verbatim`。問題をまたぐのは `duplicate_keys` だけで、クラスタをまたぐ場合だけ数え(D-200)、クラスタ順で後の問題を落ちとする。
  - **再生成ループ**: `cli.Pipeline._recall_drafts` と `_Drafted` を廃止し、`recall/generate.py` に一本化する。1問ごとに `1 + MAX_REGENERATIONS`(= 3)回まで生成 → 検査。`LlmCallError` は再生成しない(D-101。ただし子プロセスの切り詰めはアダプタが出し直す。D-231)。`meter.stop_seen()` での早期停止を保つ(D-144)。上限を超えた問題があっても全問を回し切り、その後に超えた問題をすべて列挙して `RegenerationExhausted(SystemExit)` で止まる(D-217。メッセージは問題 ID・検査名・回数だけ)。
  - **含意判定**: 答えの要素は `reference_for(q, focus=要素の option_id)`、識別ポイントは `reference_for(q, distractors=distractor_ids)`(D-192)。不合格は再生成せず `EntailmentRejected`。
  - **reference の形**: 誤答の本文を「正しい情報」として渡すと、「X は当てはまらない」という正しい識別ポイントが contradicted になる。そこで reference を「正解: …」と「誤答選択肢(この問題では正しくない記述): …」の節に分け、`ENTAILMENT_SYSTEM` に「誤答選択肢の節は、この問題の答えとして正しくない記述の一覧である。statement が誤答選択肢を正しくないと述べることは矛盾ではない」と足す。プロンプトの変更は ADR-019 の運用の変更で、実判定での効果は合成リハーサル(C-026)で確かめる。
  - **成果物**: recall の JSON に `answers[{option, text}]` と `discrimination{text, distractors[]}`、report に `regenerations`(問題ごとの回数)を足す(検査ごとの延べ件数は作らない、D-219)。ログは件数だけ。
- **Existing assets to reuse**: `is_present`、`original_ratio`、`RecallKey.term_set`、`emit_if_consistent`、`ContractError`、`parse_proposals` の keyed 方式、`Meter`。
- **Corresponding technical choice**: ADR-022
- **What this requires of a person**: なし(合成リハーサルは NFR-7 に書く)。
- **Reversibility**: 可逆。コミットを戻せば元に戻る。

### R-6 → design
- **Required features/modules**: `script/label_guard.py`、`script/generate.py`、`domain/script.py`、`cli.py`(script)。
- **Implementation method**:
  - `_SERIES` を letter `ABCDEF`・number `123456`・kana `アイウエオカ` に、正規表現の文字クラスを A–F、1–6、①–⑥、ア–カに広げ、`_FULLWIDTH` の正規化に Ｅ・Ｆ・５・６・⑤・⑥ を足す。判定は「系列の先頭から M 件がすべて揃う」(日本語は present、英語は chain/markers。D-201)。判定はトラック単位なので、M はそのトラックに含まれる問題の M の最小値(D-211)。`find_enumerations` に M を渡す。
  - 台本の識別ポイントのセグメント `Discrimination` に、想起素材から `distractor_ids` を引き継ぐ。台本段の含意判定(`script/generate._claims`)は、識別ポイントについて `reference_for(q, distractors=…)` を使う(NFR-1 の台本の側)。
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
- **Tests**: 判定器のスタブが要素2だけに contradicted を返すと `EntailmentRejected` で成果物が書かれない。参照先の誤答と矛盾する識別ポイントでも止まる。言い換えのフィクスチャでは誤検出 0。
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
- **フル処理**: 人がホストで `test_full_run_logs.py exec` を走らせ、枠で止まれば `--resume`(D-190)。記録の整形(`render`)も人が同じ作業ルートに対して走らせ、件数だけの記録を渡す。ループは `out/` を読まず、記録の判定だけを行う(D-220)。受入基準は元の T-047 の A-1〜A-6 に戻し、定数は新しい列の値にする。再現性の2列は `reproducibility.md`。
- **What this requires of a person**: 最小ホームと資格情報、CosyVoice を用意し、週次枠のリセット後に始め、止まれば `--resume` すること。
- **Reversibility**: フル処理は不可逆(教材本文が最小ホームのセッション記録に残る、レート枠の消費)。`reversible: false`、rehearsal は合成リハーサルと TTS プローブ、attempts は max 3(1回 = `--fresh-run` の列1本)。較正とリハーサルは可逆。

### NFR-8 → design
- **切り詰めの出し直し**(D-231): `ClaudeCliLlm` の子プロセスを起動する1か所で、`TRUNCATED` で終わった子だけを同じ argv と stdin で最大 `MAX_REISSUES`(= 2)回出し直す。他の種別は出し直さない(D-101)。出し直しは `provider_invocations` に入り `llm_calls` には入らないので、2語の差はチャンク分割と出し直しで説明できる。ログは件数だけ。
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
4. 1試行が週次枠の大半を使う見込みで、試行3回は数週にまたがりうる。
5. `Question` の型変更の波及が広い(src 約 12、tests 10 以上)。foundation のタスクが大きくなる。`cli.py` と `llm_claude_cli.py` を複数のタスクが触るので、`/tasks` で DAG の辺を引いて直列にする。
6. 通常モードの段キャッシュは、成果物の形が変わっても upstream が変わらない段で古い成果物を読みうる。フル処理は `--fresh-run` なので影響しない。

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

## Self-assessment (assumptions, confidence)
- **Assumptions made**:
  - 子の CLI に `--setting-sources` と `--tools` に当たるフラグがある(未確認。実測の第1段を DAG の先頭に置く)。
  - 保存済みの生 HTML に N-of-M の15問が含まれ、選択アイコンの印で単一選択と複数選択を判別できる(未確認)。
  - 部分出力が `TimeoutExpired.stdout` に載る(CPython 3.13。実装時にテストで確かめる)。
  - reference を正解と誤答の節に分けたプロンプトで、実判定器(Opus)が正しい識別ポイントを contradicted にしない(未確認。合成リハーサルで確かめる)。
  - `uv lock` がホストで通る(未確認)。
- **Confidence**: アーキテクチャ=高(既存構成の延長) / データモデルの波及=中(範囲は広いが機械的) / 想起素材の実データでの通過=低(Risks 1) / 子 CLI のフラグ=低(未確認) / フル処理の所要=低(推定のみ)。
- **Open questions / points for the human to decide**: 提案レンズ L-DES-ADR・L-DES-FAILURE-WALK の採否(適用済み)。
