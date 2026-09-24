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
| コマンド | `PYTHONPATH=src python tests/measurements/test_extraction_logs.py golden --out-root /mnt/d/prog_works/quiz-sift --label p001-q001=C --label p001-q003=D --label p001-q004=C --label p001-q005=A --label p001-q006=C --label p001-q007=B --label p001-q010=A --label p001-q026=C --label p001-q028=D --label p001-q031=A --label-source エージェントが読み取った。ログイン済み Chromium の結果ページに CDP の Runtime.evaluate でブラウザの DOM API を使い、各問題ブロックで answer-result-pane--answer-correct--* クラスが付いた選択肢の位置を A〜D に対応づけた。人は正解を読んでおらず、このラベルを載せたコマンドを発行しただけ。抽出器と同じ正解マーク(correct-mark)を別パーサで読んだものなので、10/10 はパーサ間の一致を示し、抽出器が読む信号の選び方の独立検証ではない(コミット 4952646)。` |
| 実行日時 | 2026-09-24T21:17:08+09:00 |
| コミット | `b5251a4d02ab` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |
| ラベルの出所 | エージェントが読み取った。ログイン済み Chromium の結果ページに CDP の Runtime.evaluate でブラウザの DOM API を使い、各問題ブロックで answer-result-pane--answer-correct--* クラスが付いた選択肢の位置を A〜D に対応づけた。人は正解を読んでおらず、このラベルを載せたコマンドを発行しただけ。抽出器と同じ正解マーク(correct-mark)を別パーサで読んだものなので、10/10 はパーサ間の一致を示し、抽出器が読む信号の選び方の独立検証ではない(コミット 4952646)。 |

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
