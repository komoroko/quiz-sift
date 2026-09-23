# 実測ログ: ゴールデンセットの正解ラベル一致率

R-2(C-002)の「ゴールデンセット10件に対する正解ラベル一致率が 100% で、正解判定の根拠の要素種別が
全問で同一である」ことの実測(受入基準 A-2 / A-3、D-048)。人手ラベルは人が結果ページで正解を確かめて
付けたもので、抽出ラベルは `extract.udemy.extract` が保存済みの生 HTML から同定したもの。
問題 ID の `p001-qNNN` は結果ページの NNN 問目。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_extraction_logs.py golden --out-root /mnt/d/prog_works/quiz-sift --label p001-q001=C --label p001-q003=D --label p001-q004=C --label p001-q005=A --label p001-q006=C --label p001-q007=B --label p001-q010=A --label p001-q026=C --label p001-q028=D --label p001-q031=A` |
| 実行日時 | 2026-09-24T06:37:25+09:00 |
| コミット | `9f890232b4de` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |

## 一致率

`10/10`

## ゴールデンセット

| 問題 ID | 受験者の選択 | 人手ラベル | 抽出ラベル | selector_kind | 判定 |
|---|---|---|---|---|---|
| p001-q001 | B | C | C | correct-mark | 一致 |
| p001-q003 | B | D | D | correct-mark | 一致 |
| p001-q004 | C | C | C | correct-mark | 一致 |
| p001-q005 | C | A | A | correct-mark | 一致 |
| p001-q006 | B | C | C | correct-mark | 一致 |
| p001-q007 | D | B | B | correct-mark | 一致 |
| p001-q010 | A | A | A | correct-mark | 一致 |
| p001-q026 | C | C | C | correct-mark | 一致 |
| p001-q028 | D | D | D | correct-mark | 一致 |
| p001-q031 | A | A | A | correct-mark | 一致 |
