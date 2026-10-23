# 実測ログ: 保存済みの生 HTML の再抽出

R-2(C-002)の「保存済みの生 HTML の再抽出の報告に全問数と (N, M) の組ごとの件数が現れる」ことの記録。
`tests/measurements/test_extraction_logs.py rerun` が保存済みの生 HTML をオフラインで抽出し直し、件数だけを
書いた。全問数は問題ブロックの数で、抽出 + 抽出の失敗に等しい。推定経路は、正解マークが無く単一選択の問題で
受験者の選択を正解とした件数。問題文と選択肢の本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_extraction_logs.py rerun --out-root .` |
| 実行日時 | 2026-10-08T08:10:17+09:00 |
| コミット | `b29d8a64d865` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |

## 件数

| 項目 | 件数 |
|---|---|
| 全問数 | 190 |
| 抽出 | 190 |
| 抽出の失敗 | 0 |
| 取得の失敗 | 0 |
| 推定経路 | 0 |

## (N, M) の組ごとの件数

| N | M | 件数 |
|---|---|---|
| 1 | 4 | 175 |
| 2 | 5 | 13 |
| 3 | 6 | 2 |

## 抽出の失敗の理由ごとの件数

| 理由 | 件数 |
|---|---|
| stem | 0 |
| options | 0 |
| option_text | 0 |
| answer | 0 |
