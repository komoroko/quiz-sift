# 実測ログ: ブラウザからのテスト実施と結果閲覧(E2E)

R-12(C-012)の「ローカル起動した Web UI からテストを実施し、結果を閲覧できる」ことを、実際の
ブラウザで通しで操作して確かめた記録(ADR-012、受入基準 A-2)。node と Next.js はサンドボックス
イメージに無いので、ホストで1回走らせた。`tests/measurements/test_host_check_logs.py` がこの記録を
機械判定する。

ローカル API(`python -m quiz_sift.server`)は使い捨ての作業ルートに空きポートで立て、架空の4問の
デッキ(NFR-2 の対象外)を出題させた。Next.js はこの木の `web/` を一時ディレクトリへ写し、
`QUIZ_SIFT_API` をその API に向けて `next build` してから `next start` で立てた(rewrites は
ビルド時に固定されるため)。
ブラウザは Playwright が置いた chromium をヘッドレスで起動し、`adapters/browser_cdp.CdpSession`
で CDP 越しにクリックした。スクリーンショットは gitignore 対象の `out/ui-e2e/` にある。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_host_check_logs.py ui-e2e --node-modules /mnt/d/prog_works/quiz-sift/web/node_modules` |
| 実行日時 | 2026-09-23T22:56:17+09:00 |
| コミット | `21532f686efe` |
| ブラウザ | Chrome/149.0.7827.55 |
| Next.js | 16.3.4(この木の `web/` を写して `next build`、BUILD_ID `lwU6M5CGbfXWNVVYgShzL`) |
| 実行環境 | ホスト(Linux 6.6.87.2-microsoft-standard-WSL2) |

## 終了コード

`0`

## 手順

| # | 手順 | 結果 | 確認したこと |
|---|---|---|---|
| 1 | デッキ一覧を開く | ok | 一覧に「架空デッキ」が出た |
| 2 | デッキを開いて事前テストを始める | ok | セッション 20260923T135613-186557 の画面へ遷移した |
| 3 | 事前テストの全問に「選択肢 A」で答える | ok | 採点表示 q1:正解, q2:不正解, q3:不正解, q4:不正解 |
| 4 | デッキへ戻って事後テストを始める | ok | セッション 20260923T135615-42e55c の画面へ遷移した |
| 5 | 事後テストの全問に正解で答える | ok | 採点表示 q1:正解, q2:正解, q3:正解, q4:正解 |
| 6 | 事前・事後を選んでレポートを開く | ok | レポートに 25.0% (1/4), 100.0% (4/4), +75.0pt が出た |
| 7 | 回答がブラウザ内ストレージでなくサーバ側のストアにある | ok | localStorage + sessionStorage = 0 件、API の回答数 {'20260923T135613-186557': 4, '20260923T135615-42e55c': 4}、ストア out/learning.sqlite3 が存在 |

## スクリーンショット

| パス | digest |
|---|---|
| `out/ui-e2e/01-decks.png` | `sha256:c7abb7269052de6c46c719fc67881cc5777cd8f2567a3918cd06e916bb764770` |
| `out/ui-e2e/02-graded.png` | `sha256:37e20410aff3e5fdab598fd7c8a43dc3b9565d98916a5c99858fda7a5216a047` |
| `out/ui-e2e/03-session-done.png` | `sha256:161a67af06ebfb3136d190d168b77bd06208ba274dd66f3204cb17bb70c36860` |
| `out/ui-e2e/04-report.png` | `sha256:480f19084cd3e2b0b614ebdc9d2e342618270faff6ef228fbf8a7b7350179652` |
