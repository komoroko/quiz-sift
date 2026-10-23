# Test Plan

> `/verify` は、この計画に沿って機能テスト(不具合)と非機能要件のテストを走らせ、結果をここに記録する。
> **acceptance** で、人がリリースを判断する材料になる。

測定の条件:
- 対象の HEAD は `58b7347` で、製品のツリーは `e6bd167`(T-053 のマージ)と同じ。
- 実行日は 2026-09-30。
- 品質ゲートの6ステップ(`bash gate.sh lint|format|types|test|docs|smoke`)を、品質ゲート用のイメージ(`sha256:1a1da729…`)の中で走らせた。ネットワークは切ってある(`--network none`)。
- 結果は6ステップとも exit 0。test は 398 passed、5 skipped。

## 1. Functional tests (requirement satisfaction)

- claim ごとの受入テストは `tests/acceptance/test_*.py::test_cNNN_*` にある(R-9 は欠番)。
- 下の表の「結果」は、上に書いたサンドボックスでの実測。
- grounded review の判定は `.rein/review.yaml` にあり、どの claim も `aligned` には届いていない(§2 の後)。この表と取り違えないこと。

| Requirement | What to check | Means (auto/manual) | Result | Notes |
|------|----------|-------------------|------|------|
| R-1 | 100問以上を取り込み、全フィールドが残ること。既習フラグの解決順(C-001) | auto `test_c001_*` | ✅ | |
| R-2 | Udemy 抽出のゴールデンセットで正解ラベルが一致すること(C-002) | auto `test_c002_*` | ✅ | 実ページの抽出は過去のサイクルで測った(`measurements/extraction-run.md`、`golden-set.md`) |
| R-3 | 排他クラスタ。M/N・比率が記録され、既知5グループが上限内に収まること(C-003) | auto `test_c003_*` | ✅ | 実データは列 591e7bb9 のクラスタ段の値が `measurements/full-run.md` にある |
| R-4 | 想起キーと誤答選択肢が元データに実在すること(C-004) | auto `test_c004_*` | ✅ | **実データでは RecallRejected 175/175**(§4 の DEF-1) |
| R-5 | グロッサリに矛盾が0件。照合対象なしを別扱いすること(C-005) | auto `test_c005_*` | ✅ | |
| R-6 | 台本が「問い→間→答え→識別」の順であること(C-006) | auto `test_c006_*` | ✅ | |
| R-7 | 音声を書き出し、3秒以上の無音を機械判定すること。連番と m3u(C-007) | auto `test_c007_*` | ✅ | 実 TTS は torch 2.10 では動かしていない(§3) |
| R-8 | 4択の出題で正誤と時間を記録。シャッフルしても正答率が正しいこと(C-008) | auto `test_c008_*` | ✅ | |
| R-10 | 誤答率順の再生リスト。全問正解のとき「該当なし」を出すこと(C-010) | auto `test_c010_*` | ✅ | |
| R-11 | 複数セットを分離できること(C-011) | auto `test_c011_*` | ✅ | |
| R-12 | Web UI の学習記録がサーバ側のストアに入ること(C-012) | auto `test_c012_*`、UI e2e | ✅ | `measurements/ui-e2e.md` |

Legend: ✅ pass / ❌ fail / ⬜ not run

## 2. Non-functional requirement tests (criteria checklist)

| NFR | What to check (criterion) | Means (auto/manual) | Result | Notes |
|-----|---------------------------|---------------------|--------|-------|
| NFR-1 | 矛盾したら成果物を出さずに止まること。含意判定(C-013) | auto `test_c013_*` | ✅ | T-051 で含意判定を Opus に移した(D-163) |
| NFR-2 | 教材の本文が Git の追跡対象・コミット・ログに入らないこと(C-014) | auto `test_c014_*`、レビュー | ✅ | SEC-003(言い換えがマスクされない。low)が残る |
| NFR-3 | 認証情報が平文で存在しないこと(C-015) | auto `test_c015_*`、gitleaks | ✅ | |
| NFR-4 | 途中から再実行したとき、前の段で LLM が呼ばれないこと(C-016) | auto `test_c016_*` | ✅ | |
| NFR-5 | CI と品質ゲートが同じステップであること(C-017) | auto `test_c017_*`、gate 6ステップ | ✅ | 0.12 への移行で一度赤になり、T-053 で直した |
| NFR-6 | ゲートがネットワークなしで通ること(C-018) | auto `test_c018_*`、`--network none` で実測 | ✅ | |
| NFR-7 | 処理のコストと所要時間が見えること(C-019) | auto(T-037 から T-045 までの受入テスト)、`measurements/full-run.md` | ⚠️ 途中まで | フル処理が完走していないので、列の完走での判定はまだ(D-164、次のサイクル) |
| NFR-8 | 従量課金の経路を取らないこと(C-020) | auto(T-039、T-044、T-047 の受入テスト) | ✅ | 実行した9回とも `metered_env_names` は空 |

### Performance
- [ ] 100〜200問のフル処理の所要時間。実測はクラスタ段まで(`full-run.md`)。完走での測定は次のサイクルに回す。

### Security (mandatory in `/verify`)

- [x] 持ち越したレビューが下の表に書いてある。
- [x] 依存の監査: Python は `rein audit run`(pip-audit)、frontend は `npm audit` で実行した。
- [x] 秘密を平文で保存・ログ出力していない。コミットの段階で gitleaks が止める。
- [x] 入力の検証。Udemy 由来の本文は子プロセスに stdin で渡し、ツールは拒否リストで止めている。拒否リストである弱さは SEC-001 に書いた。

| Check | Result | Severity | Taken against (commit / date) | Notes |
|------|------|--------|------|------|
| acceptance のセキュリティレビュー(持ち越し) | ✅ blocking 0 件 | medium 1 / low 2 | `subject_head_sha` e6bd167、2026-09-30 | SEC-001 から SEC-003(§4) |
| 依存の監査(Python、`uv run pip-audit`) | ✅ passed | — | 13f5d43、2026-09-30 | manifest と lock はそれ以降変えていない |
| 依存の監査(frontend、`npm audit --package-lock-only`、`web/`) | ✅ 0 件 | — | 58b7347、2026-09-30 | `rein audit run` の対象外。ホストで手動実行した |

### Reliability / operations
- [x] エラー時の振る舞いが定義どおり(D-042: contradicted か uncertain なら止まる。レート枠を超えたら止まる)。
- [x] 必要な情報がログと meter に出る(NFR-7 の終了行のキー集合を T-047 の A-1 で確かめた)。

## 3. Manual verification checklist (human-run acceptance)

| Check | How | Result | Notes |
|-------|-----|--------|-------|
| 実 TTS(CosyVoice)が torch 2.10 で動く | `--tts-model` を渡して audio 段を1クラスタ走らせる | ⬜ | T-052 で torch を下げてからは動かしていない |
| 100〜200問のフル処理の完走(元の T-047) | `--fresh-run cluster` から `audio` まで | ⬜ | 想起の段で止まる(DEF-1)。次のサイクルに回す |
| 出力の質(想起素材・台本)を目で見る | 生成物を読む | ⬜ | 想起の段より先の成果物が実データではまだ無い |
| 対応 OS | WSL2(Ubuntu)だけ | ✅ | ほかの OS は対象外 |

Legend: ✅ pass / ❌ fail / ⬜ not run

## 4. Defects found
| ID | Content | Severity | Task | Status |
|----|------|--------|-----------|------|
| DEF-1 | 実データの想起の段が RecallRejected で止まる(ungrounded_keys 175/175、missing_options 47)。`RECALL_SYSTEM` の指示と `recall/verify.py` の実在検査が噛み合っていない | high | 次のサイクル(D-164) | open |
| DEF-2 | 含意判定(haiku)が、一致しているものを contradicted と誤判定した(32件中1件) | high | T-051 | fixed |
| DEF-3 | setuptools 81 に PYSEC-2026-3447 | medium | T-052 | fixed |
| DEF-4 | loose-rein 0.12 への移行後、C-017 と C-018 の受入テストが `KeyError: 'kind'` で落ちた | medium | T-053 | fixed |
| SEC-001 | 子プロセスの設定は起動前の検査だけで防いでいて、最小ホームの settings 経由で `env` などを差し込める余地がある | medium | 次のサイクル | open(non-blocking) |
| SEC-002 | タイムアウト時に孫プロセスが pipe を持ち続けると、`communicate()` が戻らない | low | 次のサイクル | open(non-blocking) |
| SEC-003 | 子の stdout を INFO ログに出しており、言い換えた教材の文面はマスクされない | low | 次のサイクル | open(non-blocking) |

## 5. Overall judgment (filled by the human)
- **Release decision**: hold / go / conditional go
- **Remaining issues**:
