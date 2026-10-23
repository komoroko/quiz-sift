# 実測ログ: ゴールデンセットの正解の集合の一致率

R-2(C-002)の「人手で確認した N-of-M を3件以上加えたゴールデンセット全体で、正解の集合の一致率が 100% である」
ことの実測(D-048)。表の「人手ラベル」列は外から渡した正解の集合で、誰が何を見て読んだかは実行条件の
「ラベルの出所」に記す。抽出ラベルは `extract.udemy.extract` が保存済みの生 HTML から同定した正解の集合。
選択肢記号はカンマで区切り、`-` は受験者が何も選ばなかったことを表す。
問題 ID の `pPPP-qNNN` は PPP 枚目の結果ページの NNN 問目。本文は載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_extraction_logs.py golden --out-root . --label p001-q002=B,D,E --label p001-q083=A,C,F --label p001-q012=A,C --label p001-q125=B,D --label p001-q001=C --label p001-q003=D --label-source ラベルは Claude が golden-candidates の抽出結果から組み立て、リポジトリの所有者が Udemy の結果ページの正解の印と照らして問題ないと申告して承認した(2026-10-08)。抽出結果と独立に読んだラベルではない` |
| 実行日時 | 2026-10-08T08:26:12+09:00 |
| コミット | `edab9e02db6f` |
| 生 HTML | `out/udemy/aip-c01/raw/page-001.html` (`sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631`) |
| ラベルの出所 | ラベルは Claude が golden-candidates の抽出結果から組み立て、リポジトリの所有者が Udemy の結果ページの正解の印と照らして問題ないと申告して承認した(2026-10-08)。抽出結果と独立に読んだラベルではない |

## 一致率

`6/6`

## ゴールデンセット

| 問題 ID | 受験者の選択 | 人手ラベル | 抽出ラベル | selector_kind | 判定 |
|---|---|---|---|---|---|
| p001-q002 | C | B,D,E | B,D,E | correct-mark | 一致 |
| p001-q083 | F | A,C,F | A,C,F | correct-mark | 一致 |
| p001-q012 | E | A,C | A,C | correct-mark | 一致 |
| p001-q125 | B | B,D | B,D | correct-mark | 一致 |
| p001-q001 | B | C | C | correct-mark | 一致 |
| p001-q003 | B | D | D | correct-mark | 一致 |
