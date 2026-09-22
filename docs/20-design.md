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
| LLM | Anthropic `claude-opus-5`、`ports/llm.py` のタスク別メソッド越し | ADR-002 |
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

- **Required features/modules**: `ports/llm.py`, `cluster/assign.py`, `cluster/metrics.py`, `cluster/summary.py`, `cluster/grounding.py`
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
  API 呼び出し回数が1回分の2倍になることを確認する。
  要約の語句実在性は `cluster/grounding.py` が名詞句を切り出して照合する(R-4 と共有。重複実装を
  作らない)。**形態素解析だけでは足りない**: 実測(2026-09-22)で fugashi + unidic-lite は
  「Amazon EventBridge のルールで S3 のイベントを捕捉する」から Amazon / EventBridge / ルール /
  イベント / 捕捉 を正しく取ったが、**`S3` を `S` と `3` に分割した**。AWS のサービス名は
  この形(英字 + 数字)が主なので、大文字略語のヒューリスティックは補助ではなく**必須**(D-056)。
- **Existing assets to reuse**: `statistics`, `collections`, `itertools`。R-4 と `grounding` を共有。
- **Corresponding technical choice**: ADR-003, ADR-010
- **証拠種別**: 退化ガードと negative control、メトリクスの計算は `command`。
  **キャッシュ迂回2回の割当一致率は `artifact`**(D-030。スタブは決定的なので一致率が常に 100% になり、
  ゲートでは測る意味が消える)。
- **What this requires of a person**:
  (1) Anthropic API キー。
  (2) **退化ガード用の合成フィクスチャ(5グループ×3問以上)と、退化スタブ2本を書き、
  そのグループ構造が「既知の正解」であることを保証すること**。
  (3) 実データの M/N と最大クラスタ比率、および警告を読んで圧縮の質を判定すること。
- **Reversibility**: コードと成果物は **可逆**。ただし **LLM API の呼び出しは課金**であり、
  フル処理とキャッシュ迂回の2回再実行はその分だけ**誰かが課金される=不可逆**。
  **→ 実 API を叩くタスクを分離し、`reversible: false` とする。**

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
- **Existing assets to reuse**: `difflib`, `collections.Counter`。`cluster/grounding.py` を共有。
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
- **Existing assets to reuse**: `cluster/grounding.py`, `safety/entailment.py`, `audio/naming.py`
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
「実際に動く」側は `artifact` へ降ろした。

実現性は 2026-09-22 に実測済み(`docs/speculative-work.md` に記録)。`--network none` で
fugashi + unidic-lite の形態素解析、ffmpeg `silencedetect` による mp3 の無音検出、
Playwright chromium の起動を個別に確認した(最後のものは結局イメージに入れない)。

### NFR-7: 処理コストと所要時間の可視化

`pipeline/meter.py` が実行ごとに `run_id, elapsed_sec, calls_by_model, estimated_cost` を
JSON Lines で記録する。**トークンは input / output の2値に畳まない**(AR-29): ADR-002 が
adaptive thinking を既定にしているので出力側に思考トークンが乗り、プロンプトキャッシュを使えば
入力側は `cache_creation_input_tokens` / `cache_read_input_tokens` で単価が別になる。
2値に畳むと推定コストが系統的にずれ、D-021 が「次サイクルで上限を決める材料」と位置づけた
その材料が歪む。単価表も同じ粒度で持つ。

単価表はコード内定数で、`claude-opus-5` は入力 $5.00 / 出力 $25.00 per 1M、`priced_at` に取得日を
持たせる。**TTS はローカル OSS なのでコストが乗らない**(ADR-004)。上限は初回実測後(D-021)。

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

## Open questions

- **NFR-1 の判定能力が未測定のまま mandate を通る**。手書きフィクスチャは判定器のロジックを守るが、
  モデルが実際に矛盾を見つけられるかは本サイクルでは誰も測らない。誤学習は本人が気づけない種類の
  失敗なので、次サイクルで実測を入れるかは残る課題。
- **CosyVoice のホストへの導入手順が未定**。pip では入らず(実測)、git clone + サブモジュール +
  torch が要る。`/tasks` で手順を決める。
- **`web/` の依存管理が未設計**。node と Playwright をイメージから外したので、`npm ci` と
  `next build` はホスト側の手順になる。`/tasks` で決める。
- **`L-DES-ADR` レンズが未適用**。条件(ADR に却下オプションがあるか)は機械的に判定でき、
  12 本の ADR がいずれも該当するが、`proposed` の採否は人間が mandate で決めるもの。
  採用するなら「選んだ案の短所が、却下した案の短所に比べて過小に書かれていないか」が見られる。

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


## Self-assessment (assumptions, confidence)

- **Assumptions made**:
  - Udemy のページ構造は HTML から解析できる形になっている。**未検証**。R-2 の前提。
  - CDP 接続が Udemy のボット検知を回避できる。**未検証**。
  - CosyVoice の文内コードスイッチが AWS のサービス名混在に耐える。**未検証**(公称値のみ)。
  - CosyVoice をホストに導入できる。**未検証** — pip で入らないことだけは実測済み。
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
- **Open questions / points for the human to decide**:
  - 上の Open questions 4点。
  - `/tasks` で分解した結果が現実的な規模に収まらない場合の、スコープ縮小の判断。
  - **2巡目の修正が独立に検証されていない**: 再招集枠は使い切った。AR-5 / AR-6 / AR-8 / N-1 / N-2 /
    N-3 の最終修正はリードが適用したままレビューを受けていない(N-1 と S-2 のテストが赤くなることは
    手元で確認済み)。
  - **ガードの境界を2度越えた**: `src/` と `scripts/` に、Bash のヒアドキュメントで
    PreToolUse フックを素通りして書き込んだ。どちらも是正済みだが、同じ機構で再発しうる。
