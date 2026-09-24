# 実測ログ: Udemy からの実取得

R-2(C-002)の「Udemy のページからヘッドレスブラウザで抽出すると、成功件数と失敗箇所が報告される」
ことを、実物の結果ページに対して1回走らせた記録(受入基準 A-1)。取得はログイン済みの Chrome に
CDP で接続して行った(ADR-007、`adapters/browser_cdp.fetch`)。Udemy へのアクセスは戻せないので、
`rein approve T-019` の承認のもとで1回だけ実行した。

取得は `tests/measurements/test_extraction_logs.py fetch` が子プロセス(`fetch-run`)として走らせ、
その argv・標準出力・標準エラー・終了コードを加工せずに転記している。生 HTML・問題 JSONL・根拠レポートは
gitignore 対象の `out/udemy/` にあり、ここには本文を載せない(NFR-2)。
`tests/measurements/test_extraction_logs.py` がこの記録を機械判定する。

## 実行条件

| 項目 | 値 |
|---|---|
| 実行日時 | 2026-09-24T05:59:03+09:00 |
| コミット | `f1a17a991b70` |
| 対象 | 演習テストの結果ページ1枚 |

## argv

```text
/mnt/d/prog_works/quiz-sift/.venv/bin/python
/mnt/d/prog_works/quiz-sift/.worktrees/T-019/tests/measurements/test_extraction_logs.py
fetch-run
--out-root
/mnt/d/prog_works/quiz-sift
--url
https://www.udemy.com/course/awsgenerative-ai-developer-professional-aip-c01-j/learn/quiz/7600618/result/1802860553
```

## 終了コード

`0`

## 標準出力

```text
url=https://www.udemy.com/course/awsgenerative-ai-developer-professional-aip-c01-j/learn/quiz/7600618/result/1802860553
fetch_seconds=12.4
raw_digest=sha256:9fd31dd227e4bd024ed181419dcb14971998c040749f8541931da606be33f631
question_blocks=190
extracted=175
failed=15
with_explanation=175
without_explanation=0
selector_kinds=correct-mark
extraction_failure=1 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[3]/div[1]
extraction_failure=11 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[13]/div[1]
extraction_failure=23 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[25]/div[1]
extraction_failure=26 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[28]/div[1]
extraction_failure=40 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[42]/div[1]
extraction_failure=44 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[46]/div[1]
extraction_failure=75 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[77]/div[1]
extraction_failure=76 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[78]/div[1]
extraction_failure=82 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[84]/div[1]
extraction_failure=100 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[102]/div[1]
extraction_failure=112 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[114]/div[1]
extraction_failure=124 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[126]/div[1]
extraction_failure=151 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[153]/div[1]
extraction_failure=155 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[157]/div[1]
extraction_failure=166 options /html[1]/body[1]/div[1]/div[1]/div[1]/div[1]/main[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/div[3]/div[168]/div[1]
wrote=out/udemy/aip-c01/questions.jsonl,out/udemy/aip-c01/extract-report.json
```

## 標準エラー

```text
```
