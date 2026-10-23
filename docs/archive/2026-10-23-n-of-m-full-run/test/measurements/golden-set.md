# 実測ログ: ゴールデンセットの正解ラベル一致率

R-2(C-002)の「ゴールデンセット10件に対する正解ラベル一致率が 100% で、正解判定の根拠の要素種別が
全問で同一である」ことの実測(受入基準 A-2 / A-3、D-048)。表の「人手ラベル」列は外から渡した正解ラベルで、
誰が何を見て読んだかは実行条件の「ラベルの出所」に記す。抽出ラベルは `extract.udemy.extract` が保存済みの
生 HTML から同定したもの。
問題 ID の `p001-qNNN` は結果ページの NNN 問目。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src /mnt/d/prog_works/quiz-sift/.venv/bin/python tests/measurements/test_extraction_logs.py golden --out-root /mnt/d/prog_works/quiz-sift --label p001-q001=C --label p001-q003=D --label p001-q004=C --label p001-q005=A --label p001-q006=C --label p001-q007=B --label p001-q010=A --label p001-q026=C --label p001-q028=D --label p001-q031=A --label-source 人が目視で確認した。2026-09-24、リポジトリの持ち主が自分のログイン済みブラウザで演習テストの結果ページを開き、1・3・4・5・6・7・10・26・28・31 問目の正解を画面上の表示から目視で確認したうえで「全部あっていました」と回答した。確認は抽出ラベルを見ない形では行われていない(本人は以前の実行でこの --label の値を発行している。値はコミット 4952646 のものを据え置いた)。` |
| 実行日時 | 2026-09-24T21:32:04+09:00 |
| コミット | `0f93ad34b17e` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |
| ラベルの出所 | 人が目視で確認した。2026-09-24、リポジトリの持ち主が自分のログイン済みブラウザで演習テストの結果ページを開き、1・3・4・5・6・7・10・26・28・31 問目の正解を画面上の表示から目視で確認したうえで「全部あっていました」と回答した。確認は抽出ラベルを見ない形では行われていない(本人は以前の実行でこの --label の値を発行している。値はコミット 4952646 のものを据え置いた)。 |

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
