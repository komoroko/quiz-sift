# Design

> `/design` generates this, starting from `docs/10-requirements.md`.
> Important technical choices are **decided by the human via a `structured-question`** and recorded in `docs/decisions/ADR-*.md`.
> Finalized once a human approves at **the mandate**.

## Architecture overview

依存方向を一方向に固定し、**判定される純粋ロジック**と**外部に触るアダプタ**を物理的に分ける。
これは一般化のための抽象ではなく、2つの制約が同時に要求する最小形である。

1. D-008 が LLM / TTS を差し替え可能なインタフェース越しに使うことを決めている。
2. 品質ゲートは `network_profile: none` のサンドボックスで走る(NFR-6)。外部 API を呼ぶコードが
   テストの到達経路にあると、そこで止まる。

**設計の中心にある規律**: 受入基準が機械判定を要求している箇所は純粋関数に落とし、外部 API の応答は
その関数への**入力**にする。ラベル列挙判定・再生時間推定・無音構造判定・採点・誤答率降順・連番命名・
保存則は、これでネットワーク無しの `command` 証拠になる。

```
src/quiz_sift/
  domain/     問題・デッキ・クラスタ・想起素材・台本・音声メタ・学習記録の型
  ports/      Llm / Tts / Clock の Protocol(D-008 の実体。Browser は置かない → AR-26)
  adapters/   ports の実装。外部 SDK の import はこの中だけ
  ingest/     R-1  parse → normalize → validate → dedup → IngestReport
  extract/    R-2  Udemy DOM → 問題 + 正解根拠。HTML 文字列を入力とする純粋関数
  grounding/  語句の実在性判定(R-3 / R-4 / R-5 が共有。形態素解析 + 大文字略語)
  cluster/    R-3  排他分割 + 退化メトリクス + 要約の実在性判定
  recall/     R-4  想起キー / 答え / 識別ポイント + 弁別性・丸写し判定
  glossary/   R-5
  script/     R-6  台本セグメント列 + ラベル列挙判定 + 再生時間推定 + 分割
  audio/      R-7  合成 + 無音構造判定 + 連番命名 + m3u
  store/      学習記録(SQLite)。CLI と server の唯一の共有点(D-023)
  scoring/    R-8  出題セッション・採点・レポート(純粋)
  schedule/   R-10 誤答率降順の再生リスト
  decks/      R-11 デッキ名前空間と manifest
  server/     R-12 ローカル HTTP API
  pipeline/   NFR-4 段階キャッシュ / NFR-7 計測
  safety/     NFR-1 含意判定 / NFR-2 パス検証 / NFR-3 ログマスク
  cli.py      CLI 入口(D-033 の smoke の差し替え先)
web/          Next.js / React。表示と OptionId の送信のみ
gate.sh       CI と品質ゲートが同じ検査を呼ぶ単一の入口(NFR-5 (d))。リポジトリ直下なのは
              `scripts/` が guard.paths に含まれ、承認前に書けないため(N-3)
tests/        ゲートが走らせるテストとフィクスチャ
```

データの流れは一方向で、順序は要件が固定している(NFR-4 が段階として列挙している)。

```
data/<deck>/*.jsonl ─┬→ ingest ─→ cluster ─→ recall ─→ script ─→ audio ─→ out/<deck>/audio/
 (deck.yaml)         │                └→ glossary ──────────────→ out/<deck>/glossary/
 Udemy ─→ extract ───┘
                                                     store(SQLite) ←→ server ←→ web
                                                          ↑
                                              scoring ────┴──→ schedule → m3u
```

## Tech stack (finalized)

| Area | Choice | Rationale (ADR) |
|------|------|-----------|
| Language/runtime | Python 3.13 / Next.js · React | D-012, D-018 |
| LLM | **Claude Code CLI(`claude -p`、サブスクリプション認証)**。分類・要約・含意は `claude-opus-5-5`、想起・用語は `claude-haiku-4-5` の混成。`ports/llm.py` のタスク別メソッド越し | ADR-013, ADR-015(ADR-002 を一部 supersede), ADR-019(含意のモデル) |
| クラスタリング | LLM に直接分類させ、排他分割と「その他」は後処理で強制 | ADR-003 |
| TTS | CosyVoice(ローカル OSS、Apache-2.0) | ADR-004 |
| 形態素解析 | fugashi + unidic(語句実在性の判定) | ADR-010 |
| Udemy 抽出 | 既存ブラウザへ CDP 接続 + HTML 純粋関数 | ADR-007 |
| Data persistence | SQLite(`sqlite3`、stdlib)+ JSONL(問題)+ YAML(manifest) | ADR-005, ADR-011 |
| Web UI 結合 | Python が HTTP API、UI は表示と `OptionId` 送信のみ | ADR-006 |
| Testing | pytest / ruff / mypy strict。**依存はサンドボックスイメージに焼く** | ADR-001, ADR-012 |

## Implementation approach per requirement

> R-9 は欠番。承認前の縮小で要件ごと落とした(D-028)。

### R-1 → design

- **Required features/modules**: `ingest/reader.py`, `ingest/normalize.py`, `ingest/validate.py`, `ingest/dedup.py`, `domain/question.py`, `domain/report.py`
- **Implementation method**:
  入力1件ごとに `Ok(Question) | Rejected(reason, raw_id)` を返し、`IngestReport` が
  `accepted + rejected == input_total` を `__post_init__` の不変条件として持つ。保存則を
  「テストで確かめる性質」ではなく「壊すと構築できない構造」にする。
  除外理由は enum(`OPTION_COUNT_NOT_4` / `ANSWER_UNRESOLVED` / `DUPLICATE_ID`)。
  既習フラグは純関数 `resolve_studied(per_question, deck_default) -> StudiedFlag` で
  個別 → デッキ → `unknown` の順に解決する。`unknown` から他へ変換する関数を置かない。
  重複検出は正規化(空白畳み込み・NFKC・記号除去)後の完全一致とし、加えて `difflib.SequenceMatcher`
  の ratio 0.95 以上を「ほぼ同一」とする。
  **`IngestReport` は公式解説を持つ問題の件数と持たない問題の件数も報告する**(AR-10)。
  NFR-1 の照合先がどれだけあるかを承認前に知るためで、R-5 の `no_reference` の前提でもある。
- **Existing assets to reuse**: `json`, `difflib`, `unicodedata`, `dataclasses`, `enum`(すべて stdlib)
- **Corresponding technical choice**: ADR-011
- **What this requires of a person**: 教材ファイルを `data/<deck>/` に置くこと。正常系フィクスチャの内容を
  「妥当だと認める」こと(合成なので作者が書く)。
- **Reversibility**: **可逆**。読み取りと `out/` への書き出しのみ。コミットの revert と `out/` の削除で戻る。

### R-2 → design

- **Required features/modules**: `adapters/browser_cdp.py`(`fetch(url) -> str`)、`extract/udemy.py`、`extract/evidence.py`、`extract/writer.py`、`safety/gitignore_guard.py`
- **Implementation method**:
  **抽出器を「HTML 文字列 → 結果」の純粋関数にする**。ブラウザは HTML を取ってくるだけ。これで
  ヘッドレスブラウザ抜きに、合成 HTML フィクスチャに対するテストがサンドボックスで走る。
  この純粋関数がテスト容易性を与えているので、**`ports/browser.py` の Protocol は置かない**
  (D-008 が挙げるのは LLM・TTS・音声認識であってブラウザではない。実装1つ・呼び出し1箇所の
  抽象は要件が強制していない。AR-26)。
  正解の同定は `AnswerEvidence(selector_kind, element_path)` を全問に必ず記録する。
  「根拠の要素種別が全問で同一」は `len({e.selector_kind for e in evidences}) == 1` で判定する。
  **`selector_kind` は「実行時にどの抽出規則が実際にマッチしたか」から導出する**(AR-4)。
  定数にしてはならない — 定数なら集合サイズは常に 1 で、この判定は入力に依存しない**恒真式**になる。
  `try: 正解マーク要素 / except: 受験者の選択した要素` のフォールバックを持つ抽出器が両方の枝で
  同じ値を書けば、ゴールデンセット10件だけ第1の枝で当たり、残り190問は誤答を正解として取り込んだまま
  全基準が緑になる。**フォールバック枝ごとに別の `selector_kind` を返す。**
  **negative control**: 問題ごとに DOM 構造が異なる合成フィクスチャ(片方は正解マーク、片方は
  受験者選択マークしか持たない)を置き、**その集合に対して集合サイズが 2 になって赤くなること**を
  テストする。赤くなり得ない緑は証拠ではない。
  抽出失敗は `ExtractionFailure(question_index, missing_field, element_path)` として列挙し、
  `Question` の各フィールドを空文字禁止として型で縛る。
  **取得レベルの失敗**(CDP の切断、認証切れ、ボット検知)は `FetchFailure` として要素レベルの
  失敗とは別に受け、「抽出できた問題数と抽出できなかった問題数の両方が報告される」に両方が
  算入されるようにする(AR-5)。
  **公式解説を取得できた問題数と、できなかった問題数を `ExtractReport` に入れて報告する**(AR-10)。
  NFR-1 の照合先がどれだけあるかを承認前に知るための基準。
  **本文を持つ dataclass は `repr=False` を指定する**(AR-17)。素の例外トレースバックには
  `safety/log_filter.py` が効かないので、repr に教材本文が載ると NFR-2 の「ログ出力のいずれにも
  含まれない」が破れる。根拠レコードのレポートも `out/<deck>/` 配下にのみ書き、`gitignore_guard`
  の対象に入れる(`docs/` も `reports/` も追跡対象)。
  書き出し先は起動時に `.gitignore` 対象であることを自前のパターン評価器で検証してから開く
  (D-055)。**`.gitignore` の意味論は `fnmatch` と同じではない**(AR-16): ディレクトリ指定 `data/` は
  `data/deckA/q.jsonl` にマッチしなければならないが `fnmatch` では偽になる。先頭アンカー、`**`、
  否定 `!`、末尾スラッシュの区別も違う。実際の `.gitignore` は `data/` と `out/` の
  ディレクトリ形式なので、素朴な実装は1行目から間違える。危険な向きは偽陽性側で、「無視対象だ」と
  誤答すると実教材本文が追跡対象パスへ書かれる。NFR-2 は critical claim なので、**ホスト上で
  `git check-ignore -v` と自前評価器の判定を代表パス集合に対して突き合わせる artifact 検証を
  1本置く**(ゲートでは自前評価器の単体テスト、ホストでは等価性の実測)。
- **Existing assets to reuse**: `html.parser`, `fnmatch`, `pathlib`(stdlib)
- **Corresponding technical choice**: ADR-007, ADR-012
- **証拠種別**: 合成 HTML フィクスチャに対する抽出器のテストは `command`。
  **実教材10件のゴールデンセットに対する一致率 100% は `artifact`**(実教材は NFR-2 により
  コミットできず、サンドボックスから読めない。D-048)。
- **What this requires of a person**:
  (1) ログイン済みの Chrome をデバッグポート付きで起動しておくこと。
  (2) 対象コース / 模試の URL。
  (3) **ゴールデンセット10件の人手ラベル付け**(受験者の選択と正解が異なるものを3件以上含める)。
  (4) 二要素認証・ボット検知に遭遇した場合の手動介入。
  (5) **規約解釈の責任**(D-007。本ループは独立に検証していない)。
- **Reversibility**: **不可逆な面を持つ**。コードと抽出結果は revert / 削除で戻るが、
  **Udemy へのページ取得はアカウントにアクセス履歴を残し、パターンによってはボット検知を招く**。
  これは戻せない。CDP 接続によりパスワードはプログラムが持たないので不可逆面は縮んだが、消えていない。
  **→ タスクを「抽出ロジック(可逆)」と「実取得の実行(不可逆)」に分け、後者を `reversible: false` とする。**

### R-3 → design

- **Required features/modules**: `ports/llm.py`, `cluster/assign.py`, `cluster/metrics.py`, `cluster/summary.py`, `grounding/`
- **Implementation method**:
  **2層に割る**。上層 = LLM にグループ分けを返させる。下層 = 「生の応答 → 排他分割 + その他」の
  純粋アルゴリズムと、そこから計算されるメトリクス群。M/N・排他性・最大クラスタ比率・その他比率・
  全問題 ID の一覧は下層だけで機械判定できる。
  退化ガードのフィクスチャは `tests/fixtures/clusters/known5.jsonl`(既知5グループ、各3問以上)。
  合成なので NFR-2 の対象外でコミットできる。
  **negative control を必ず置く**(AR-11)。下層のガードの入力はスタブが返した割当なので、
  「5グループを返すスタブ」に対して緑になるだけでは**赤くなり得ない緑**である。
  退化スタブを2本(全問を1グループに返す / 全問を未分類で返す)固定フィクスチャとして置き、
  **そのスタブに対して最大クラスタ 50%・その他 20% のガードが赤くなること**をテストで要求する。
  これは受入基準の追加ではなく、既存基準の判定可能性の確保。
  **外部呼び出しの失敗は非 0 終了にする**(AR-5 / S-1)。`classify_clusters` は
  `ClassificationResult(assignments, failures: list[CallFailure])` を返し、**`failures` が空でない
  実行は下流ステージへ進まず非 0 終了する**。これは圧縮の質についての閾値ではなく「外部呼び出しが
  失敗した」という事実についての規則なので、**D-004(実データに合格ラインを置かない)とは衝突しない**
  — R-7 の `SynthesisReport` が既に同じ形を採っている。
  「サマリに警告を出して先へ進む」では足りない: 分割呼び出しの3回目が 429 で落ちて 120 問が
  「その他」に落ちても、排他分割も M/N も成立したまま、その他比率 60% の出力が 90 分の音声になる。
  警告を読むのは人間であって機械ではなく、見落とす経路を誰も塞げない。
  あわせて、**サマリに `other_ratio` / `max_cluster_ratio` / `failure_count` の3値が出力され、
  失敗件数が実際の失敗数に一致すること**を、失敗スタブに対する `command` 証拠として1本置く。
  キャッシュは NFR-4 の**成果物キャッシュのみ**とし、**LLM レスポンスキャッシュは置かない**
  (AR-26: どの要件も強制しておらず、置けば R-3 の「2回の実行で API 呼び出しが2倍になる」基準が
  それを検出するために存在することになる)。再現性測定は成果物キャッシュを迂回して2回走らせ、
  `llm_calls` が1回分の2倍になることを確認する。
  要約の語句実在性は `grounding/` が名詞句を切り出して照合する(R-4 と共有。重複実装を
  作らない)。**照合するのは要約の専門用語の断片だけ**にする(D-159 / D-161、2026-09-27 に「全名詞句」から改める)。
  断片は要約の本文から直接切り出す。日本語デッキは英字で始まる英数字の語とカタカナ2字以上の列、英語デッキは
  大文字略語と文頭以外の大文字始まりの語。漢字とカタカナが混ざった句(判断ポイント)はカタカナの列だけを照合する。
  漢字だけの一般名詞(判断、要点)はモデルが要約のたびに持ち込み、1語で列全体が止まる(T-047 の実データで
  最初のクラスタが止まった)。捏造の害が大きいサービス名は英字かカタカナで書かれるので、引き続き止まる。
  専門用語が0件の要約は照合を通り、残る守りは含意判定だけになる。この判定は `cluster/summary.py` に置き、
  R-5 の抽出器には依存させない(抽出精度の都合で R-3 の安全検査が黙って変わらないように)。
  要約プロンプトは見出し・箇条書きなしの平文で3文以内・200字以内と指示する(D-160)。**形態素解析だけでは足りない**: 実測(2026-09-22)で fugashi + unidic-lite は
  「Amazon EventBridge のルールで S3 のイベントを捕捉する」から Amazon / EventBridge / ルール /
  イベント / 捕捉 を正しく取ったが、**`S3` を `S` と `3` に分割した**。AWS のサービス名は
  この形(英字 + 数字)が主なので、大文字略語のヒューリスティックは補助ではなく**必須**(D-056)。
- **Existing assets to reuse**: `statistics`, `collections`, `itertools`。R-4 と `grounding` を共有。
- **Corresponding technical choice**: ADR-003, ADR-010
- **証拠種別**: 退化ガードと negative control、メトリクスの計算は `command`。
  **キャッシュ迂回2回の割当一致率は `artifact`**(D-030。スタブは決定的なので一致率が常に 100% になり、
  ゲートでは測る意味が消える)。
  **失敗の分類(ADR-013 の CLI 経路で形が変わった)**。`FailureKind` は `SPAWN` / `TIMEOUT` /
  `PARSE` / `SCHEMA` / `TRUNCATED` / `INCOMPLETE_IDS` / `PROVIDER_ERROR` の**7種**。
  **レート制限は特別扱いしない**(D-091 を 2026-09-25 に撤回): `PROVIDER_ERROR` として失敗に数え、
  下流へ進まず非 0 終了する。`rate_limit_event` が届いていた場合に限り、最後に届いた `resetsAt` をログに残す
  (D-123)。人は枠のリセット後に `--resume <run_id>` で再開し、その列が作った段はキャッシュから読む
  (NFR-7 節「実行の列」、ADR-018)。
  **撤回の理由**(2026-09-25 時点): レート制限と判定できる既知の値が1つも無かった。観測できた `status` は
  `allowed_warning` と `allowed` で、どちらも**成功した呼び出しにも付いてくる**(枠の残量通知)。
  `rejected` は claude 2.1.282 の実行ファイルのスキーマから読んだ値で、枠切れの実物は未観測(probe §3、D-127)。除外すると
  ホワイトリストが空になり、`RateLimited` の枝に到達する入力が存在しないまま「待機して継続」という
  成立条件の無い基準を凍結することになる。**判定順序の並べ替えも効かなかった** — `rate_limit_event`
  を読むには stdout を JSON として解釈する必要があり、枠切れで JSON でない出力が返るなら順序に
  関係なく `PARSE` に落ちる。**失敗に倒せば、判定表も待機も再試行も要らなくなる。**
  D-128 で「レート枠による停止」の述語が戻ったが、それは**列の完走判定にだけ**使う(NFR-7 節)。再試行・待機は作らない。
  **`stop_reason` / `is_error` / `subtype` は `result` イベントからだけ読む**(D-131。`assistant` メッセージにも
  `stop_reason: tool_use` が載るので取り違えない)。
  判定順序: 起動失敗 → **overage**(`PROVIDER_ERROR`。D-125、D-134)→ タイムアウト → stdout が **`result` イベントをちょうど1つ含む JSON のイベント列**に
  ならない(`PARSE`。**空の stdout、途中で切れて `result` の無い列を含む** — D-119、AR-100)→ 許可集合外のツール
  (`init.tools` の不一致か途中の `tool_use`。`PROVIDER_ERROR`。D-156)→ `subtype` が
  `error_max_turns`、または `stop_reason` が `max_tokens` / `model_context_window_exceeded` のどちらか
  (`TRUNCATED`。D-102、D-120。値は Messages API の定義から取った。CLI 経由での実測は無い)→ `is_error` が真
  (`PROVIDER_ERROR`)→ スキーマ不適合(`SCHEMA`)→ ID 完全性(`INCOMPLETE_IDS`)。
  **overage の走査はストリーム全体の厳格なパースと別に行う**: stdout を行ごとに `json.loads` し、
  `type == "rate_limit_event"` の行だけを拾う(読めない行は飛ばす)。タイムアウトでも途中までの stdout
  (`TimeoutExpired.stdout`)を走査する。こうしないと `PARSE` / `TIMEOUT` で終わった呼び出しの overage が
  記録されず、NFR-8 (h) の「呼び出しごとに記録」が欠ける。overage を `TRUNCATED` / `SCHEMA` /
  `INCOMPLETE_IDS` より前に置くのは、「真が1件でもあればその呼び出しを `PROVIDER_ERROR`」が
  切り詰めや ID 欠落の陰に隠れないため。
  **再試行しない**(D-101 を改訂)。レート制限を失敗に倒した結果、再試行する対象が1つも残らない。
  これにより `provider_invocations` が `llm_calls` を超える要因は**チャンク分割だけ**になり、
  2語の差が常に説明可能になる。
  **応答の切り詰め**(AR-62): CLI 経路には `max_tokens` 相当が無い。ID 完全性のためにスキーマへ
  `unassigned` を足した結果、応答は純増する。`CLASSIFY_BATCH_SIZE` は 50 から **25** へ下げ、
  `inputs_digest` の「生成パラメータ」には `max_tokens` の代わりに `--max-turns` とバッチサイズを入れる。
  **タイムアウトは子プロセス1本に置く**(既定 10 分)。**1回の実行**を実経過時間 12 時間で止める(AR-52、
  D-131)。列の壁時計には上限を置かず記録だけにする。上限は `cli.main` の `signal.alarm` で掛け、ハンドラが
  `SystemExit` の派生を上げる(D-138)。`subprocess.run` は子を kill してから上げ直し、`finally` が終了行を
  書く。外部の `timeout` コマンドは SIGTERM で終了行が書かれず「レート枠以外の停止」に落ちるので使わない。
  **ID 完全性**(NFR-8 経由で R-3 に入った新基準)。スキーマに `unassigned: string[]` を必須で
  足し、プロンプトを「どこにも当てはまらない問題は `unassigned` に入れる。すべての問題キーが
  ちょうど1回現れること」へ書き換える。`flatten(groups[].question_ids) + unassigned` が渡した
  キー集合と**多重集合として一致**しない呼び出しは失敗。現行の「バッチに無いキーは落とす」挙動(`llm_anthropic.py:186-187`)は新基準と正反対なので
  **削除する**。**残す SDK アダプタも新しい契約に従う**(AR-67): D-090 がそれを残すと決め、
  NFR-8 (c) がテストコードからの直接構築を認めている以上、旧挙動のまま残すと **R-3 の ID 完全性に
  違反するアダプタが、要件が認めた経路で構築可能な状態でリポジトリに残る**。パーサを共有すれば
  2つのアダプタの意味が分岐しない。この書き換えで `inputs_digest` が動き、全ステージの
  キャッシュが1回無効化される(NFR-4 の性質は保つ)。
- **What this requires of a person**:
  (1) ホストに `claude` が在りログイン済みであること、および ADR-014 の最小ホームのセットアップ。
  **API キーは要らなくなった**(ADR-013)。
  (2) **退化ガード用の合成フィクスチャ(5グループ×3問以上)と、退化スタブ2本を書き、
  そのグループ構造が「既知の正解」であることを保証すること**。
  (3) 実データの M/N と最大クラスタ比率、および警告を読んで圧縮の質を判定すること。
- **Reversibility**: コードと成果物は **可逆**。**課金は発生しなくなった**(ADR-013)が、
  不可逆な点は残る: (i) **教材本文がホストの Claude Code セッション記録に平文で書かれる**
  (D-086 が許容、D-088 が不可逆ゲートの根拠に据えた)、(ii) **サブスクリプションのレート枠の消費**。
  **→ 実 CLI を叩くタスクは分離したまま、`reversible: false` を維持する**(D-088)。

### R-4 → design

- **Required features/modules**: `recall/generate.py`, `recall/verify.py`, `domain/recall.py`
- **Implementation method**:
  想起キーを D-005 に従い `RecallKey(terms: tuple[str, ...])` として型に持つ(単一文字列にしない)。
  受入基準がそのまま書ける:
  - **重複禁止**: `len(keys) == len({frozenset(k.terms) for k in keys})`。
    tuple の等価比較にすると `("AWS","料金","設定")` と `("料金","設定","AWS")` が別物になり、
    **同じ組を並べ替えただけのキーが検出をすり抜ける**(AR-25)。D-005 は「語・状況・条件の**組**」と
    定義しており、組に順序の意味は無い。表示用の順序は tuple のまま保持してよい。
  - 全要素汎用語の禁止: 全 term が他クラスタにも出現するキーの件数 == 0
  - 他クラスタ出現率: 各 term について「該当クラスタ以外で出現するクラスタ数 / 全クラスタ数」
  答えと正解ラベルの矛盾判定は `safety/entailment.py` に委譲。矛盾検出時は**成果物を書かずに**非0終了
  (書いてから消す実装にしない)。
  **丸写し判定は全誤答選択肢に対して行う**(AR-12)。単数の誤答選択肢と比べる実装は、2件を
  貼り合わせた識別ポイント(「『誤答A』のに対し『誤答B』」)を通してしまう — どちらか1件との
  一致区間は全体の約半分で、残り 50% が閾値を超えるため。**全誤答選択肢に対する一致区間の和**を
  `difflib.SequenceMatcher.get_matching_blocks` で取り、それを除いた残りが全体の 30% 以上あることを
  要求する。上の形の識別ポイントが赤くなるフィクスチャを置く。
- **Existing assets to reuse**: `difflib`, `collections.Counter`。`grounding/` を共有。
- **Corresponding technical choice**: ADR-010
- **What this requires of a person**: API キー。生成された想起キーが本当に手がかりになるかの主観判定
  (機械判定は「汎用語でない」までしか言えない)。
- **Reversibility**: **可逆**(API 課金分は R-3 のフル処理タスクに乗る)。

### R-5 → design

- **Required features/modules**: `glossary/extract_terms.py`, `glossary/describe.py`, `glossary/verify.py`
- **Implementation method**:
  照合結果を **3値** `consistent(n) | contradicted(n) | no_reference` にし、`no_reference` を
  `consistent` に潰す経路を型で塞ぐ(受入基準「照合対象なしを合格として報告しない」の実体)。
  専門用語の抽出は fugashi + unidic の名詞句抽出に、大文字略語(S3, IAM)とカタカナ列の
  ヒューリスティックを併用する。
  グロッサリ音声は `out/<deck>/glossary/` に `g001_...` の**別連番系列**で出し、専用の m3u を
  もう1本出す。想起素材の再生リストには混ぜない(D-049)。
- **Existing assets to reuse**: `grounding/`, `safety/entailment.py`, `audio/naming.py`
- **Corresponding technical choice**: ADR-010
- **What this requires of a person**: API キー。**公式解説が0件だった場合に「照合対象なし」で進めることの
  承認** — R-1 / R-2 が件数を報告するので承認前に判断材料は揃う。
- **Reversibility**: **可逆**。

### R-6 → design

- **Required features/modules**: `script/generate.py`, `script/label_guard.py`, `script/duration.py`, `script/split.py`, `domain/script.py`
- **Implementation method**:
  **台本を文字列ではなくセグメント列として持つ**: `Prompt | Pause(seconds) | Answer | Discrimination`。
  「構成が問いかけ→間→答え→識別ポイントの順」はセグメント列の型と順序検証で機械判定でき、
  文字列の中を正規表現で探す実装にしない。`【間】` のような地の文が混入する経路が構造的に消える。
  「間」は `Pause(seconds=3.3)` セグメント。TTS へ渡す直前に adapters 層が扱う(ADR-004)。
  **ラベル列挙判定**(純粋関数、`command` 証拠):
  - **判定単位は「1トラック分のセグメント列を連結したもの」**(AR-13)。セグメント内に閉じると、
    Prompt「アの場合とイの場合を思い出してください」/ Answer「ウが正解です」/
    Discrimination「エは別のサービスです」のように**セグメントを跨いで4件が並ぶ形を素通しする**。
    耳で聞けば4択のラベル列挙そのもので、一問一答形式の生成器はこの形を自然に作る。
    セグメント内判定はこの単位の部分集合として残す。
  - 日本語: `A/B/C/D`・`1〜4`(「1番」「①」含む)・`ア〜エ` のいずれかの系列が **4件並列**で
    出現する形を検出。単独出現は許す。
  - 英語: 「区切り記号で並ぶ4つの単独ラベル」という**列挙構造**を検出する。単独トークンの禁止には
    しない(冠詞の "a"、"Availability Zone A" のような正当な出現を落とさないため)。
  再生時間推定 `estimate_seconds(segments, language)` = Σ(セグメント長 × 言語別レート)+ Σ(Pause 秒数)。
  初期レートは日本語 7.0 文字/秒、英語 2.5 語/秒とし、初回実測後に見直す(D-052)。
  **3分超過時は台本のみを分割**する。1クラスタが複数トラック(`012a_`, `012b_`)になり、クラスタは
  割らないので R-3 の M は動かない(ADR-008)。
  **分割は「1問分のセグメント列」= `Prompt, Pause, Answer, Discrimination?` を最小単位とし、
  問の境界でしか割らない**(AR-8 / S-3)。`Prompt | Pause | Answer` を最小単位にすると
  **最後の `Answer` の直後が正当な境界になり、`Discrimination` だけのパートが作れてしまう** —
  それは `Pause` を1つも含まないので R-7 の無音構造基準を必ず落とし、AR-8 が名指しした穴が復活する。
  `Discrimination` で始まるトラックができれば R-6 の順序基準も落ちる。
  **どのパートも `Pause` を1つ以上含み、かつ `Prompt` で始まること**をテストで固定する
  (長さで割る実装と、末尾の `Discrimination` を切り離す実装の両方がこのテストで赤になる)。
  1問分でも3分を超える場合は分割せず、再生成側へ倒す。
  超過したクラスタは列挙する。
- **Existing assets to reuse**: `re`, `unicodedata`, `dataclasses`
- **Corresponding technical choice**: ADR-002, ADR-008
- **What this requires of a person**: API キー。3分という上限が妥当かの実測後の見直し(D-010)。
- **Reversibility**: **可逆**。

### R-7 → design

- **Required features/modules**: `ports/tts.py`, `adapters/tts_cosyvoice.py`, `audio/synthesize.py`, `audio/silence.py`, `audio/naming.py`, `audio/playlist.py`, `domain/audio_meta.py`
- **Implementation method**:
  **「間」は TTS に頼らず、自前で PCM 無音を生成して結合する**。CosyVoice は SSML の break を
  持たないが、これは不利ではない: 無音の長さと位置が生成側で決定的になる(ADR-004)。
  セグメントごとに合成し、`Pause` セグメントは無音 PCM として挟み、連結して書き出す。
  **挿入する無音は 3.3 秒、判定のしきい値は 3.0 秒**(D-053)。実測(2026-09-22)で 3.000 秒の
  無音を mp3 にエンコードし直すと **2.992 秒**になった。3.0 秒ちょうど挿入する実装は
  受入基準「3 秒以上」を通らない。挿入側に余裕を持たせ、判定側は基準どおりに置く。
  **mp3 の書き出しも ffmpeg で行う**(AR-27)。`wave` は WAV しか書けない。エンコード設定は
  CBR とし、先頭のエンコーダ遅延/パディングが無音判定に与える影響を固定する。
  **無音構造判定は「デコード済み PCM フレーム列 → 判定」の純粋関数**。
  `detect_structure(frames, sample_rate, *, silence_dbfs, min_silence_sec) -> list[Run]` が
  `[Voiced, Silent(3.3s), Voiced]` のような区間の列を返す。判定式は**要件の文言に揃える**(AR-9):

  > 3.0 秒以上の `Silent` Run が存在し、その **前後いずれにも `Voiced` Run がある** ものが1つ以上。

  設計初版の `runs[0].voiced and runs[-1].voiced` は要件に無い条件(ファイルが有音で始まり有音で
  終わる)を足しており、mp3 のエンコーダ遅延や TTS 出力の息継ぎ・余韻で落ちる。
  `min_silence_sec` の意味論は「**これ未満の無音は `Silent` Run として出さず、前後の `Voiced` に
  吸収する**」と定める(未定義だと先頭 30 ms の無音で判定が反転する)。先頭/末尾に短い無音を含む
  合成 PCM に対して緑になることをテストで固定する。
  `audioop` は Python 3.13 で削除済みなので RMS は `array` / `struct` で自前に書く。
  連番命名 `f"{seq:03d}{part}_{deck_id}_{cluster_id}.mp3"`。生成したファイル名リストを `sorted()` した
  結果が意図順と一致することを機械判定する。あわせて m3u を出す。
  言語とボイスの一致は `AudioMeta(language, voice_id)` を sidecar JSON に書き、コード内定数表
  `VOICE_TABLE[voice_id].language == meta.language` で判定する。
  **音声化に失敗したクラスタは `SynthesisReport(succeeded, failed: list[SynthesisFailure])` として
  列挙する**(AR-10)。部分成功時はプロセスを非0終了させ、下流(R-10 の再生リスト)が欠けた
  トラックを参照しないようにする。
- **Existing assets to reuse**: `wave`, `struct`, `array`, `pathlib`(stdlib)、ffmpeg(エンコードとデコード)
- **Corresponding technical choice**: ADR-004, ADR-001, ADR-012
- **証拠種別**: `detect_structure`・連番のソート順・m3u の形式・ボイスと言語の一致・失敗列挙は
  すべて `command`(合成 PCM に対して判定)。
  **実際に生成した mp3 に無音区間があることは `artifact`**(ADR-012)。CosyVoice は pip で入らず、
  torch を含めるとイメージが数 GB 級になるため、ゲートに重みを置かない。ホストで1回実測して記録する。
- **What this requires of a person**:
  (1) **artifact 実測が走る前に**、CosyVoice をホストに導入しモデル重みを用意すること
  (git clone + サブモジュール + torch)。実測コマンド自体はループが行う(N-2)。
  (2) 日本語 / 英語それぞれで使うボイスの選択。
  (3) 生成された mp3 を**携帯端末へ手動転送すること**(D-016)。
  (4) 端末の音楽アプリがファイル名昇順で並べることの確認(端末依存で、ループは検証できない)。
- **Reversibility**: **可逆**。ローカル OSS なので API 課金が無く、**R-7 は不可逆点を持たない**。

### R-8 → design

- **Required features/modules**: `scoring/session.py`, `scoring/grade.py`, `scoring/report.py`, `store/`
- **Implementation method** — **AR-4(要件段階)の裏切り実装を「型で不可能にする」のがここの眼目**:
  出題セッションは `PresentedQuestion(question_id, presentation: tuple[OptionId, ...])` を返す。
  `presentation` は選択肢 **ID** の並び。
  回答の受け口は `submit(session_id, question_id, chosen: OptionId)` **のみ**。
  **表示位置インデックス(0〜3)を受け取る API を存在させない**。採点は `chosen == correct_option_id`。
  こうすると「シャッフル後の位置とシャッフル前の正解インデックスを突き合わせる採点器」は、
  書けるコード上の経路が無い。100%/0% のテストはフィクスチャの `correct_option_id` をそのまま
  送る / 別の ID を送る、で書ける。表示位置で入力を組み立てる余地が無い。
  **シャッフルの seed は「セッション ID × 問題 ID」から導出する**(AR-7)。問題 ID だけから
  導出すると**同じ問題はいつ出題しても同じ順列**になり、事前テストで「2番目が正解だった」と
  位置を覚えた人は事後テストでも2番目を選んで正解する。基準の文言(シャッフルされている)は
  緑のまま、括弧書きが名指しした偽陽性がそのまま残る。**同一問題の2セッションで `presentation` が
  異なること**を固定 seed のテストで機械判定する。
  所要時間は `ports/clock.py` 越しに測り、テストでは固定 clock を注入する。
  **レポートは既習フラグの値ごと(`studied` / `unseen` / `unknown`)に正答率を分けて出す**(AR-10)。
  D-015 が要求している当のもので、`unknown` 一群だけのレポートは D-015 の目的を満たさない。
  レポートの限界開示文(hold-out 不在 + テスト効果)はモジュール定数とし、出力に必ず含まれることを
  テストで検査する。
  **UI 側では採点しない**(ADR-006)。
- **Existing assets to reuse**: `random`, `statistics`, `datetime`, `sqlite3`(すべて stdlib)
- **Corresponding technical choice**: ADR-005, ADR-006
- **What this requires of a person**: 限界開示文の文言のレビュー。**実受験は次サイクル**(D-003)。
- **Reversibility**: コードは **可逆**。学習記録への書き込みは**追記のみなら可逆**。
  破壊的な書き換え(スキーマ移行、既存回答の上書き)は**不可逆**。本サイクルは記録が空から始まるので
  実害は小さいが、**次サイクル以降のスキーマ移行タスクは不可逆点になる**。

### R-10 → design

- **Required features/modules**: `schedule/rank.py`, `schedule/playlist.py`
- **Implementation method**:
  `rank_clusters(attempts) -> list[ClusterRank]`。誤答率 = クラスタ内の誤答数 / 出題数、降順。
  同率のタイブレークは「最終学習日時が古い順 → クラスタ ID 昇順」で決定的にする(D-054)。
  全問正解時は空リストではなく `NoTargets` を返し、型で区別する。
  **出力は `out/<deck>/review/` の別系列へ複製し、そこで誤答率順の連番を振る**(AR-24)。
  R-7 の `out/<deck>/audio/` はクラスタ順の連番で、そこを改名すると R-7 の「ファイル名昇順ソートで
  意図した順に並ぶ」が壊れる。別系列にすることで両方の順序が同時に成立する
  (グロッサリが D-049 で同じ形を採っているのと揃う)。可能なら OS のハードリンクで複製し、
  端末へは復習系列のディレクトリだけを転送する運用にする。m3u も同じディレクトリに出す。
- **Existing assets to reuse**: `audio/naming.py`, `audio/playlist.py`
- **What this requires of a person**: 事前 / 事後テストを受けて学習ログを作ること(本サイクルでは
  合成ログでの動作確認まで)。**有効性は検証できないことの了承**(要件に明記済み)。
- **Reversibility**: **可逆**。出力ファイルのみ。

### R-11 → design

- **Required features/modules**: `decks/manifest.py`, `decks/registry.py`, `store/DeckScopedStore`
- **Implementation method**:
  **すべての識別子を `(deck_id, local_id)` の複合キーにする**。`QuestionId` / `ClusterId` を
  `deck_id` を含む frozen dataclass にし、クラスタリング関数が1デッキ分の入力しか受け取らない
  (`cluster(deck: Deck)`)ようにすれば、**問題の混入**は経路自体が存在しない。
  **ただしそれは第1基準にしか効かない**(AR-18)。第2基準「学習記録・レポートがセット単位で
  分離される」に対して `deck_id` 列はただの列で、`WHERE deck_id = ?` を1本書き忘れた集計クエリは
  型検査にも mypy strict にも引っかからない。**ストアへのアクセスを `DeckScopedStore(deck_id)`
  経由に限定し、生の接続を `store/location.py` の外に出さない**。2デッキ分のフィクスチャで
  集計が分離することを R-8 / R-10 のテストに入れる。
  ディレクトリも分離: `data/<deck_id>/`, `out/<deck_id>/`。
  デッキ manifest `data/<deck_id>/deck.yaml`(人が書く): `deck_id`, `title`, `language`(`ja`/`en`)、
  `default_studied`(`studied`/`unseen`/未指定)。R-1 の既習フラグ解決の「セット指定」がこれ。
  **デッキ ID は不変**とする(D-059)。
  日英混在は CosyVoice が文内コードスイッチを扱うので、**分割せずそのまま渡す**(D-060)。
  1ファイル内に2言語が現れても「使用したボイス」は単数のままなので、R-7 の言語とボイスの一致判定も
  そのまま成立する。
- **Existing assets to reuse**: PyYAML(`pyproject.toml` の明示的な依存として宣言済み)
- **Corresponding technical choice**: ADR-011, ADR-004, ADR-005
- **What this requires of a person**: **デッキ manifest を書くこと**(言語、デッキ単位の既習 / 初見指定)。
  これが無いと D-015 の集計分離が達成できない。どの問題集をどのデッキに入れるかの判断。
- **Reversibility**: **可逆**。ただしデッキ ID の変更は移行を伴うので、不変として扱う。

### R-12 → design

- **Required features/modules**: `server/`(`ThreadingHTTPServer` ベースのローカル API)、`web/`(Next.js / React)、`store/location.py`
- **Implementation method**:
  **API が学習記録ストアへの唯一の書き込み経路**。UI に `localStorage` への永続化経路を作らない。
  CLI が生成した成果物は **UI から読むだけ**。`web/` から LLM SDK を import しないことをレビューで見る。
  **ストアの所在と形式は `store/location.py` の1箇所で定義**し、CLI も server もそこしか呼ばない。
  所在をハードコードする第2の経路を作らない(受入基準の実体)。
  2プロセス構成が持ち込む3点を設計で塞ぐ(AR-15):
  - **オリジン**: ブラウザは `localhost:3000` から API へ `POST application/json` を投げる前に
    preflight(`OPTIONS`)を出す。`BaseHTTPRequestHandler` は未実装メソッドに 501 を返すので、
    そのままでは**回答の送信がブラウザ側で失敗する**。**Next.js の `rewrites` で同一オリジンに
    寄せる**(CORS ヘッダを自前で付けるより、許可オリジンが増えない分だけ安全)。
  - **スレッドと接続**: `HTTPServer` は逐次処理なので1リクエストの DB 待ちで UI 全体が止まる。
    `ThreadingHTTPServer` を使い、**SQLite の接続はリクエストごとに開いて閉じる**。
    起動時に作った接続をハンドラスレッドで使うと `check_same_thread` に当たって `ProgrammingError`
    で落ちる。ADR-005 の WAL は**プロセス間**の話で、この制約には効かない。
  - **成果物の配信**: `out/` は Next.js の `public/` の外なので、API に読み取り専用の配信
    エンドポイントを1本置く。`gitignore_guard` の対象パス配下だけを配信対象にする。
  起動は2プロセス(`quiz-sift serve` と Next.js)。
- **Existing assets to reuse**: `http.server`, `sqlite3`, `json`(stdlib)
- **Corresponding technical choice**: ADR-006, ADR-012
- **証拠種別**: API 契約テスト(セッション生成・提出・採点・レポート)は `command`。
  **ブラウザから実施・閲覧できることは `artifact`**(ADR-012)。node と Playwright を
  ネットワーク遮断のイメージに入れる経路が塞がっているため、ホストで E2E を1回走らせて
  ログとスクリーンショットを記録する。
- **What this requires of a person**: ポート番号の選択。**artifact 実測が走る前に `npm ci` と
  `next build` をホストで済ませておくこと**(実測コマンド自体はループが行う。N-2)。
- **Reversibility**: **可逆**。ローカル起動のみ、外部公開なし。

## How non-functional requirements are met

### NFR-1: 誤学習の防止

`safety/entailment.py` が `EntailmentVerdict = consistent | contradicted | uncertain` を返す。
判定は LLM による含意判定(D-024)。**`contradicted` と `uncertain` の両方で成果物を書かずに非0終了**
する(D-042)。対象は想起素材・台本・グロッサリ・クラスタ要約。
検証は**手書きフィクスチャ**(D-043): 仕込んだ矛盾の検出、言い換えに対する誤検出0、
ミューテーションに対する検出率の記録。**測れるのは判定器のロジックであってモデルの判定能力ではない**。
検出率に合格ラインは置かない(D-044)。この2点は人間が承知のうえで残した穴で、下の Risks と
Open questions に記録してある。

**判定のモデルとプロンプト(ADR-019、D-163)**: 判定は `claude-opus-5-5` で行う。プロンプトは、statement が reference の複数の問題をまとめた要約でありうることを明示する。省略・一般化・reference に書かれていない事柄に触れていないことは矛盾ではなく、statement の主張が正解・解説と両立しないときだけ `contradicted` とする。Haiku と2文のプロンプトは、32 件中 1 件の要約を `contradicted` とした。同じ要約を、修正プロンプトの Haiku と Opus はともに `consistent` と判定した(2026-09-28 の切り分け)。1件で列全体が止まる(D-042)ので、T-047 が完走できなかった。

### NFR-2: 教材データの保護

`safety/gitignore_guard.py` を全書き出し経路の入口に置き、起動時にパスが `.gitignore` 対象である
ことを検証してから開く。**自前評価器と本物の git の等価性を artifact で実測する**(AR-16)。
`safety/log_filter.py` が問題文本文をログへ出さず、**本文を持つ dataclass は `repr=False`**
(素の例外トレースにはフィルタが効かない。AR-17)。根拠レコードのレポートも `out/<deck>/` 配下のみ。
合成フィクスチャは対象外なのでコミットできる(要件に明記済み)。

### NFR-3: 認証情報の保護

`safety/secret_mask.py` を logging の Filter として登録する。Anthropic API キーは環境変数からのみ読む。
**Udemy の認証情報はプログラムが保存しない** — CDP で既存のログイン済みブラウザに接続するため
(ADR-007)。ただし「保存しない」と「露出しない」は別で、**CDP 接続はセッションクッキーを
読み出せる**(AR-28)。マスク対象に `Cookie` / `Set-Cookie` / `sessionid` 系のパターンを残す。

ログを通らずに `cli.main` の外へ抜けた例外は、インタプリタが `sys.excepthook` で stderr に書く。
この経路には Filter が効かないので、`cli.main` が `sys.excepthook` を伏せるフックに差し替える。
フックはトレースバック全文(原因の連鎖を含む)を `mask_secrets` に通し、呼ばれた時点の `sys.stderr` へ書く。
直前のフックは呼ばない。フック自身が失敗したら型名だけを書き、平文へ戻さない。例外は捕まえない(D-157)。

### NFR-4: 段階的な再実行性

`pipeline/stage.py` が各ステージを `(inputs_digest) -> artifact` のコンテンツアドレス方式で
`out/<deck>/<stage>/` に永続化する。`ports` 層の呼び出しカウンタで「前段の呼び出し0」を機械判定する
(数えているのは自分の呼び出し回数なので、スタブで測って正当)。

**`inputs_digest` の構成要素を明示する**(AR-21): 前段成果物の内容 + プロンプトテンプレートの digest
+ モデル ID + 生成パラメータ。**ステージ実装コード自体は入れない**。前者を入れないと、
プロンプトを直して再実行しても digest が変わらず旧成果物がキャッシュヒットで返り、
「直したのに変わらない」に誰も気づかない。後者を入れると無関係な編集で全ステージが再実行され、
NFR-7 のコストが毎回乗る。

**キャッシュ層と配信層を分ける**(AR-14)。`out/<deck>/<stage>/` は digest 名のキャッシュ、
`out/<deck>/audio/` は連番の配信名。配信側は再実行のたびに**キャッシュから作り直す**
(古い `012_` が残って端末に古い音声が転送されるのを防ぐ)。
**ステージ出力は一時パスへ書いて完了後に `os.replace` する**。合成の途中で落ちると digest 付きの
パスに切り詰められた mp3 が残り、次の実行がそれをキャッシュヒットとして読む — NFR-4 の
「前段の外部 API 呼び出し 0」は緑のまま、壊れた音声が下流に流れる。

R-3 の再現性測定はこの対象外とし、`artifact` 判定(D-030)。

### NFR-5: 開発基盤と CI

uv / ruff / mypy strict / pytest / pre-commit / mkdocs。テストは `tests/`(`src/` は `guard.paths` に
含まれるため、mandate 承認までスケルトンを置けない)。

**(d) の「同一のコマンド列」は `gate.sh` で構造的に保証する**(AR-6)。CI の `run` も
品質ゲートの argv も `bash gate.sh <step>` を呼ぶ。**同一性の対象は検査ステップだけで、
環境を用意する手順(`uv sync` など)は含まない** — ゲートは依存が焼き込まれたイメージで走り、
`uv sync` は `--network none` では原理的に動かないため。**この線引きは要件 NFR-5 (d) に
書き換え済み**(S-2)。要件は「部分集合」ではなく**集合の一致**を求める — でないと CI から
検査を1本落としても「同一のコマンド列がゲートで緑」が真になる。
`tests/test_gate_environment.py` が (1) 両者の `<step>` 集合の一致、(2) 各ケースが名前どおりの
ツールを呼び失敗を握りつぶしていないこと、(3) イメージの実バージョンが焼き込み requirements と
一致すること(N-1)を機械判定する。共通スクリプト化は「2箇所がずれる」を「1箇所を緩めれば
両方同時に緩む」に置き換えるので、(2) が無いと `lint) ruff check . || true` の1行編集で
CI とゲートが同時に無害化される。

**(c) の pre-commit はゲートにも CI にも置かない**(D-031)。フックを GitHub から clone するため
遮断下で動かない。証拠は**ホストでの `pre-commit run --all-files` の artifact** とする(AR-19)。
`name-tests-test` は `--pytest-test-first` を渡す(既定は `*_test.py` を要求し、`testpaths` と
矛盾して初日から赤になる)。

mypy は `mypy_path = ["src"]` を設定し、`ignore_missing_imports` を全体既定にせず
`[[tool.mypy.overrides]]` で型スタブの無いサードパーティにだけ限定する(AR-23)。
そうしないと `tests/` から import した製品コードの型が `Any` に落ち、strict が空振りする。

**実 CI の green はスコープ外**(要件の Out of scope、D-003)。

### NFR-6: 品質ゲートのネットワーク非依存

**実行時依存をサンドボックスイメージに焼く**(ADR-001)。`.rein/oci/quiz-sift/Containerfile` が
ffmpeg を apt で入れ、Python 依存を **`uv.lock` から導出した `requirements.txt`** で入れる。
lock を直接 COPY できないのは `rein oci build` のビルドコンテキストが Containerfile の
ディレクトリだからで、**派生物が lock と一致していることを `tests/test_gate_environment.py` が
検査する**(AR-1)。

**node・Playwright・torch・CosyVoice はイメージに入れない**(ADR-012)。R-7 と R-12 の
「実際に動く」側は `artifact` へ降ろした。**`claude` バイナリも同じ扱い**(ADR-013): node
ランタイムを要するので焼かず、ゲート内では注入した fake runner に差し替わる。

**境界は2分割する**(ADR-014)。ゲートで差し替えるのは `claude` の spawn **だけ**で、env 辞書の
構築・argv の組み立て・空 cwd の作成・最小ホームの検証・stdout のパース・失敗分類・マスクは
**実コードのままサンドボックスで走る**。1つの runner に全部を飲ませると、NFR-8 の (b) も (e) も
「fake が観測した要求」しか検査できず、実装の正しさが自己確認に落ちる。

**実 CLI の出力契約をフィクスチャで固定する**(AR-43)。`tests/fixtures/claude_cli/` に実 CLI から
採取した生の stdout を1件置き、`.meta.yaml` に採取日・`claude --version` の出力・モデル・
**採取に使った argv 全体**・合成プロンプト全文・sha256 を記録する。**2026-09-26: argv に `--safe-mode` が
加わった(D-130)ので、success / invalid_schema は `--safe-mode` 付きの設計 argv で取り直す**(D-133)。
旧 argv を2本目の期待形として受ける分岐は作らない。取り直した success に `rate_limit_event` が現れなければ、
(h) の「`isUsingOverage` 偽」の実物が失われるので、その事実を `.meta.yaml` に書く。**失敗の形も採る**(AR-63)。成功1件だけでは、新設した `FailureKind` 7種と判定順序が**同じ実装者が
書いた fake に対してしか検証されない**。採取コストがほぼゼロの形を1件足す:
**不正な `--json-schema` に対する出力**(`PARSE` の実物)。**2026-09-25 の T-029 実測(claude 2.1.282)で、
`Error: --json-schema is not valid JSON: ...` は標準出力ではなく標準エラーに出て、標準出力は空、終了コードは 1**
だった。当初この節は「JSON ですらない `Error: ...` の標準出力」と書いていたが誤り。`PARSE` の定義は
「stdout が JSON のイベント列にならない(空を含む)」で、終了コードも stderr も判定に使わない(D-119)。
stderr はマスクして `stderr_masked` に残すだけ。フィクスチャの `.meta.yaml` には終了コードと stderr を記録する。
**`error_max_turns` の実物は採らない**(D-118 / ADR-016、人間が判断)。T-029 の実測では `--max-turns 1` が
haiku でも `claude-opus-5-5` でも、stream-json でも json でも `subtype: success` / `num_turns: 2` で完了した。
StructuredOutput で完了する呼び出しはターン上限に掛からないらしい。要件作成時の「`--max-turns 1` で
`error_max_turns`」という観測は 2.1.282 では再現しない。`TRUNCATED` の判定枝(`subtype: error_max_turns` /
`stop_reason`)は R-3 のとおり残す。検証は単一ファクトリの fake で行い、その骨格は成功フィクスチャから作って
`subtype` / `is_error` / `stop_reason` / `structured_output` だけを差し替える。実物の形は **`unknown`**
(Open questions)。
**期待する `FailureKind` を明記する**(AR-77): 不正な `--json-schema`(stdout 空)→ **`PARSE`**、
ファクトリの `error_max_turns` 形 → **`TRUNCATED`**(D-102)。期待値を書かずに「それぞれが期待する
`FailureKind` へ落ちる」を証拠にすることはできない。`FailureKind` は `TRUNCATED` を含む**7種**。
ゲート内の判定は4本:
(1) 実パーサがフィクスチャを食えること、(2) fake の出力がフィクスチャと同一のキー骨格を持つこと、
(3) **fake が単一のファクトリからしか生まれないこと**(これが無いと「全 fake の集合」が列挙できず
(2) が空振りする)、(4) **negative control** — `usage.cache_read_input_tokens` を落とした fake が
(2) で赤くなること。採取は合成プロンプトで行うので NFR-2 に触れない。

実現性は 2026-09-22 に実測済み(`docs/speculative-work.md` に記録)。`--network none` で
fugashi + unidic-lite の形態素解析、ffmpeg `silencedetect` による mp3 の無音検出、
Playwright chromium の起動を個別に確認した(最後のものは結局イメージに入れない)。

### NFR-7: 処理コストと所要時間の可視化

`pipeline/meter.py` が実行ごとに JSON Lines で記録する。現行 `Meter` は `UsageRecorder` しか
満たさず、記録できるのは「成功した子プロセス数」だけなので、**`ports/metering.py` に上位 Protocol
`RunMeter` を新設する**(`ports/llm.py` は触らない):

```
class RunMeter(UsageRecorder, Protocol):
    def begin_call(self, method: str) -> None: ...       # llm_calls += 1
    def begin_invocation(self, argv_digest: str) -> InvocationScope: ...   # provider_invocations += 1(D-107)
    def end_invocation(self, model: str, turns: int | None, usage: TokenUsage | None,
                       cost_usd: float | None, is_error: bool | None,
                       skills_digest: str | None) -> None: ...   # cost_usd = total_cost_usd(D-114)
    def record_quota(self, info: RateLimitInfo) -> None: ...   # 開いている InvocationScope に属する
    def record_failure(self, kind: FailureKind) -> None: ...
```

**`RateLimitInfo` の拡張**(`ports/metering.py`)。`rate_limit_type` / `is_using_overage: bool | None` /
`overage_status` を足す。3つの `resetsAt`(トップレベル・`five_hour`・`seven_day`)は既に持つ。
**`ALLOWED_STATUS` と D-110 の判定(`status != "allowed_warning"` なら真)は削除する** — 成功フィクスチャに
`status: allowed` が出ており、この判定は `allowed` の呼び出しが `PROVIDER_ERROR` で落ちるとレート制限と誤る。

**レート枠による停止の述語**(D-128、D-135)は `ports/metering.py` の純関数
`rate_limit_stop(kind, is_error, last) -> bool` 1本に置く(Meter とログのレンダリングが同じ定義を使い、
adapters → ports の依存の向きを保つ)。真になるのは `kind == PROVIDER_ERROR`・`is_error is True`・
`last is not None`・`last.status == "rejected"`・`last.is_using_overage is False` のすべてを満たすときだけ。
`is_using_overage` が `None`(キーが無い)なら偽 — 欠けたキーを「使っていない」と読まない。
`last` は**その呼び出し自身の子プロセスが最後に出したイベント**で、Meter は失敗した子1本ごとに
`(kind, is_error, last_rate_limit)` を保持する。並行度に関係なく呼び出し単位で評価する(AR-113a)。

**レート枠は `rate_limit_event` を読むアダプタが `record_quota` で渡す**(D-114)。`Llm` Protocol を変えない
方針なので cli は `RateLimitInfo` を観測できず、meter がその実行で最初に受けた値を `quota_at_start`、最後に
受けた値を `quota_at_end` にする。届かなければ `null` とし、届かなかった呼び出しの件数を別に数える(AR-116)。
アダプタは子1本について、最初と最後の `RateLimitInfo`・届いた件数・`isUsingOverage` が1回でも真だったかを持つ。

`LlmFactory` は `Callable[[RunMeter], Llm]` になる。`Meter` が `RunMeter` を満たすので
`UsageRecorder` の口は温存され、`Llm` Protocol は不変のまま。

**2つの呼び出し回数**(AR-38)。`llm_calls` は Protocol メソッド1回、`provider_invocations` は
子プロセス1本。差が生まれる要因は**チャンク分割**だけ(D-101 により再試行しない)なので、
2語の差は常に説明可能である。

**3つの呼び出し回数**(AR-57)。`model_turns` はエンベロープの `num_turns` の総和で、**レート枠を
消費するのはこれ**。`--json-schema` の結果が `StructuredOutput` ツール経由で配送されるため、成功
した子プロセス1本の中でモデル往復が 2 回以上起きる(実測: `num_turns: 2` / `stop_reason: tool_use`)。
`provider_invocations` を「枠の消費実態」と読むと 2〜3 倍ずれる。

**`model_turns` の記録経路**(AR-75)。`UsageRecorder.record(model, usage)` は4値しか運ばないので、
**`end_invocation(model, turns, usage)` を `RunMeter` に置く** — `num_turns` と `usage` と モデル ID を
**呼び出し単位で対にして**渡す口がないと、`Σ_model overhead_model × turns_model` の判定が組めない。
`turns` が取れずに終わった呼び出し(`SPAWN` / `TIMEOUT` / `PARSE`)は `turns=None` / `usage=None` で
記録し、**件数を別に数える**(AR-65 が usage について置いた規律をターンにも通す)。

**並行度**。並行度 1 を凍結した以上、ロック付きの区間計算は作らない(AR-66)。レート枠による停止の述語は
並行度 1 に依存せず呼び出し単位で評価する(上記)。並行度は
「同時に起動した子プロセスの最大数」として定数 1 を記録する。時刻は既存の `ports/clock.py` の
`Clock.monotonic` を使う。**レート制限を失敗に倒した(D-091 撤回)ので、待機も累積待機時間も無い。**

**失敗した呼び出しのトークンも会計に入れる**(AR-65)。拒否・打ち切り・エラーでも消費したトークンは
枠を食うので、**成否の判定より先に `usage` を記録する**(現行 SDK アダプタが同じ規律を持つ)。
`result` イベントが来ずに終わった子プロセスは `usage: null` で記録し件数を別に数える。
**`Meter.record` はモデル ID が単価表に無いとき例外を投げる**(現行実装)が、CLI が返す ID が
`claude-haiku-4.5` のように一字違うだけでフル処理が計測器の例外で死ぬので、**照合失敗は
`{"status":"unavailable","reason":"unknown_model"}` へ倒し、例外にしない**。

**ログのフィールド一覧**(AR-65。要件は「項目リストは1本だけ」と定めているので、設計は散文ではなく
名前で示す)。`out/<deck>/meter.jsonl` に1実行あたり2行を追記する。

- **開始行**(`record: "start"`): `record` / `run_id` / `execution_id` / `started_at` / `cache_mode`
  (`fresh` / `resume` / `normal`)/ `stage` / `env_names` / `metered_env_names`
- **終了行**(`record: "end"`。`Meter.finish` が出す):
  - 識別: `record` / `run_id`(**列の ID**。D-124)/ `execution_id`(1回の実行の ID)
  - 時間: `started_at` / `finished_at` / `elapsed_sec`
  - 回数: `llm_calls` / `provider_invocations` / `model_turns` / `turns_by_model` / `max_concurrency`
  - トークン: `tokens`(4値の内訳ごとの総量)/ `tokens_by_model` / `calls_by_model`
  - コスト: `estimated_cost`(`notional: true` / `computed` / `declared` / `relative_diff` / `status`)/
    `disclaimer` / `prices`(出所つき、2モデル分)
  - オーバーヘッド: `overhead`(**モデル別の写像**。各要素が `per_turn_tokens` / `bucket` / `measured_at` /
    `cli_version` / `argv_digest` / `skills_digest` / `turns`。`unknown` のときは `reason` と
    `argv_digests` / `skills_digests`)
  - 失敗: `failures_by_kind`(7種)/ `failed_invocations` / `invocations_without_usage` / `invocations_without_turns`
  - レート枠による停止: `rate_limit_stopped_invocations`(述語が真の数)/ `ended_by_rate_limit`
    (`value`・その実行の最後のイベントの `status`・3つの `resetsAt`。定義は D-128)
  - レート枠: `quota_at_start` / `quota_at_end`(`status` / `rateLimitType` / `isUsingOverage` / `overageStatus` /
    `resetsAt` / `five_hour` / `seven_day` のそれぞれの `utilization` と `resetsAt`。届かなければ `null`)
  - overage: `overage_invocations` / `invocations_without_rate_limit_event` / `overage_check`
    (`failed` / `unknown` / `passed`)
  - 停止: `stop`(`exit_code` / `exit_reason`(例外クラス名)/ `kind`: `completed` / `rate_limit` / `other`)
  - 列の集約: `sequence`(その時点までの列の集約。最後の実行の終了行が最終値。D-139。中身は下の「実行の列」)

**実行ログには `material` を置かない**(D-116)。フル処理で material を独立に得る手段は無く、
「合計 − Σ overhead×turns」で出せば AR-51 が禁じた恒等式に戻る。恒等式の判定は T-030 の較正表
(既知長の合成プロンプト)でだけ行う。レート制限で終わった実行は待機しないので、待機時間の欄は置かない
(D-091 撤回)。

**notional コスト**。自算値は既存 `ModelPrice.cost`(4値 × 単価)、申告値は `total_cost_usd` の
総和、相対差は `(declared - computed) / computed`。**単価が1つでも 0 なら
`{"status":"unavailable","reason":"zero_price"}` を書き、「記録済み」と報告しない**(AR-36)。
キー名は `estimated_cost` のまま残し(D-113。改名すると done の T-002 の凍結基準が偽になる)、
その中に `"notional": true` を、同じ行に免責の1行を書く。**実課金額を主張する語(`billed` / `charged` / `実課金`)がログとレポートに現れないことを
機械判定する**。単価表は 2 モデル分(ADR-015)で、`priced_at` に加えて**取得元**を持つ。

> **注記(Risks へも載せる)**: `total_cost_usd` はサブスクリプション実行でも返るが、これは CLI が
> 定価表から計算した名目値である可能性が高い。その場合「自算値 vs 申告値」の相対差は**単価表の
> 一致度を測っているだけ**で、課金の裏取りにはならない。AR-24 / AR-36 が閉じたかったのは
> 「常に 0.0 を返す実装」なのでその目的は達するが、**2値の独立性は見かけほど高くない**。

**固定オーバーヘッドの分離**(AR-41 / AR-51 / AR-76)。定数は**モデルごと**に
`OverheadConstant(model, per_turn_tokens, bucket, measured_at, cli_version, argv_digest, skills_digest)` として持ち、
判定は **`Σ_model (overhead_model × turns_model) + material = 報告された入力トークン合計`**
(material が独立に分かる較正表の上でだけ判定する。D-116)。
ADR-015 の混成でモデルごとに値が違うので、単一の `overhead × num_turns` では真になりえない。
ログの `overhead` フィールドも `tokens_by_model` / `prices` と同じく**モデル別の写像**にする。
**切片は「1子プロセスあたり」であって「1ターンあたり」ではない**(AR-76)ので、`total(k) = c + k·m`
の回帰で得た `c` を実測の `num_turns` で割って `per_turn_tokens` を出し、**その割り算を記録に残す**
(同一 argv では `num_turns` が動かないので、per-turn 値はこの割り算でしか分離できない)。
**`argv_digest` が AR-51 への直接の答え** — ADR-013 が定めた argv を変えると定数が自動的に無効に
なり、実行時に現 argv のダイジェストと突き合わせて違えば `overhead` を `unknown` として報告し
「記録済み」と言わない。`--safe-mode` の追加(D-130)でダイジェストは変わるが、`OVERHEAD` は空なので失う定数は無く、
T-030 は `--safe-mode` 付きの argv で較正する。
**`skills_digest`**(D-126、D-141): アカウント同期の skills 一覧はシステムプロンプト長を動かしうるので、
`init` イベントの `skills` の名前を並べ替えた JSON 配列の sha256 を出所に加える(キーが無ければ固定値
`"absent"` のダイジェスト)。アダプタが `end_invocation(..., skills_digest=...)` で渡し、Meter はモデルごとに
観測値の集合が `{constant.skills_digest}` と一致しなければ `status: unknown`・`reason: skills_mismatch` とする
(argv を先に比べる)。較正表にも `skills_digest` の列を足す。

測定は**同一ブロックの k 回反復**(k = 1, 2, **3**)で `total(k) = overhead + k·m` を取る。
要件は2本(短・長)を求めているが、**2点だと切片と傾きが一意に解けてしまい「差が材料の差と一致
する」半分が恒等式になる**(AR-51 の恒等式問題が形を変えて残る)。3点目を足すと線形性そのものが
反証可能になる。実測は**ホスト(`artifact`)**で `docs/test/measurements/overhead-<model>.md` に
数値表として残し、**ゲート(`command`)**はその表から切片を再計算して定数と突き合わせる —
これでネットワーク無しに機械判定が成立する。

**レート枠の記録**。`stream-json` の `rate_limit_event` が `rate_limit_info` として `status` /
`rateLimitType` / `utilization` / `resetsAt` / `isUsingOverage` / `overageStatus` と、`unifiedWindows` の
`five_hour` / `seven_day` を返す。**各実行で最初と最後に届いたイベント**の値を記録し(届かなければ `null` と
件数)、列の集約では実行ごとの `{execution_id, at_start, at_end}` を並べる(AR-116、AR-121)。

#### 実行の列(ADR-018、D-124・D-128・D-129・D-132・D-136〜D-139・D-142〜D-145)

- **口**(`cli.py`、**LLM を呼びうる段のサブコマンド** = cluster / recall / glossary / script / audio に共通):
  `--fresh-run` は新しい `run_id` を採番して段キャッシュを迂回して始める。`--resume RUN_ID` は既存の列に実行を
  1つ足す。**`--resume` できるのは列の `status` が `resumable` のときだけ**で、開始行の無い `run_id`・`completed`・
  `not_completable` の列は即座にエラー終了する(AR-133。`completed` の列に足すと最後の exit 0 が途中の実行に
  なって判定が反転し、追記だけのログでは戻せない)。無指定は通常のキャッシュを使う1実行だけの列
  (`cache_mode: normal`)で、フル処理の列には数えない(NFR-4 の「前段の呼び出し 0」を壊さないため)。
  `run_id` は実行開始時に stderr へ表示する。ingest / review / serve は LLM を呼ばないので、起動前検査も列への
  登録もしない(D-115 の「LLM を使う段の前」に揃える。D-143)。
- **R-3 の再現性測定**(D-142): キャッシュ迂回の2回は、`--fresh-run cluster` の**1実行だけで exit 0 まで走った列**
  を2本使う。再開した列は測定に使わない(列の和はやり直しの分を含み、「1回分の2倍」の比較が成り立たない。
  AR-124)。枠で止まったら、その列は捨てて新しい `--fresh-run` で取り直す。
- **順序**: 起動前検査(NFR-8 (b)。`claude` の解決 → flock → 中身検査)→ 開始行の書き込み → 段の実行 →
  `finally` で終了行。**検査に落ちた起動は列に登録しない**(D-132)。列の最初の `--fresh-run` が検査に落ちたとき
  は `run_id` が生まれていないので、人は最小ホームを直して `--fresh-run` からやり直す(失うものは無い)。
  開始行・終了行とも1回の `write` で書き `flush` + `os.fsync` する。`Meter` は開始行の時点で作る(現行
  `cli.Pipeline` は最初の LLM 呼び出しで遅れて作るので、キャッシュだけで終わる実行が1行も書かない。これを直す)。
- **起動前検査の差し替え口**(D-143、AR-126): `cli.main(argv, *, llm_factory=build_default_llm,
  preflight=default_preflight)`。`default_preflight` は `adapters/claude_process.py` のモジュール関数で、呼ばれた
  ときに `Workspace` を構築して `preflight()` を走らせる(既定値は関数オブジェクトなので import 時には何も走らない。
  AR-73)。ゲート内の cli テストは `llm_factory` と同じく `preflight` にも fake を渡す(テストコードからの直接構築 =
  (c) が認める opt-in と同じ経路)。**製品の入口がこの既定値のまま呼ぶこと**は、`inspect.signature(cli.main)` の
  2つの既定値が `composition.build_default_llm` と `claude_process.default_preflight` そのものであることと、
  (d) の入口集合が `main(argv)` 以外の引数を渡さないことを AST で判定して守る。検査の中身は `Workspace` を
  `tmp_path` で直接走らせるテストが判定する(差し替えない。AR-72)。
- **迂回と再開**(`pipeline/stage.py`、D-137 改訂、AR-125・AR-131): `--fresh-run` / `--resume` の実行は、
  **`inputs_digest` の構成要素に `run_id` を加える**。これで列ごとに成果物の名前が分かれ、(1) その列が作った
  成果物にだけヒットする(実行中に自分が作った段にも即座にヒットする)、(2) 列より前の古い成果物にも他の列の
  成果物にもヒットしない、(3) 別の列が同じ入力で上書きすることがない(再現性の2つの割当が同時に残る)。
  許可集合も `produced_stages` も要らない。通常の実行(`normal`)の digest は変えない。キャッシュの削除はしない
  (教材本文を CLI のセッション記録にもう一度平文で残すため。D-086)。列ごとの成果物は人が `out/<deck>/` を
  整理するまで残る。
- **段の中での早期停止**(D-144、AR-127): 呼び出しの結果に `isUsingOverage` 真、または自分の子の
  `rate_limit_event` の `status: rejected` が現れたら、**その段の残りの呼び出しを発行せず**に集計して非 0 終了する。
  overage が始まった後の呼び出しはすべて従量課金になり、`rejected` の後の呼び出しは D-129 により再開時に
  捨てられるので、どちらも続ける利得が無い。失敗件数は実際に発行した呼び出しについて数える(R-3 の
  「失敗件数が実際の失敗数と一致」は保たれる)。それ以外の失敗種別は現行どおり段の呼び出しを終えてから止める。
- **集約**(`pipeline/run_sequence.py`、純関数。入力は1つの `run_id` の開始行と終了行):
  - 和: `llm_calls` / `provider_invocations` / `model_turns` / `turns_by_model` / `tokens` / `tokens_by_model` /
    `invocations_without_usage` / `invocations_without_turns` / `failures_by_kind` / `overage_invocations` /
    `invocations_without_rate_limit_event`
  - `estimated_cost`: `computed` と `declared` はそれぞれ和で、`relative_diff` は和から計算し直す。
    `status: recorded` はすべての実行が `recorded` のときだけ。`notional: true` と `disclaimer` を付ける。
  - 最大: `max_concurrency`
  - 時間: `elapsed_sec_sum`(各実行の単調時計の経過時間の和)と `wall_clock_sec`(最初の開始行の `started_at` から
    最後の終了行の `finished_at` まで)
  - レート枠: `quota`(実行ごとの `{execution_id, at_start, at_end}`)/ `quota_missing`(`{at_start: n, at_end: n}`)
  - `overage_check`(和から同じ規則で判定)/ `metered_env_names`(実行ごとの和集合)
  - `status`(D-145、AR-123・AR-133): 次の3つのどれか1つと `not_completable_reasons`。
    - `not_completable`: 終了行の無い開始行(いま締めている実行を除く)、途中の実行の「exit 0 でもレート枠による
      停止でもない」停止、`overage_invocations > 0`、`metered_env_names` が空でない実行、最初の実行が `fresh`
      でない、のいずれか。
    - `completed`: `not_completable` でなく、**最後の実行の `stage` が最終段 `audio`** で exit 0。
      途中の実行は exit 0(段ごとに進めた場合)かレート枠による停止(失敗した呼び出しが1件以上あり、そのすべてで
      述語が真)のどちらかであればよい(要件が禁じるのは途中の**非 0** 終了のうちレート枠以外のもの)。
      最終段まで走っていない列を `completed` にしない。
    - `resumable`: 上のどちらでもない(最後の実行がレート枠で止まった、または最終段以外で exit 0)。
  - **合算はやり直しの分も数える**: D-129 により段の途中で止まった段は再開時に最初から呼び直すので、列の
    `llm_calls` は止まらずに走った1回分より多くなる。要件が「和」を課しているので重複は除かない。
  - 集約は対象 `run_id` の行が既知の形でなければ例外で止まる。旧形式の行を読む分岐は作らない。
  - **読めない行**(AR-132): JSON として読めない行(電源断で途中まで書かれた行)は、**その列の最初の開始行より
    後ろ**にあれば、その列を `not_completable`(理由 `unreadable_line:<行番号>`)にする。前にあれば、その列の行では
    ありえないので件数 `unreadable_lines_before` だけ数える。どちらでも例外にせず、終了行の書き込みを止めない。
    書き手は追記の前にファイル末尾が改行でなければ改行を1つ書き、途中の行と次の行を連結させない。

**蓄積する情報の設計**(`meter.jsonl`)。

1. **情報が失われない前提条件**
   - [ ] 開始行を最初の LLM 呼び出しより前に fsync まで済ませて書く。
   - [ ] 終了行を `finally` で書く。12 時間の上限は in-process の `signal.alarm` で掛ける(D-138)。
   - [ ] 追記だけで、書き換え・切り詰め・自動削除をしない。
   - [ ] `--resume` は `resumable` でない列(存在しない `run_id` を含む)を拒否する。
   - [ ] 途中まで書かれた行は後続の行と連結させず、該当しうる列を `not_completable` にする(黙って読み飛ばさない)。
   - [ ] 残る失われ方: SIGKILL・電源断では終了行が残らない。これは `not_completable` として数えられ、黙っては消えない。
2. **ライフサイクル**: 開始行(開く)→ 終了行(閉じる)→ 最後の終了行の `sequence`(列の最終値)→ **昇格**:
   T-020 が完走した(または完走しなかった)列の集約と実行ごとの行を `docs/test/measurements/full-run.md` へ写して
   コミットする(本文を含まないので NFR-2 に触れない)→ **アーカイブ**: `rein cycle-close` で docs 側が
   `docs/archive/` へ。`out/<deck>/meter.jsonl` の生の行は人が `out/<deck>/` を消すまで残る。自動ローテーション
   は作らない(1実行2行・数 KB)。
3. **保持層と更新サイクル**: 作業層 = `out/<deck>/meter.jsonl`(ホストごと・gitignored・1実行ごとに2行追記)/
   証拠層 = `docs/test/measurements/full-run.md`・`reproducibility.md`(コミット。T-020 の列が閉じるたびに1回)/
   保管層 = `docs/archive/`(サイクルを閉じるときに1回)。

`meter.jsonl` は `out/` 配下なので `Pipeline.__init__` の `ensure_ignored` が既に守る。中身は件数・ID・
ダイジェスト・環境変数**名**・レート枠の値だけで、教材本文を含まない(逐語ログ `InvocationRecord` とは別ファイル)。

### NFR-8: 外部サービスの利用が従量課金を伴わないこと

ADR-013 が経路を決め、ADR-014 が境界を決めた。ADR-017 が最小ホームの中身検査と `--safe-mode` を、ADR-018 が実行の列と overage を決めた。ここは **(a)〜(h) をどう機械判定するか**だけを書く。

**モジュール構成**(新規):

| ファイル | 役割 |
|---|---|
| `composition.py` | **(c) の唯一の選択点**。`build_default_llm()` 1本だけを置く |
| `ports/process.py` | `CommandSpec` / `CommandResult` / `CommandRunner` Protocol |
| `ports/metering.py` | `RunMeter` Protocol |
| `adapters/llm_prompts.py` | system プロンプトと JSON Schema。**SDK に触らない** |
| `adapters/llm_contract.py` | 応答の純粋パーサ(ID 完全性検査を含む)。**既存 `llm_anthropic.py` の `_proposals` / `_string` / `_schema` をここへ移して共有する**(AR-67。再実装しない) |
| `adapters/llm_claude_cli.py` | `ClaudeCliLlm`。argv 組み立て・stdin・失敗分類 |
| `adapters/claude_process.py` | `SubprocessRunner`(実 spawn)と `Workspace`(空 cwd / 最小ホーム) |
| `adapters/claude_failure.py` | `FailureKind` 7種と判定順序(レート制限と overage は `PROVIDER_ERROR`) |
| `safety/invocation_log.py` | 逐語ログの構造化レコードとマスク |

**前提作業(これが無いと (a) と (d) が確定的に赤)**: `src/quiz_sift/cli.py` の
`from quiz_sift.adapters import llm_anthropic` を**削除する**。`llm_anthropic.py` はモジュール
先頭に `import anthropic` を持つので、CLI を import した時点で `sys.modules` に `anthropic` が
入る。cli.py がこれを import しているのは `inputs_digest` のためにプロンプト定数とモデル名を
読んでいるからなので、それらを `adapters/llm_prompts.py` へ移す。あわせて `pyproject.toml` に
`[project.scripts]` を**1本だけ**追加する(現在 0 本。(d) は「1つだけ」を判定するので 0 は不可)。
**入口は `[project.scripts]` だけではない**(AR-69)。`cli.py` は `if __name__ == "__main__"` を持ち、
docstring が `python -m quiz_sift.cli ...` を製品の使い方として書いている。(d) の走査対象は
**`__main__` ガードを持つモジュールを含む「到達可能な入口の集合」全体**とする — コンソール
スクリプトの本数だけを数えると、`python -m` 経由の第2の入口が素通りする。

**(a) の判定**: **新規インタプリタ(サブプロセス)**で走らせる(AR-50。`sys.modules` はプロセス
全域なので、(c) が認める「テストコードからの直接構築」のテストと同一プロセスで判定すると順序依存で
赤になる)。`anthropic` の import 可能性は **`sys.path` にスタブパッケージを置く**ことで満たす
(AR-49。実 SDK はイメージに無く、判定に要るのは「読み込まれたか」だけ)。

**`build_default_llm` の形**(AR-55。設計が判定だけ書いて対象を書いていなかった):

```
# src/quiz_sift/composition.py — このモジュールにはこの関数以外を置かない
def build_default_llm(meter: RunMeter) -> Llm:
    return ClaudeCliLlm(meter)
```

`CommandRunner` と `Workspace` は **`ClaudeCliLlm.__init__` の本体で構築する**(キーワード既定値には
**しない**)。既定値にすると `SubprocessRunner()` / `Workspace()` が **import 時に1回だけ評価され**、
AR-64 のプロセス間ロック取得や最小ホームの検証が `composition.py` の import で走ってしまい、
(c) の AST テスト自体がサンドボックスの import 時に死ぬ(AR-73)。

**`__init__` にも (c) と同じ AST 制約を掛ける**(AR-74)。既定式や本体は (c) の検査の死角で、
`runner = SubprocessRunner() if os.environ.get("QS_DIRECT") else Other()` と書けば全検査を素通りする —
D-099 と (c) が `build_default_llm` から追い出した分岐と環境変数参照が、検査の無い場所へ移るだけになる。
`ClaudeCliLlm.__init__` の本体は**属性代入と引数なしのクラス呼び出しだけ**とし、`if` / `match` /
`try` / 添字 / `os.environ` / ファイル読み出し / `importlib` を含まないことを AST で判定する。

**`Workspace` はゲートでも差し替えない**(AR-72)。差し替えると (b) の「製品が `HOME` 配下に作成した
ファイルが 0 件」が fake の指す一時ディレクトリに対する判定になり、**構造上あたりまえに 0 件**になる
(AR-55 が (a) について指摘したのと同型の「赤くなり得ない緑」)。代わりに `Workspace` は
**根のパスを引数に取る実クラス**(`Workspace(home_root, cwd_root)`)とし、ゲート内テストは
`tmp_path` を根として**実物を走らせる**。ADR-014 が「ゲートで差し替えるのは `claude` の spawn だけ」と
書いた境界はこれで保たれる。最小ホームの実体が無いサンドボックスでは、テストが最小ホームの形を
**T-029 の実測形**(`.claude.json`、`.claude/settings.json` = `{"theme":"dark"}`、`skills/synced/…`、
`plugins/synced/…`、`projects/…/*.jsonl`、資格情報)で自分で作って与える。`new_cwd()` が作った一時ディレクトリは**呼び出しごとに
後始末する**(500〜1000 回ぶんの残骸を残さない)。

ゲート内の fake runner 注入は `composition` ではなく**テストコードからの直接構築**で行う。

**(b) の起動前検査**(ADR-017、D-122、D-132)。`Workspace.preflight()` が次の順に行い、1つでも当たれば原因の
相対パスと規則番号を載せた `SystemExit` の派生で終了する。**検査は読むだけで、mkdir・書き込み・修復をしない**
(キーを削って書き直すのは製品が `HOME` 配下に書くことになり、黙って直すことにもなる)。

1. 子の `PATH` で `claude` を解決する(`shutil.which`)。
2. `home_root` がディレクトリであることを確かめ、その fd に `fcntl.flock`(D-105)。
3. ロックを持ったまま中身を検査する:
   - i. `.claude/CLAUDE.md` が在る(`lstat`。symlink・ディレクトリも拒否)。
   - ii. `.claude/rules/` 配下に、再帰的に見てファイル(symlink を含む)が1つでもある。空ディレクトリは通す。
   - iii. `.claude/settings.json` が在り、UTF-8 の JSON として読めない(`JSONDecodeError`・`UnicodeDecodeError`
     とも拒否)/ トップレベルが object でない / キー集合が `{"theme"}` の部分集合でない(`{}` は通す)。
   - iv. `.claude/.credentials.json` が無い。**`os.path.lexists` だけで判定し、開かない**(NFR-3)。

CLI 自身が書くもの(`.claude.json`・`settings.json`・`skills/synced/`・`plugins/synced/`・`projects/`・
`backups/`・`cache/`)は子プロセスの振る舞いであって製品の作成物ではない。ゲートでは fake runner が何も
書かないので、`run()` の前後のホーム配下のファイル集合の差分がそのまま製品の作成物になる(ADR-014 の方式のまま)。

ゲート内の `command` 判定は、`tmp_path` を根にした実物の `Workspace` と spawn を数える fake runner で置く:
- **positive control**: 上の実測形が通り spawn が 1 回になる(何でも拒否する実装を赤にする)。
- **拒否側**(それぞれ spawn 0 回と非 0 終了): `.claude/CLAUDE.md` / `rules/a.md` / `rules/sub/b.md` /
  settings に `env`・`apiKeyHelper`・`hooks`・`permissions`・`enabledPlugins` / 壊れた JSON / `[]` / 資格情報なし。
- **NFR-3 の control**: 資格情報ファイルを `chmod 000` にしても検査が通る(開いていれば `PermissionError` で落ちる)。
- 許可キー集合 `{"theme"}` と拒否パスは要件から**テスト側にリテラルで書き写す**(製品の定数を読まない。AR-30)。
- 検査の後、ホーム配下の作成ファイル差分が 0 件。

**(a) の control は2本要る**(AR-55)。
- **positive control**: 判定に使う実行で `build_default_llm()` が実際に呼ばれ `llm_calls ≥ 1` である
  ことを同時に要求する。`Llm` に一度も触れないサブコマンド(取り込みなど)を走らせるだけでは
  無条件に緑になるため。判定対象のコマンドを固定して記す。
- **negative control**: 選択点を SDK アダプタへ差し替えると (a) が赤くなること。**ただし赤の出所に
  注意が要る** — `llm_anthropic.py` はモジュール先頭に `import anthropic` を持つので、差し替えは
  import だけで赤くする。control が測るべきは「パイプラインが実際に SDK を使ったこと」なので、
  **差し替え版でも positive control(`llm_calls ≥ 1`)が緑であることを同時に要求する**。
- **`AnthropicLlm` のコンストラクタを `(meter)` 1引数に揃える**。現行は
  `(client, recorder, *, model)` で、現行の既定実装が `anthropic.Anthropic()` を入れ子で作っている
  (D-099 と (c) がどちらも禁じる形)。揃えないと **(c) の形を保ったまま SDK へ差し替えることが
  できず、要件が要求する negative control が書けない**(AR-55)。クライアントの構築は
  `__init__` の本体へ移す。

**(c) の AST 判定**: `ast.parse` で `build_default_llm` を取り、(1) 本体が `Return` 1文、
(2) `value` が `ast.Call` で `func` が `ast.Name`(`ast.Attribute` は落とす)、(3) `ast.walk` に
`If` / `Match` / `Try` / `Subscript` / `IfExp` / `BoolOp` / `Lambda` / `Attribute` / `comprehension`
/ `Import` が無い、(4) 名前参照が `{クラス名} ∪ 引数名` に含まれる、(5) その名前が実際にクラスで
あることを `inspect.isclass` で確かめる(関数へのすり替えを塞ぐ)。**入れ子のクラス呼び出しは
禁止**する(D-099)ので、`llm_calls` の計数はデコレータではなくアダプタ内部に置く。
加えて「`Llm` を実装するクラスを構築している式が `src/` 全体で `composition.py` の1箇所だけ」を
AST で走査する(これが無いと「選択点は1つ」が `composition.py` の中しか見ない自己完結になる)。

**(e) の argv**(2026-09-25 実測で確定):

```
claude -p --output-format stream-json --verbose --max-turns 3
  --json-schema <インライン JSON>          ← ファイルパスは受け付けない(実測)
  --model <claude-opus-5-5 | claude-haiku-4-5>
  --system-prompt <タスク別。教材本文を含まない>
  --exclude-dynamic-system-prompt-sections
  --strict-mcp-config --mcp-config {"mcpServers":{}}
  --settings {"permissions":{"deny":[<全ツール名>]}}
  --permission-mode default
  --safe-mode                              ← D-130。CLAUDE.md 系・ユーザメモリを読まない
```

**argv 全体を1回通して受け付けられることを確認済み**(2026-09-25。`--exclude-dynamic-system-prompt-sections`・
`--strict-mcp-config`・`--mcp-config` のインライン JSON・`--settings` のインライン JSON・
`--permission-mode` のすべて)。**`--safe-mode` 付きの argv は 2026-09-25 の番兵実測で haiku だけ通した**
(OAuth 可・`init.tools` = `["StructuredOutput"]`・`memory_paths: null`)。`claude-opus-5-5` との組み合わせは
**未確認**で、契約フィクスチャの取り直し(D-133)で確かめる。

**(e)5 の証拠**(D-130)。番兵による「CLAUDE.md 系の読み込み 0 件」はホスト実測の `artifact`。
home-probe の実測スクリプトはリポジトリに無いので、採取経路を `tests/measurements/collect_claude_cli.py` に足し、
判定テスト(`tests/measurements/test_claude_cli_home_probe_logs.py`)を置く。**positive control を必須にする**:
同じ番兵に対して `--safe-mode` の**無い** argv で番兵が読まれること(無いと「0 件」は番兵が効いていないだけでも
成立する)。番兵の後始末は元のバイト列の sha256 一致で確かめる。ゲート内の argv の形の判定((e)・(g))は
要件から書き写した**リテラル**に `--safe-mode` を足す(`tests/measurements/test_claude_cli_logs.py` の
`BARE_FLAGS` も同じ集合に揃える)。

**(h) overage**(ADR-018、D-125・D-134・D-140)。実行ごとに `overage_invocations`(`isUsingOverage` が1回でも
真だった子の数)と `invocations_without_rate_limit_event`(イベントが1件も届かなかった子の数。SPAWN・TIMEOUT・
PARSE の子も含む)を数え、`overage_check` を `overage_invocations ≥ 1` なら `failed`、そうでなく
`invocations_without_rate_limit_event ≥ 1` なら `unknown`、どちらでもなければ `passed` とする。列の集約も
同じ規則で和から判定する。ゲート内の判定: 実物フィクスチャ(`isUsingOverage: false`)で `passed`、骨格の値だけを
`true` に差し替えた単一ファクトリの fake で `PROVIDER_ERROR`・非 0 終了・`failed`、`rate_limit_event` を落とした
fake で `unknown`(`unknown` を `passed` に潰す実装を赤にする negative control)。

- **ツールは `StructuredOutput` の1本まで削れる**(実測)。`--allowedTools ''` は無効果だが、
  `--settings` の `permissions.deny` に**全ツール名を列挙する**と `init.tools` が
  `["StructuredOutput"]` だけになり、それでも呼び出しは成功する。`StructuredOutput` は
  `--json-schema` の結果を配送する機構そのものなので、脅威モデル上は「0 件」と等価。
  **素の状態には `Glob` と `Grep`(どちらもファイルシステムを読む)が含まれる**ので、deny の列挙から
  漏らせない。有効ツールの集合は他フラグの有無でも動く(実測で 19 / 22 / 1)ため、**列挙の正しさを
  列挙の側から保証してはならない** — 判定は `init.tools` が `{"StructuredOutput"}` と**一致**すること。
- **判定の時点**(AR-61): `init` はターン 0 の宣言であり、脅威(第三者テキストによる注入)が働くのは
  ターン 1 以降。`tools` が1本だけなら t>0 で増える余地は無いが、**`stream-json` の全イベントを
  通して、許容集合外のツール**使用**が現れたら失敗**にする判定も置く(時間軸に沿った判定)。
- 権限バイパスの不在は否定判定(`--dangerously-skip-permissions` が argv に無く、
  `--permission-mode` が `bypassPermissions` / `acceptEdits` でない)。
- 作業ディレクトリは `Workspace.new_cwd()` が毎回 `mkdtemp` で作る**空ディレクトリ**。リポジトリ内には
  作らない。
- プロンプト本体は **stdin だけ**(`subprocess.run(input=...)` はパイプであって一時ファイルではない)。

**NFR-2 のマスク**(AR-42)。**全文を後からマスクする実装にしない**。`InvocationRecord` に
`argv` / `exit_code` / `duration_sec` / `usage`(4値)/ `failure_kind` / `result_is_error` /
`rate_limit_first` / `rate_limit_last` / `rate_limit_event_count` / `overage_seen` / `stdin_sha256` / `stdin_len` / `stdout_masked` / `stderr_masked` を
**別々のフィールドとして**持ち、ログはそのレンダリングとして生成する。こうすると「全面
`[MASKED]` に置換する実装」が**型として表現できなくなる**。マスク自体は既存の
`safety/log_filter.MaterialTextFilter.mask` をそのまま使い、`safety/secret_mask.py` に
OAuth トークンの形と `.credentials.json` のパス文字列を足す。

**並行度は 1(直列)で凍結する**(D-100)。500〜1000 回 × 3.4〜4.2 秒 = 28〜70 分で、1回の実行の
上限 12 時間に収まる。並列化してもレート枠は増えないので、**枠が binding なら利得はゼロで枠に速く
当たるだけ**。加えて並列化は最小ホームの共有による `.claude.json` 同時書き込みを持ち込む。
`MAX_CONCURRENCY` は1箇所の定数にしておき、T-020 で枠に余裕があると分かった次サイクルで上げる。

**並行度 1 は1実行の中でしか効かない**(AR-64)。最小ホームは `~/.quiz-sift-claude-home` という
**固定パスのプロセス間共有資源**なので、R-3 が要求する「キャッシュを迂回した2回の実行」を並行に
走らせたり、人が別シェルで別の段を起動したりすれば、2つの `claude` が同じ `.claude.json` を
同時に書く。**(b) の判定そのものも壊れる** — fake runner の差分観測が、別プロセスの書き込みを
「製品が作ったファイル」として数える。**最小ホームの使用をプロセス間ロックで直列化し、取得できなければ
起動時にエラー終了する**。ロックは**最小ホームのディレクトリ自身の fd に `fcntl.flock` を掛ける**形にし、
ロックファイルを作らない(D-105)。`HOME` 配下にロックファイルを置くと、(b) の「製品が `HOME` 配下に
作成したファイルが 0 件」を製品自身が破る。

- **証拠種別**: (a)〜(e) と (g) は `command`。**`init.tools` が実際に許容集合に収まることの確認は
  `artifact`**(ゲートでは argv の形しか見えず、AR-43 と同じ自己確認の穴がここにも開くため、
  ホスト実測を1本置いて塞ぐ)。(e)5 の読み込み 0 件は番兵のホスト実測の `artifact`。(f) は T-020 の実測ログに
対する `artifact`。(h) は実物フィクスチャと fake に対する `command`。
- **What this requires of a person**: ADR-014 の最小ホームを1回セットアップし、ADR-017 の中身の条件
  (`settings.json` のキーを `{"theme"}` に保つ、`.claude/CLAUDE.md` と `rules/` を置かない)を保つこと。
  (h) が `unknown` のとき課金画面を目視するかを判断すること。
  **サンドボックスイメージの再ビルドは本サイクルでは不要**(D-103。`anthropic` は通常依存のまま残し、
  extra 化は次サイクルに回す)。
- **Reversibility**: **可逆**。

### 旧タスク ID との対応(2026-09-26 の /tasks)

この節より上の本文と decisions の rationale は、旧サイクルのタスク ID で作業を指していることがある。今サイクルの対応は次のとおり(AR-154)。

| 旧 | 新 | 内容 |
|---|---|---|
| T-024 | T-039 | 子プロセス境界と起動前検査 |
| T-025 | T-040 | プロンプト・スキーマ・パーサの共有化 |
| T-026 | T-042 | 失敗分類・overage 走査・逐語ログ・fake ファクトリ |
| T-027 | T-043 | ClaudeCliLlm |
| T-028 | T-044 と T-045 | 既定 LLM の切り替え / 実行の列の CLI |
| T-029 | T-041 | 契約フィクスチャ(--safe-mode で取り直し)と番兵実測 |
| T-030 | T-046 | オーバーヘッド定数の実測 |
| T-020 | T-047 | フル処理(不可逆) |
| T-031(done)の手直し | T-037、T-038 | 計測の口の修正 / 実行の列の集約 |
| (新規) | T-032〜T-036 | 実装済みの claim を受入テストに固定する absorb(D-121、D-147) |

## Risks / trade-offs

人間が判断したトレードオフを、判断の内容とともに残す。

- **R-7 と R-12 の「実際に動く」側が `artifact` に降りた**(ADR-012)。ゲートでは純粋関数と
  API 契約テストしか走らないので、**音声生成と UI には毎回の回帰検知が無い**。実装を壊しても
  ゲートは赤くならず、`/verify` の実測で初めて分かる。代償は ADR-006 により小さく抑えられている
  (判定されるロジックは全部 Python 側にあり、UI に残るのは表示と送信、音声側に残るのは TTS 呼び出し)。
  artifact 判定は計5本(R-2 のゴールデンセット、R-3 の再現性、R-7 の無音、R-12 のブラウザ動作、
  NFR-2 の gitignore 等価性)。**実行主体は「人が環境を用意し、ループが実測する」**(N-2)。
- **NFR-1 の検証が手書きフィクスチャに限定されている**(D-043)。判定器のパースとしきい値ロジックは
  測れるが、**モデルの判定能力は測っていない**。加えて検出率に合格ラインが無い(D-044)ので、
  検出率が低くても「矛盾件数 0」は成立する。この穴は人間が承知のうえで残した。
- **退化ガードは合成フィクスチャに対してしか課されない**。実データの退化は閾値ではなく警告として
  出す(AR-5 の処置)が、**読むのは人間**であって機械ではない。D-004 を変えずに書ける範囲の対策。
- **`smoke` が既知の赤として凍結されている**(AR-20)。CLI は `src/` に住み、`src/` は `guard.paths`
  にあるので承認まで書けない。凍結されたステップで落ちたタスクは実装者に送り返されず停止するが、
  `required: false` なので DoD はブロックしない。
- **案C は要件11本 + NFR7本で、受入基準は60本を超える**。`/tasks` の分解次第では現実的な規模に
  収まらない可能性がある。
- **中心仮説は本サイクルでは検証されない**(D-002)。R-8 が測るのは記憶の定着であって転移ではない。
- **CosyVoice の日本語品質は Sarashina2.2-TTS に劣る可能性がある**。ライセンス(Apache-2.0 対 非商用)を
  優先した。実際に聞いて不満なら `ports/tts.py` 越しに差し替える(ADR-004)。
- **Udemy への実アクセスは不可逆**。CDP 接続でパスワードの取り扱いは消えたが、アクセス履歴と
  ボット検知のリスクは残る。
- **`total_cost_usd` と自算値の独立性は見かけほど高くない**(ADR-013 / NFR-7)。申告値は CLI が
  定価表から計算した名目値である可能性が高く、その場合「自算値 vs 申告値」の相対差は**単価表の
  一致度を測っているだけ**で、課金の裏取りにはならない。AR-24 / AR-36 が閉じたかったのは
  「常に 0.0 を返す実装」なのでその目的は達する。
- **`claude` CLI のバージョンが凍結できない**。ADR-001 は依存をイメージに焼くと決めたが、CLI は
  ホストに置かれ自動更新される。`--json-schema`・`usage` の4値・`rate_limit_event` の形はいずれも
  2.1.281〜2.1.282 での実測に依拠しており、**更新で壊れてもゲートは緑のまま**(ゲートは fake runner しか
  見ない)。契約フィクスチャ(NFR-6)が唯一の検知手段で、それも採取し直すまでは古い契約を守る。
- **NFR-8 は否定的証拠にとどまる**(D-087)。`apiKeySource` は D-093 により本番経路で観測できる
  ようになったが、**要件は毎回の検証を課していない**。hooks・ユーザメモリ・システム管理設定から
  指示や権限が入る経路は製品コードでは塞げず、criterion の「この基準が測っていないこと」へ落とした。
- **有効ツールは `StructuredOutput` の1本までしか削れない**(実測)。それはファイルシステムにも
  ネットワークにも触れないので脅威モデル上は 0 件と等価だが、**CLI の更新で deny の列挙から漏れる
  新ツールが増えれば黙って有効になる**。ゲートは argv しか見ないのでこれを検知できず、ホスト実測の
  `init.tools`(NFR-8 (e)1 の `artifact`)を取り直したときに初めて分かる。
- **CLI の更新が最小ホームに新しいキーを書くと、起動前検査が止まる**(ADR-017)。許可集合 `{"theme"}` は
  T-029 時点の実測形で、広げるのは要件の改訂(`/revise`)。止まるのは安全側だが、T-020 の列の途中で起きると
  人の判断が要る(検査に落ちた起動は列に登録しないので、列そのものは壊れない。D-132)。
- **skills ダイジェストは名前しか見ない**(D-141)。`init` が運ぶのは skill の名前だけなので、名前が同じまま
  説明文が変わる同期はオーバーヘッド定数の無効化に現れない。
- **週次のレート枠が 2026-09-25 時点で 95%**(実測)。D-085 は「枠を実測せず凍結する」と決めたが、
  `rate_limit_event` により**枠を使い切らずに使用率が読める**ことが分かった。T-020 は
  7日枠がリセットされる 2026-09-28 08:00 JST 以降でなければ完走しない見込み。

## Open questions

- **NFR-1 の判定能力が未測定のまま mandate を通る**。手書きフィクスチャは判定器のロジックを守るが、
  モデルが実際に矛盾を見つけられるかは本サイクルでは誰も測らない。誤学習は本人が気づけない種類の
  失敗なので、次サイクルで実測を入れるかは残る課題。
- **CosyVoice のホストへの導入手順が未定**。pip では入らず(実測)、git clone + サブモジュール +
  torch が要る。`/tasks` で手順を決める。
- **`web/` の依存管理が未設計**。node と Playwright をイメージから外したので、`npm ci` と
  `next build` はホスト側の手順になる。`/tasks` で決める。
- **`L-DES-ADR` / `L-DES-FAILURE-WALK` は `proposed` のまま、2026-09-25 のラウンドで適用した**。
  前サイクルで `L-REQ-FAILURE-MODES` を「人間が採用と判断したのにレビュアー枠を使い切っていて
  要件段階に一度も当たらなかった」と記録したのと同じ失敗を繰り返さないため。**採否そのものは
  人間が mandate で決める**ので、採らない判断なら今回の適用記録ごと落とせばよい。
- **枠を使い切ったときの返却の「形」は実測していない**(D-123、D-128)。`rate_limit_event` の構造は
  `status: allowed` / `allowed_warning` の状態で観測し、`rejected` は実行ファイルのスキーマから読んだだけ。
  どの形でも失敗に倒れて非 0 終了するので**1回の実行の判定は壊れない**。ただし **ADR-018 の下では、この未知が
  T-020 の列の完走を直接左右する**: 枠切れが `rejected` の `rate_limit_event` を伴わない形(JSON でない出力で
  `PARSE`、イベントが出ない等)なら、その停止はレート枠以外の停止として列を完走不可にし、`resetsAt` もログに
  残らない。その場合 T-020 は複数の枠にまたがって完走できず、次サイクルで扱う(D-129)。
- **新しいプロセスで `rate_limit_event` が毎回出るかは実測していない**。home-probe §3 は状態が**変わるたびに**
  出す作りと読んでいる。出ない呼び出しが多ければ、(h) は T-020 で常に `unknown` になり、レート枠による停止の
  述語も `last is None` で偽になる。T-020 の実行ログの `invocations_without_rate_limit_event` で分かる。
- **`claude-opus-5-5` と `--safe-mode` の組み合わせは未確認**(番兵実測は haiku だけ)。契約フィクスチャの
  取り直し(D-133)で確かめる。`--safe-mode` の下でも `init.skills` は存在した(probe §4、haiku)。一覧がモデルの
  文脈に入ってシステムプロンプト長を動かすかは未測定で、D-126 の `skills_digest` はそのための番人である。
- **最小ホームの `.claude.json` の中身は検査しない**(AR-130)。Console(API キー)のアカウントでログインした状態が
  残っていた場合に OAuth より優先されるかは未検証。実行時の `apiKeySource` 検証は D-087 / Clarifications で
  課さないと決めているので、この経路は NFR-8 の「測っていないこと」に属する。
- **オーバーヘッド定数が日/セッションでぶれるかは未実測**。`argv_digest` は argv の変化を検知するが、
  同じ argv で値がぶれる場合は検知できない。ぶれるなら「定数」の前提が崩れ、NFR-7 の分離が
  `unknown` を報告し続けることになる。
- **単価表の取得元 URL がまだ空**。NFR-7 が出所を要求しており、`claude-opus-5-5` と
  `claude-haiku-4-5` の両方について埋めないと **C-019 が判定不能**になる。`/tasks` の作業項目。

- **`TRUNCATED` の実物の形は未観測(`unknown`、D-118)**。claude 2.1.282 では `--max-turns 1` でも
  StructuredOutput で完了する。`error_max_turns` が返る条件も、そのときのイベント列の形も分からない。
  `stop_reason` の切り詰め値(`max_tokens` / `model_context_window_exceeded`、D-120)も CLI 経由では
  一度も観測していない。`TRUNCATED` の判定枝は成功フィクスチャから作った fake でしか検証されず、
  **判定器と fake が同じ仮定を共有する**(自己確認)。実物が別の形で来た場合の行き先は2つある:
  `is_error` が真なら `PROVIDER_ERROR`、偽で `structured_output` が無ければ **`SCHEMA`**(モデルの不適合と
  区別できない)(AR-103)。どちらも失敗に倒れて非 0 終了するので下流は壊れないが、内訳がずれる。
  **T-020 の実測で `SPAWN` / `INCOMPLETE_IDS` 以外の失敗が1件でも出たら**、その `stderr_masked` と
  `stdout_masked` の骨格(キーと `subtype` / `stop_reason` / `is_error` の値)を次サイクルで読む。
  `stdout_masked` は教材本文をマスクしてあるので、**それをそのままフィクスチャにはしない** —
  形が分かったら合成プロンプトで取り直す(NFR-2)。
- **`PARSE` はプロセス水準の失敗をまとめて吸収する**(D-119、AR-101)。stdout が空で非 0 終了する形は
  不正スキーマに限らない: 最小ホームの OAuth 失効、`claude` はあるが node が壊れている(exit 127)、
  CLI 更新による argv の拒否(`unknown option`)、クラッシュ。どれも `PARSE` に数えられ、**内訳からは
  区別できない**。区別の材料は実行ログの `exit_code` と `stderr_masked` にだけ残る。特に argv の拒否は
  「CLI の更新でゲートは緑のまま本番が壊れる」契約ずれそのもので、R-3 の7種の内訳では見えない。
  `PARSE` が出たら人が `stderr_masked` を読む、が今サイクルの運用。種別を足すなら R-3 の改訂になる。

## Adversarial review

> Findings from the independent `adversarial-reviewer` round before the mandate (procedure: design.md step 6),
> with the lead's disposition per finding. Blockers must be `fixed` or `disputed` (with the reason)
> before the gate; the human sees this table — and settles any unresolved dispute — at the mandate.

適用レンズ: L-DES-COVERAGE / L-DES-INFEASIBILITY / L-DES-NFR-HOLES / L-DES-UNSTATED-ASSETS /
L-DES-YAGNI の5件(`rein lens --select design` が applied として解決)と、
**L-DES-FAILURE-WALK**(`proposed` だが D-029 で人間が失敗モードの検査を design 段階から始めると
決めているため適用)。`L-DES-ADR` は `proposed` のまま未適用 — 条件(ADR に却下オプションがあるか)は
機械的に判定できるが、採否は人間が mandate で決めるもの。

**29 件(blocker 8 / major 16 / minor 5)。** blocker 8 件はすべて処置済み。

| ID | Sev | Finding | Disposition |
|----|-----|---------|-------------|
| AR-1 | blocker | 凍結されるイメージが設計の依存を1つも持たず、`uv sync --frozen` の前提である `uv.lock` も存在しない。今の状態で承認すると最初のタスクが `import anthropic` で落ちる | **run_experiment → fixed**: `uv lock` をコミットし、Containerfile を lock 由来の `requirements.txt` から焼く形に書き換え、ffmpeg を追加して再ピン。`rein baseline measure` で5ステップ緑を確認(smoke のみ後述) |
| AR-2 | blocker | Playwright E2E を `command` にする経路が無い(node_modules / `next build` / `PYTEST_DISABLE_PLUGIN_AUTOLOAD` / uid とブラウザ配置 / 2プロセス起動) | **reduce_scope → fixed**: 人間の判断で R-12 の証拠を `artifact` に落とし、ADR-012 を差し替え。node と Playwright はイメージに入れない |
| AR-3 | blocker | CosyVoice は pip で入らず(git clone + サブモジュール)、torch だけで 196 MB(圧縮時)。重みが無ければゲート内に mp3 が生まれず、R-7 の `command` 証拠が成立しない | **run_experiment → reduce_scope → fixed**: `pip download cosyvoice` の失敗と wheel サイズを実測。人間の判断で R-7 の証拠を `artifact` に落とし、ADR-004 / ADR-012 を更新 |
| AR-4 | blocker | `selector_kind` を定数にする設計が「根拠の要素種別が全問で同一」を**恒真式**にし、要件段階の AR-3(誤答を正解として取り込む)を開け直す | **revise_design → fixed**: 実行時にマッチした規則から導出する形に変更し、集合サイズが 2 になって赤くなる negative control フィクスチャを要求 |
| AR-5 | blocker | 外部依存の失敗経路が設計に1行も無い。LLM の分割呼び出しが途中で落ちると 120 問が「その他」に落ち、実データに合格ラインが無い(D-004)ため何にも引っかからず 90 分の音声になる | **revise_design → fixed**: `ClassificationResult(assignments, failures)` / `FetchFailure` / `SynthesisReport` を定め、その他比率・最大クラスタ比率・失敗件数を**閾値なしの警告として成果物サマリに必ず出す**。D-004 は変えていない |
| AR-6 | blocker | NFR-5 (d)「CI の run と同一のコマンド列」が構造的に成立しない(`uv sync` は遮断下で走らない)。不一致ごと凍結される | **revise_design → fixed**: `gate.sh` を作り、CI の `run` とゲートの argv が同じスクリプトを呼ぶ形にした。同一性の対象を検査ステップに限ることを設計に明記 |
| AR-7 | blocker | シャッフル seed を問題 ID だけから導出すると事前/事後で並びが同一になり、「位置を覚えたことによる偽陽性の排除」という基準の目的を裏切る | **revise_design → fixed**: seed を (セッション ID × 問題 ID) から導出し、同一問題の2セッションで `presentation` が異なることを機械判定 |
| AR-8 | blocker | 台本分割で `Pause` を含まないパートが生まれ、そのクラスタは R-7 の無音構造基準を必ず落ちる | **revise_design → fixed**(人間の判断): `Prompt \| Pause \| Answer` を最小単位とし、その境界でしか割らない。割れない場合は再生成側へ倒す |
| AR-9 | major | `runs[0].voiced` が要件に無い条件を足しており、`min_silence_sec` の意味論が未定義で判定が二読みになる | **revise_design → fixed**: 判定式を要件の文言に揃え、`min_silence_sec` を「これ未満の無音は Run として出さない」と定義 |
| AR-10 | major | 設計がどこにも書いていない受入基準が4本(R-1 / R-2 の解説件数、R-7 の失敗クラスタ列挙、R-8 の既習別集計) | **revise_design → fixed**: 各節に実装先を明記。`/tasks` で受入基準→タスクの全数対応を取るまで閉じない |
| AR-11 | major | 退化ガードに negative control が無く、スタブが返した割当を測るだけで赤くならない | **revise_design → fixed**: 退化スタブ2本(全問1グループ / 全問未分類)に対してガードが赤くなることをテストで要求 |
| AR-12 | major | 丸写し判定が誤答選択肢1件との比較なので、2件を貼り合わせた識別ポイントが通る | **revise_design → fixed**: 全誤答選択肢に対する一致区間の**和**を除いた残りで判定 |
| AR-13 | major | ラベル列挙判定がセグメント内に閉じており、セグメントを跨いだ4件列挙を素通しする | **revise_design → fixed**: 判定単位を「1トラック分のセグメント列を連結したもの」に拡大 |
| AR-14 | major | キャッシュ層(digest 名)と配信層(連番名)の関係が未定義で、部分書き込みが汚染キャッシュになる | **revise_design → fixed**: 配信側は毎回キャッシュから作り直す。ステージ出力は一時パスへ書いて `os.replace` |
| AR-15 | major | 2プロセス構成の CORS(preflight に 501)・`HTTPServer` の逐次処理・`sqlite3` の `check_same_thread`・`out/` の配信経路が未設計 | **revise_design → fixed**: Next.js の `rewrites` で同一オリジン化、`ThreadingHTTPServer` + リクエストごとの接続、読み取り専用の配信エンドポイント |
| AR-16 | major | NFR-2(critical)全体が相互検証の無い自前 fnmatch 評価器に乗る。`.gitignore` のディレクトリ形式は fnmatch では1行目から外れる | **revise_design → fixed**: ホストで `git check-ignore -v` との等価性を artifact で実測する検証を追加 |
| AR-17 | major | `AnswerEvidence.raw_text` は実教材本文なのに保存先が未定義。素の例外トレースにはログフィルタが効かない | **revise_design → fixed**: 根拠レコードは `out/<deck>/` のみ、本文を持つ dataclass は `repr=False`。`raw_text` はフィールドから外した |
| AR-18 | major | R-11 の混入防止は clustering にしか効かず、学習記録の分離は `deck_id` 列だけで構造的保証が無い | **revise_design → fixed**: `DeckScopedStore(deck_id)` 経由に限定し、生の接続を `store/location.py` の外に出さない |
| AR-19 | major | NFR-5 (c) の pre-commit に証拠経路が無く、`name-tests-test` が既存ファイルで既に赤 | **revise_design → fixed**: 証拠をホストでの artifact と明示。`--pytest-test-first` を `.pre-commit-config.yaml` に追加済み |
| AR-20 | major | 凍結前に必須の smoke 差し替え先が設計に無く、イメージ内で CLI を起動する経路も無い | **revise_design → fixed**: `gate.sh smoke` が `sys.path` に `src` を入れて `quiz_sift.__version__` を読む。`config.yaml` に反映済み。src/ が生えるまで赤なので**人間の判断で既知の赤として凍結**した |
| AR-21 | major | `inputs_digest` の構成要素が未定義で、プロンプト変更が古い成果物の再利用として通る | **revise_design → fixed**: 前段成果物 + プロンプト digest + モデル ID + 生成パラメータ。実装コードは入れない、と明記 |
| AR-22 | major | `command_timeout_sec: 1800` に CosyVoice の CPU 推論と E2E が乗る | **fixed(AR-2 / AR-3 の帰結)**: 音声生成も E2E もゲートから出たので、この衝突は消えた |
| AR-23 | major | `ignore_missing_imports = true` + `mypy_path` 不在で strict が広く空振りする | **revise_design → fixed**: `mypy_path = ["src"]`、`ignore_missing_imports = false` + override で限定。`pyproject.toml` に反映済み |
| AR-24 | major | R-10 の再生リストと R-7 の連番の関係(改名 / 複製 / m3u のみ)が未定義 | **revise_design → fixed**: `out/<deck>/review/` の別系列へ複製して連番を振る(グロッサリの D-049 と同じ形) |
| AR-25 | minor | `RecallKey` の tuple 等価比較は要素順の入れ替えで重複禁止を回避できる | **revise_design → fixed**: 重複判定を `frozenset(terms)` で行う |
| AR-26 | minor | `ports/browser.py` と LLM レスポンスキャッシュをどの要件も強制していない | **revise_design → fixed**: どちらも落とした。抽出器の純粋関数がテスト容易性を与えており、LLM キャッシュは R-3 の「呼び出しが2倍」基準が検出するために存在することになっていた |
| AR-27 | minor | mp3 への**書き出し**経路が設計に無い(`wave` は WAV のみ) | **revise_design → fixed**: エンコードも ffmpeg。CBR と明記 |
| AR-28 | minor | CDP はクッキーを読み出せるのに NFR-3 のマスク対象から Udemy 資格情報が外された | **revise_design → fixed**: 「保存しない」と「露出しない」を区別し、Cookie 系をマスク対象に残す |
| AR-29 | minor | NFR-7 の単価表が cache トークンと thinking の出力トークンを勘定に入れていない | **revise_design → fixed**: `calls_by_model` の内訳を cache write / cache read / 思考を含む出力で分ける |

### 2巡目(blocker 修正の再レビュー、規定により1回のみ)

| ID | 2巡目の判定 | 内容と最終処置 |
|----|------------|---------------|
| AR-1 | **closed** | lock・requirements・ffmpeg 入りイメージが実在し、凍結 baseline が `docs`(mkdocs)まで緑 = 依存が実際に入っている。手編集 requirements の裏切りはイメージに `uv` があるため skip されず赤になる |
| AR-2 / AR-3 | **closed** | 証拠を `artifact` に落としたので「遮断下で command にする経路」の要求自体が消えた。node / Playwright / torch / CosyVoice はイメージに無い |
| AR-4 | **closed** | 「両枝が同じ定数を返す」「1つの汎用規則で吸収する」の2通りとも、要求した negative control が落とす |
| AR-7 | **closed** | 問題 ID のみ / デッキ由来の決定的セッション ID / セッション ID のみ の3通りを試して、位置記憶の偽陽性を復活させられなかった |
| AR-5 | still-open → **fixed** | 「サマリに警告を出す」だけで非 0 終了も判定基準も無く、C-003 に失敗件数が一語も入っていなかった。**`failures` が空でない実行は下流へ進まず非 0 終了**に変更し、要件 R-3 に基準を2本追加、C-003 の statement にも反映(D-075)。D-004 は変えていない |
| AR-6 | still-open → **fixed** | `gate.sh` は作ったが要件 NFR-5 (d) と C-017 が未改訂で、`uv sync` が対象に残り、かつ「部分集合でも真」だった。**要件と claim を「集合の一致」に書き換え**、CI 側も読むテストと各ケースの本体コマンド検査を追加 |
| AR-8 | still-open → **fixed** | 最小単位に `Discrimination` が入っておらず、末尾 `Discrimination` だけのパートが作れた。**最小単位を「1問分」に変更**し、問の境界でしか割らないことにした(D-065 改訂) |
| N-1 | newly-broken → **fixed** | lock 一致検査が2ファイル同士しか見ておらず、イメージの中身は誰も見ていなかった。**イメージ内の実バージョンと焼き込み pin を突き合わせるテスト**を追加(61 pin を読み、1つ偽装すると赤くなることを確認) |
| N-2 | newly-broken → **fixed**(人間の判断) | artifact 実測の実行主体が成果物の4箇所で矛盾していた。**人が環境を用意し、ループが実測する**に統一(D-076)。artifact の本数も 4→5 に訂正 |
| N-3 | newly-broken → **fixed**(人間の判断) | `gate.sh` を `scripts/`(guard.paths)に承認前に置いていた。`src/` に続く2回目のガード違反。**リポジトリ直下へ移した**(D-077) |
| 追加 | **fixed** | `config.yaml` の smoke コメントが D-066 と食い違っていた / `gate.sh` の未使用 `all)` ケースを削除 |

**未解決の dispute**: なし。1巡目 29 件と2巡目 6 件をすべて処置した。
**規定により、これ以上のレビュアーのラウンドは回さない**(`/design` step 6: 再招集は1回のみ)。
**2巡目で still-open / newly-broken とされた 6 件の最終修正は、独立に検証されていない。**
ただし N-1 と S-2 の修正については、テストが実際に赤くなることを手元で確認してある
(pin を1つ偽装 → 赤、CI から1ステップ削除 → 赤)。
**人間が判断した点**: R-7 / R-12 の証拠種別(AR-2 / AR-3)、NFR-5 (d) の成立方法(AR-6)、
台本分割の最小単位(AR-8)、smoke の凍結(AR-20)、artifact の実行主体(N-2)、`gate.sh` の位置(N-3)。


### 2026-09-25 のデルタ(従量課金の禁止 → Claude Code CLI 経路)に対するラウンド

適用レンズ7本。うち `L-DES-ADR` と `L-DES-FAILURE-WALK` は `proposed` だが、**前サイクルで
`L-REQ-FAILURE-MODES` を「人間が採用と判断したのにレビュアー枠を使い切って一度も当たらなかった」と
記録した失敗を繰り返さないため、このラウンドで適用した**(採否そのものは人間が mandate で決める)。
**7本すべてが何かを見つけた。** blocker 4件 / major 9件 / minor 4件。

| ID | Sev | Lens | Finding | Disposition |
|----|-----|------|---------|-------------|
| AR-54 | blocker | COVERAGE | **C-019 / C-020 の statement が要件の改訂に追随しておらず、達成不能な claim として凍結される寸前だった。** C-020 は「HOME は資格情報1ファイルだけ」(D-096 が同一ファイル内で否定)と「有効ツール0件」(D-092 と実測が不可能と言う)を主張していた。C-019 はレート枠の記録も単価表の2モデル化も持っていなかった | **fixed** — 両 statement を書き直した。**これはリードの欠陥**: C-019/C-020 を一度更新した後に D-092 / D-096 で要件がさらに動き、再同期しなかった |
| AR-55 | blocker | COVERAGE / INFEASIBILITY | **NFR-8 (a) が構造上あたりまえに緑になる。** `claude` 不在と最小ホーム不在の起動時検査でパイプラインが LLM を1つも組み立てないまま終わるので、**既定が SDK アダプタのままでも `anthropic` は `sys.modules` に現れない**。R-3 の退化ガードと契約フィクスチャには negative control を課しているのに、NFR-8 の中心基準にだけ無かった。加えて `build_default_llm` の signature も本体も設計に一度も書かれておらず、`LlmFactory` の引数1つ・入れ子禁止(D-099)・fake runner の注入という3制約が互いに噛み合っていなかった | **fixed** — negative control(選択点を SDK に差し替えると (a) が赤くなること)を要件に追加。`build_default_llm` の本体を設計に明記し、`CommandRunner` / `Workspace` は `ClaudeCliLlm.__init__` のキーワード既定値として持つ形に確定した(注入はテストからの直接構築 = (c) が認める opt-in と同じ経路) |
| AR-56 | blocker | INFEASIBILITY / FAILURE-WALK | **レート制限のホワイトリストに既知の要素が1つも無く、判定順序が枝を到達不能にし、待機に上限が無い。** 実測された `status: allowed_warning` は**成功した呼び出しに付いてくる**値なので、これを到達と数えると全呼び出しが待機に倒れる。判定順序が `PARSE` を先に置いており、枠切れの `claude` が JSON でないエラー文を出す最頻の形ではレート制限判定に到達しない。7日枠の `resetsAt` は数日先になりうるのに全体上限は12時間 | **fixed** — `allowed_warning` は到達ではないと明記。**レート制限判定を `PARSE` より前へ移した**。`resetsAt` が残り予算を超えると分かった時点で待たずに成果物を保存して非 0 終了する規定を追加(待機の長さは待つ前に分かる) |
| AR-57 | blocker | NFR-HOLES | **`--max-turns 3` によりターン数が実行時変数になり、`provider_invocations` は「レート枠の消費実態」ではない**(子プロセス1本の中でモデル往復が 2 回以上)。**固定オーバーヘッド定数も成立しない**(入力トークン ≈ ターン数 × オーバーヘッド + 材料 なのに、定数にターン数のフィールドが無い) | **fixed** — 実測で裏付けた(成功時 `num_turns: 2` / `stop_reason: tool_use`。構造化出力は `StructuredOutput` ツール経由で配送されるため 1 ターンでは届かない)。**`model_turns` を第3の語として追加**し、枠を消費するのはこれと定義。オーバーヘッドは**1ターンあたり**の定数にし、判定を `overhead × num_turns + material = 合計` に変えた |
| AR-58 | major | ADR | ADR-014 が Option D を「**害が消えるから**」という理由で却下し(不可逆ゲートは害を管理する仕組みであって害を維持する理由ではない)、採用した Option C の Cons を1件も書いていなかった | **fixed** — Option D の却下理由を symlink の未実測リスク1点に絞り、教材本文が消えることは却下理由にしないと明記。Option C に実在する短所4件(資格情報の失効が検査で見えない / 固定パスのプロセス間共有 / 長期コピーが2箇所 / 平文が無期限に蓄積)を追記 |
| AR-59 | major | ADR | **ADR-015 の決め手が、同じ ADR が偽らしいと書いている仮定に乗っていた。** 「95% を Haiku に」は枠の消費がモデルに依らない場合のみ成立するが、同 ADR の表が `Opus limit` の実在を挙げている。分類は最初のステージなので、Opus 枠に当たると後続の Haiku が1回も走らない | **fixed** — 論証の限界を ADR に明記し、Option C の短所として「2つの枠のどちらに当たっても止まる」を追記。モデルごとの枠の重みは `utilization` の差分で実測できる(枠を使い切らないので D-085 の対象外)ことを `/tasks` の作業項目にした |
| AR-60 | major | INFEASIBILITY / UNSTATED-ASSETS | argv の「実測で確定」に5項目の裏付けが無い。とくに **`--settings` がファイルパスしか受けないなら、書き先が最小ホームなら (b) が赤、空 cwd なら (e)4 が二読みになり逃げ場がない** | **fixed(実測で解消)** — (e) の argv 全体を1回通し、`--exclude-dynamic-system-prompt-sections` / `--strict-mcp-config` / `--mcp-config`(インライン)/ `--settings`(インライン)/ `--permission-mode` がすべて受け付けられ、`stream-json` の `result` が4値 `usage` と `total_cost_usd` を返すことを確認した |
| AR-61 | major | NFR-HOLES / FAILURE-WALK | (e)1 の判定対象 `init.tools` は**ターン 0 の宣言**だが、脅威(第三者テキストの注入)が働くのはターン 1 以降。残る `Skill` / `ToolSearch` は実行中に能力を増やせる | **fixed** — 実測で**ツールを `StructuredOutput` の1本まで削れる**ことが分かったので、増える余地そのものが消えた。加えて `stream-json` の全イベントを通して許容集合外のツール**使用**が現れたら失敗にする、時間軸に沿った判定を追加 |
| AR-62 | major | INFEASIBILITY | CLI 経路には `max_tokens` 相当が無い。ID 完全性で `unassigned` を必須にした結果 50 問なら最低 50 個のキーが必ず出力され応答は純増するのに、切り詰めは再試行禁止(D-101)のまま実行全体を非 0 終了させる | **fixed** — 切り詰めは `stop_reason` で識別できる(実測: 成功時 `tool_use`)ので `TRUNCATED` として別に数え、`CLASSIFY_BATCH_SIZE` を 50 → **25** に下げた。`inputs_digest` の生成パラメータには `max_tokens` の代わりに `--max-turns` とバッチサイズを入れる |
| AR-63 | major | FAILURE-WALK | 契約フィクスチャが成功1件のみで、**`FailureKind` 6種と判定順序はすべて同じ実装者の fake だけで検証される**。失敗の形の一部はただで採れる | **fixed(実測で採取)** — `error_max_turns`(`--max-turns 1` で再現)と、不正な `--json-schema` に対する **JSON ですらない `Error: ...` の標準出力**(`PARSE` の実物)を採取。フィクスチャを3件に増やし、それぞれが期待する `FailureKind` へ落ちることをゲート内の `command` 証拠にする **2026-09-25 追記(T-029 実測)**: `error_max_turns` は 2.1.282 で再現せず、不正スキーマの `Error:` は stderr に出た。D-118 / D-119 / ADR-016 で置き換えた |
| AR-64 | major | FAILURE-WALK | **並行度 1 は1実行の中でしか効かない。** 最小ホームは固定パスのプロセス間共有資源で、R-3 が要求する2回の実行を並行に走らせれば `.claude.json` を同時に書く。**(b) の判定も壊れる**(別プロセスの書き込みが「製品が作ったファイル」に数えられる) | **fixed** — 最小ホームの使用をプロセス間ロックで直列化し、取得できなければ起動時にエラー終了する規定を追加 |
| AR-65 | major | NFR-HOLES | **失敗・レート制限で終わった呼び出しの usage の扱いが未定義**(枠を消費した呼び出しだけが会計から抜ける)。`Meter.record` はモデル ID が一字違うだけで例外を投げフル処理が計測器で死ぬ。**ログのフィールド一覧が設計のどこにも無い** | **fixed** — 「成否の判定より先に `usage` を記録する」を要件と設計の両方に明記。モデル ID の照合失敗は `{"status":"unavailable","reason":"unknown_model"}` へ倒し例外にしない。`Meter.finish` が出す JSON Lines のフィールドを設計に**名前で列挙**した |
| AR-66 | minor | YAGNI | 並行度 1 を凍結したのに、ロック付きの並行アイドル区間計算を作っている(設計自身が「次サイクルのため」と書いている) | **fixed(reduce_scope)** — 直列前提にし、累積待機時間は `実経過 − 稼働時間の総和` で求める。並列化する次サイクルで区間計算へ置き換える |
| AR-67 | minor | UNSTATED-ASSETS | `llm_contract.py` が `llm_anthropic.py` の既存パーサを再実装し、**残す側は削除対象の旧挙動を持ったまま**。要件が認めた経路(テストからの直接構築)で、R-3 の ID 完全性に違反するアダプタが構築可能になる | **fixed** — 既存の `_proposals` / `_string` / `_schema` を `llm_contract.py` へ**移して共有**する(再実装しない)。残す SDK アダプタも新契約に従うと明記 |
| AR-68 | minor | COVERAGE | 決定 ID の引用が3箇所誤り(D-094↔D-099、D-096↔D-100、D-097↔D-096)、**ADR-016 は存在しない** | **fixed** — 4箇所とも訂正 |
| AR-69 | minor | COVERAGE | (d) は `pyproject.toml` のスクリプトを数えるが、`cli.py` は `__main__` ガードを持ち docstring が `python -m quiz_sift.cli` を製品の使い方として書いている | **fixed** — (d) の走査対象を「`__main__` ガードを持つモジュールを含む到達可能な入口の集合」全体に広げた |
| AR-70 | minor | ADR | ADR-013 の「Implementation effort: 中」「動くのは呼び出し経路だけ」が実物(新規9モジュール・新 Protocol・プロンプト書き換え)と合わず、却下案の工数を「1サイクル」と書いたのと非対称 | **fixed** — effort を**大**に改め、波及の実体を Background にも書いた |

**このラウンドで実測が4件の finding を直接解消した**(AR-60 / AR-61 / AR-62 / AR-63)。設計を書き直す
のではなく、未確認だった前提を叩いて確かめる方が安かった。最大の収穫は **ツールを
`StructuredOutput` の1本まで削れること**と、**素の許容集合に `Glob` / `Grep` が含まれていたこと** —
後者は要件が列挙した 19 本の許容集合が**ファイルシステム読み取りを許してしまう**ことを意味していた。

**未解決の dispute**: なし。
**レンズ候補(`unclassified`)**: レビュアーが AR-54 について「**要件文書が改訂されたサイクルで、その
要件を参照する claim の statement が改訂されていない**」という条件を提案した。7本のどれにも当たらず、
機械判定可能で、`/revise --to mandate` で戻ったサイクルでは必ず起こりうる形。**今回が1度目なので
`unclassified` として記録し、2度目が出たらライブラリへ足す**(採否は acceptance で人間が決める)。

#### 2巡目(blocker 修正の再レビュー、規定により1回のみ)

**4件とも still-open**、さらに新規 blocker 2件を含む8件。1巡目の処置はいずれも「別の場所へ問題を
移しただけ」だった。レビュアー枠は使い切ったので、以下はリードが処置を当てたもので**独立には
検証されていない**。

| ID | 2巡目の判定 | 内容と、リードの最終処置 |
|----|------------|------------------------|
| AR-54 | still-open → **fixed** | C-019 が AR-51 の恒等式対策(差と切片の判定)を落としており、`material := 合計 − overhead` と実装すれば**常に緑**。C-020 は要件が名指しで禁じた「**列挙の側からの保証**」(argv の deny 列挙を読む判定)を凍結しており、要件 (d) の「コンソールスクリプト1つだけ」と (f) の証拠の形を落としていた。**C-019 / C-020 を再度書き直した** |
| AR-55 | still-open → **fixed** | 追加した negative control は、`llm_anthropic.py` が先頭に `import anthropic` を持つので**パイプラインが LLM を組み立てたかに関係なく import だけで赤くなる** — 赤の出所が測るべき振る舞いではない。また `AnthropicLlm.__init__` は `(client, recorder, *, model)` なので **(c) の形を保ったまま SDK へ差し替えること自体が不可能**で、要件が要求する control が書けない。**positive control(`llm_calls ≥ 1`)を足し、差し替え版でもそれを要求する形にした。`AnthropicLlm` のコンストラクタを `(meter)` へ揃えることを設計に明記した** |
| AR-56 | still-open → **人間が判断を変えて解消** | **`allowed_warning` を除外するとホワイトリストが空になり、`RateLimited` の枝に到達する入力が存在しない**。判定順序の並べ替えも無効 — `rate_limit_event` を読むには stdout を JSON として解釈する必要があり、枠切れで JSON でない出力が返るなら順序に関係なく `PARSE` に落ちる。**人間が D-091 を撤回し、レート制限を特別扱いしない(`PROVIDER_ERROR` として失敗に数え非 0 終了、`resetsAt` を残して人が再開)ことにした。** 判定表も待機も再試行も丸ごと不要になり、反証不能な枝が消えた |
| AR-57 | still-open → **fixed** | `model_turns` を足したのに**記録する口が無かった**(`RunMeter` にも `UsageRecorder.record` にも `num_turns` を渡す経路が無い)。判定式 `overhead × num_turns + material` は**混成モデルの下で真になりえない**(モデルごとに定数が違う)。回帰の切片は「1子プロセスあたり」で「1ターンあたり」ではない。**`end_invocation(model, turns, usage)` を置き、判定を `Σ_model (overhead_model × turns_model) + material` へ、定数と `overhead` ログをモデル別の写像にした** |
| AR-71 | **blocker(新規)→ fixed** | **AR-54 の修正を C-019 / C-020 の2本にしか当てず、同じデルタで改訂された C-003(R-3)を取り残した。** C-003 は「外部 API 呼び出しが2倍」と書くが R-3 は AR-38 で `llm_calls` に限定済みで、レート制限の再起動を認めた時点で子プロセス数は2倍にならないことが保証されていた。**提案された `unclassified` レンズ(要件が改訂されたのに claim が改訂されていない)の2度目が、同じラウンドの中で起きた。** C-003 を書き直した |
| AR-72 | **blocker(新規)→ fixed** | **`Workspace` を注入可能にしたこと(AR-55 の処置)が ADR-014 / NFR-6 の境界規定を破る。** 差し替えると (b) の「製品が `HOME` 配下に作成したファイルが 0 件」が fake の一時ディレクトリに対する判定になり**構造上あたりまえに 0 件**。AR-55 の処置が (b) に「赤くなり得ない緑」を作っていた。**`Workspace` は差し替えず、根のパスを引数に取る実クラスにしてゲートで実物を走らせる形にした** |
| AR-73 | major → **fixed** | キーワード既定値は import 時に1回評価されるので、AR-64 のプロセス間ロック取得と最小ホーム検証が `composition.py` の import で走り、**(c) の AST テスト自体がサンドボックスの import 時に死ぬ**。`new_cwd()` の後始末も未規定。**既定値をやめて `__init__` 本体で構築し、cwd は呼び出しごとに後始末する**ことにした |
| AR-74 | major → **fixed** | **`__init__` のデフォルト式は (c) の死角で第2の選択点になる** — `SubprocessRunner() if os.environ.get(...) else Other()` が全検査を素通りする。D-099 と (c) が追い出した分岐と環境変数参照が検査の無い場所へ移るだけ。**`__init__` にも同じ AST 制約を掛ける**ことにした |
| AR-75 | major → **fixed** | `model_turns` に記録経路が無い(AR-57 と同根)。`end_invocation(model, turns, usage)` で閉じた |
| AR-76 | major → **fixed** | オーバーヘッドからモデル次元が落ちていた(AR-57 と同根)。定数もログもモデル別の写像にした |
| AR-77 | major → **fixed** | `FailureKind` が本文で「6種」、下で `TRUNCATED` を足して7種、契約フィクスチャ節は「6種」のまま。**`error_max_turns` がどの Kind へ落ちるか未定義なのに「期待する Kind へ落ちることを証拠にする」と書いていた**。7種へ統一し、**人間が `error_max_turns` → `TRUNCATED` と決めた**(D-102)。不正スキーマ → `PARSE` も明記 |
| AR-78 | minor → **fixed** | NFR-8 の見出しが「(a)〜(f) がいずれも 0 件」なのに列挙に (g) があり、(g) は件数ではない。「(a)〜(g) を判定する((a)〜(f) は 0 件、(g) は argv の形の一致)」へ改めた |

**未解決の dispute**: なし。
**規定により、これ以上のレビュアーのラウンドは回さない**(`/design` step 6)。**2巡目の 12 件の処置は
すべてリードがレビュアーの提案を適用したもので、独立には検証されていない。** 要件フェーズと同じく
**同じ形の欠陥(判定器が実装側を読みに行く / 検査の無い場所へ問題が移る)が2巡続けて出た**ことを、
mandate の承認時に人間が読む前提で記録する。
**`unclassified` レンズ候補の2度目**: 「要件が改訂されたサイクルで、その要件を参照する claim の
statement が改訂されていない」— AR-54(C-019 / C-020)と AR-71(C-003)で**同一サイクル内に2度**
起きた。ライブラリへ足す判断は acceptance で人間が行う。

### 2026-09-25 T-029 実測デルタ(D-118 / D-119 / D-120 / ADR-016)に対するラウンド

T-029 のホスト実測で崩れた2つの前提(`--max-turns 1` で `error_max_turns` にならない / 不正スキーマの
`Error:` は stderr に出る)を受けた差分に対して、新しいコンテキストのレビュアーが1巡行った。レンズは
凍結済みの設計レンズ7本(`rein lens --select` は再実行していない)。**blocker 0 / major 5 / minor 3**。

| ID | 重大度 | 内容 | 処置 |
|---|---|---|---|
| AR-100 | major | JSON として読めるが `result` の無いイベント列(途中のクラッシュ)の行き先が未定義 | **revise_design**: `PARSE` を「`result` をちょうど1つ含むイベント列にならない」と定義(D-119) |
| AR-101 | major | stdout 空・非 0 終了の形は OAuth 失効、node の破損、argv の拒否、クラッシュにも共通し、すべて `PARSE` に黙って吸収される | **revise_design**: 吸収することを明記し、Open questions に列挙。区別の材料は `exit_code` / `stderr_masked`。種別を足すのは R-3 の改訂なので今サイクルでは行わない |
| AR-102 | major | 「`stop_reason` が切り詰めを示す」値が列挙されておらず、fake と判定器が同じ仮定を共有する | **revise_design**: `max_tokens` / `model_context_window_exceeded` の2値に限定(D-120)。CLI 経由では未観測なので Open questions に置く |
| AR-103 | major | 残るリスクの記述が不正確(`is_error` が偽なら `SCHEMA` に落ちる)。マスク済みの stdout をフィクスチャにする前提が書かれていない | **revise_design**: 行き先を2つとも記述。T-020 では `SPAWN` / `INCOMPLETE_IDS` 以外の失敗をすべて読む対象にする。採取は合成プロンプトで取り直す |
| AR-104 | major | T-026 A-2、T-027 A-5、T-029 A-1(artifact、operator_surface を含む)がデルタと矛盾。`error_max_turns.*` の採取物が名前だけ残る | **/tasks で処置**: 受入基準の書き換え、採取物の削除、meta に終了コードと stderr を記録する基準の追加 |
| AR-105 | minor | ADR-013 の「3件」、AR-63 の処置、自己評価の件数が古い | **revise_design**: 注記と数字を直した。`claude-cli-contract.md` は T-029 の未コミット出力なので /tasks で扱う |
| AR-106 | minor | R-3 の「`stop_reason` および `subtype`」と設計の「または」が食い違う | **revise_design**: R-3 を「どちらか」に揃えた(意味の明確化で、基準の緩和ではない) |
| AR-107 | minor | 自己評価の「反証不能な枝が消えた」が、`TRUNCATED` 枝については今は偽 | **revise_design**: 文面を直した |

**再レビューはしていない**。blocker が無いので、規定の「blocker 修正のみを1回再レビュー」は発動しない。
処置の文面はリードが適用したまま、誰にも読まれていない。

### 2026-09-26 のサイクル(rein 0.10.0 の差分: 最小ホームの中身検査・`--safe-mode`・実行の列・overage)

レンズ(`rein lens --select design` の applied 5本): L-DES-COVERAGE / L-DES-INFEASIBILITY / L-DES-NFR-HOLES /
L-DES-UNSTATED-ASSETS / L-DES-YAGNI。5本とも何かを見つけた。`proposed` の L-DES-ADR / L-DES-FAILURE-WALK は
今回は適用していない(採否は mandate で人間が決める)。

**12 件(blocker 0 / major 7 / minor 5)。** blocker が無いので再レビューは行っていない。**処置の文面は誰にも読まれていない。**

| ID | Severity | 指摘 | 処置 |
|---|---|---|---|
| AR-123 | major | `completed` に「最終段まで走ったか」が無く、cluster だけの列が完走になる。段ごとに進めた正当な列は `not_completable` になる | **revise_design → fixed**(D-145): 最後の実行の `stage` が `audio` で exit 0 を条件にし、途中の exit 0 を許す |
| AR-124 | major | 再現性測定(C-003)に再開した列を使うと、和がやり直し分を含み「1回分の2倍」が赤になる | **revise_design → fixed**(D-142): 1実行だけで exit 0 まで走った `--fresh-run` の列を2本使う |
| AR-125 | major | 同じ digest 名で上書きされ、再現性の2つの割当が同時に残らない。再開した列が他の列の上書きした中身にヒットする | **revise_design → fixed**(D-137 改訂): 迂回・再開の実行は `inputs_digest` に `run_id` を加え、列ごとに成果物を分ける |
| AR-126 | major | 起動前検査の対象(全段か LLM 段か)が D-115 と食い違い、ゲート内の cli テストが `claude` 不在で落ちる。差し替え口が未定義 | **revise_design → fixed**(D-143): LLM を呼びうる段だけに掛け、`cli.main` の `preflight` 引数を既定値付きで置き、既定値と入口を signature / AST で判定する |
| AR-127 | major | overage / `rejected` の後も同じ段の残りの呼び出しを続け、従量課金と捨てられる枠消費が増える | **revise_design → fixed**(D-144): その段の残りの呼び出しを発行せずに非 0 終了する |
| AR-128 | major | 誰でも書ける祖先 `/tmp/.claude/settings(.local).json` の `env` が `--safe-mode` 下で読まれるか未測定 | **run_experiment → closed**: 2026-09-26 に実測し、**読まれない**ことを確認した(probe §4)。設計は変えない |
| AR-129 | major | 取り直したフィクスチャに `rate_limit_event` が無いと、(h) の正例と骨格が失われる | **revise_design → fixed**(D-133 追記): 3回まで採り直し、無ければ needs-revision で止めて人間が `/revise` で決める。これまでの実呼び出しでは毎回届いている |
| AR-130 | minor | 最小ホームの `.claude.json` の認証状態(API キーのアカウント)を検査していない | **人間の判断へ(mandate の質問)**: 実行時の `apiKeySource` 検証は D-087 で課さないと決めている。Open questions に記載 |
| AR-131 | minor | 許可集合を終了行から作ると、実行中に作った段にヒットせず1実行で再クラスタリングが繰り返される | **revise_design → fixed**(AR-125 と同じ処置で解消。許可集合そのものが無くなった) |
| AR-132 | minor | 途中まで書かれた1行で、そのデッキの全列の終了行の書き込みが失敗する。終了行の fsync が未規定 | **revise_design → fixed**(D-145): 読めない行は列の開始より後ろならその列を `not_completable` にし、例外にしない。追記前に改行境界を保証し、終了行も fsync する |
| AR-133 | minor | `completed` の列にも `--resume` でき判定が反転する。最初の起動の検査落ちで再開できない。`resumable` が未定義 | **revise_design → fixed**(D-145): `--resume` は `resumable` の列だけ。最初の起動が落ちたら新しい列として始め直す(要件の文言も修正) |
| AR-134 | minor | `skills_digest` は要件の出所に無く、根拠なく `unknown` を増やす(YAGNI) | **dispute_finding**: 2026-09-26 の実測で `--safe-mode` の下でも `init.skills` が存在した(probe §4)。一覧がモデルの文脈に入るかは未測定で、入るなら argv が変わらないまま定数がずれ、C-019 の「記録済み」が誤った数値で出る。`unknown` は較正をやり直す合図であり、誤った数値より安全側。D-126 は要件の Open questions にも書いてある |

**local と判定した決定の reach**: レビュアーは、D-133・D-136・D-137 が C-003 / C-020 の証拠の成立を左右すると指摘した。
処置後の D-133(証拠の形が変わる場合は `/revise` へ止める)、D-137(列ごとに成果物を分ける)、D-142(再現性に使う列を狭める)は、
いずれも証拠を緩めず、claim の文面を動かさない。そのため local のままとし、mandate の画面に「聞かれなかったこと」として出す。
D-134・D-135・D-138〜D-141 については、覆したときに動くのが内訳・判定表・記録の形だけであることを、レビュアーが反証できなかった。

**未解決の dispute**: AR-134(上記)。人間が採らない判断をするなら、D-126 / D-141 と要件 Open questions の1項目を落とせばよい。

## Self-assessment (assumptions, confidence)

- **Assumptions made**:
  - Udemy のページ構造は HTML から解析できる形になっている。**未検証**。R-2 の前提。
  - CDP 接続が Udemy のボット検知を回避できる。**未検証**。
  - CosyVoice の文内コードスイッチが AWS のサービス名混在に耐える。**未検証**(公称値のみ)。
  - CosyVoice をホストに導入できる → **2026-09-24 に実測済み。導入でき、3音声の合成まで通した**
    (Python 3.13 のまま、上流のハードピンを外して現行版で解決。`9627cc5`)。
  - **Claude Code CLI の出力契約(`--json-schema` / `usage` の4値 / `total_cost_usd` /
    `rate_limit_event` / `init.tools`)が、ホスト上の CLI が自動更新されても維持される**。2.1.281〜2.1.282 で
    実測。**イメージに焼けないので凍結できない**(ADR-001 が効かない唯一の依存)。
  - **枠を使い切ったときに `claude` が何を返すかは未観測**。D-091 の撤回で1回の実行の成否は左右しなく
    なったが、**ADR-018 の列の完走判定は、この未知に左右される**(`rejected` を伴わない形なら列は完走不可)。
  - **Claude Pro のレート枠がフル処理を賄えるかは未検証**(D-085)。2026-09-25 時点で 7日枠が
    **95%**、リセットは 2026-09-28 08:00 JST。**T-020 はそれ以降でなければ完走しない見込み**。
  - ffmpeg / fugashi + unidic-lite がネットワーク遮断下で動く → **2026-09-22 に実測済み。動く**。
  - 作者が唯一のユーザーであり、多ユーザー対応・認証・可用性は不要。
- **Confidence**:
  - **高**: R-1 / R-8 / R-10 / R-11 の設計。純粋関数と型で受入基準を機械判定でき、敵対的レビューの
    2巡目で指摘された抜け(seed、DeckScopedStore、既習別集計)も塞いだ。
  - **高**: NFR-6 の実現性。イメージを実際に組み直して `rein baseline measure` で緑を確認した。
  - **中**: R-3 / R-4 / R-5。下層は機械判定でき negative control も入れたが、生成物の質は人の目が最終審。
  - **中**: R-6 / R-7。ラベル列挙判定と無音構造判定は純粋関数に落ち、無音 3.3 秒挿入・3.0 秒判定は
    実測で決めた。**ただし「生成した mp3 に無音がある」は artifact なので、ゲートでは守られない**。
  - **低**: R-2。ページ構造・CDP・ボット検知のいずれも未検証。
  - **低**: R-12。ブラウザ動作が artifact になり、2プロセス構成(rewrites / ThreadingHTTPServer /
    リクエストごとの接続)は設計で塞いだが**一度も動かしていない**。
  - **低**: NFR-1 の判定能力。上の Open questions の1点目。
  - **低**: NFR-8 と NFR-7 の CLI 経路まわり。**2巡のレビューで blocker が2巡とも全件 still-open**
    と判定され、しかも「判定器が実装側を読みに行く」「検査の無い場所へ問題が移る」という同じ形の
    欠陥が繰り返し出た。実測が3件を直接解消した(ツールを1本まで削れる / `Glob`・`Grep` の存在 /
    argv の受理)。失敗形の採取は T-029 の実測で半分崩れた(D-118 / D-119)。その一方で、**最終的な文面は誰にも読まれていない**。
  - **中**: R-3 の失敗分類。D-091 の撤回で「待機して継続」の枝は消えた。ただし `TRUNCATED` の枝も、
    2.1.282 では到達する実入力が見つかっていない(AR-107)。R-3 が要求するので残すが、判定は
    fake に対する `command` だけになる。実物の契約フィクスチャは2件(成功 / `PARSE`)。`TRUNCATED` の実物は
    2.1.282 で採れず、fake だけで検証する(D-118)。
- **Open questions / points for the human to decide**:
  - 上の Open questions(12点)。
  - `/tasks` で分解した結果が現実的な規模に収まらない場合の、スコープ縮小の判断。
  - **2巡目の修正が独立に検証されていない**: 再招集枠は使い切った。AR-5 / AR-6 / AR-8 / N-1 / N-2 /
    N-3 の最終修正はリードが適用したままレビューを受けていない(N-1 と S-2 のテストが赤くなることは
    手元で確認済み)。**2026-09-25 のデルタも同じ状態** — AR-54〜AR-78 の2巡目 12 件はリードが
    適用したままで、**要件フェーズと設計フェーズの両方で「同じ形の欠陥が2巡続けて出た」**。
  - **AR-46: Anthropic SDK アダプタを残すか削除するか**(人間の判断として持ち越し)。残す決定は
    済んでいるが、レビュアーは「どの要件も必要としていないのに NFR-8 (a)(c) の判定機構・
    optional extra・mypy overrides・依存監査の対象面を丸ごと生んでいる」と指摘した。**削除すれば
    要件が2本簡単になり、AR-55 が要求する `AnthropicLlm` のコンストラクタ変更も不要になる。**
  - **`L-DES-ADR` と `L-DES-FAILURE-WALK` の採否**(どちらも `proposed`)。このラウンドで適用し、
    3本とも何かを見つけた(ADR-014 の「害が消えるから却下」、ADR-015 の自己矛盾した決め手、
    ADR-013 の工数の非対称)。採らない判断なら適用記録ごと落とせる。
  - **週次枠 95% により T-020 は 2026-09-28 08:00 JST 以降でなければ走らない**。
  - **ガードの境界を2度越えた**: `src/` と `scripts/` に、Bash のヒアドキュメントで
    PreToolUse フックを素通りして書き込んだ。どちらも是正済みだが、同じ機構で再発しうる。

### 2026-09-26 のサイクルの自己評価

- **Assumptions made**:
  - `--safe-mode` は `claude-opus-5-5` でも受け付けられる。**未確認**(haiku だけ。D-133 の取り直しで確かめる)。
  - 新しいプロセスでも `rate_limit_event` が毎回出る。**4回の実呼び出しでは毎回出た**が、状態が変わったときだけ出す作りとも読める。
  - `inputs_digest` に `run_id` を加えても、NFR-4 の通常実行の性質は変わらない(通常の実行の digest は変えないため)。
  - 枠切れのときは、`rejected` を持つ `rate_limit_event` が届く。**未観測**。届かなければ、T-020 の列は枠をまたいで完走できない。
- **Confidence**:
  - **高**: 起動前検査(ADR-017)。判定対象は実測で読まれた場所に絞った。祖先の settings が読まれないことも、2026-09-26 に実測で確かめた。
  - **中**: 列の集約と status(D-145)。レビューの指摘で2回形を変えた。処置の文面は誰にも読まれていない。
  - **低**: レート枠による停止の述語(D-128 / D-135)。定義は実行ファイルのスキーマから読んだもので、実物で試していない。
- **Open questions / points for the human to decide**:
  - AR-130: 実行時の `apiKeySource` 検証を課さない判断(D-087)を、この差分でも維持するか。
  - AR-134 の dispute。
  - `proposed` のレンズ L-DES-ADR / L-DES-FAILURE-WALK を採るか。今回のラウンドでは適用していない。
