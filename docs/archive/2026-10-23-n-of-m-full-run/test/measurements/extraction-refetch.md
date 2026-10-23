# 実測ログ: フル処理に使うデッキの出どころ

R-2(C-002)の T-059: フル処理に使うデッキの全問数と (N, M) の組ごとの件数、期待件数に届かなかった件数と
その理由の記録。デッキは保存済みの生 HTML から抽出した問題で、全問数は抽出できた問題の数。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_extraction_logs.py deck-source --out-root . --decision not-needed --reason -` |
| 実行日時 | 2026-10-08T08:26:17+09:00 |
| コミット | `edab9e02db6f` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |

## 判断

取り直し不要。再抽出の記録 `docs/test/measurements/extraction-rerun.md` が期待件数に届いている。

## 件数

| 項目 | 件数 |
|---|---|
| デッキの全問数 | 190 |

## (N, M) の組ごとの件数

| N | M | 件数 |
|---|---|---|
| 1 | 4 | 175 |
| 2 | 5 | 13 |
| 3 | 6 | 2 |

## 期待件数との差

| 項目 | 期待 | 実際 | 不足 |
|---|---|---|---|
| 全問数 | 190 | 190 | 0 |
| (2, 5) | 13 | 13 | 0 |
| (3, 6) | 2 | 2 | 0 |

## 不足の理由

-
