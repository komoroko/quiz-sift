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
で CDP 越しにクリックした。スクリーンショットは `docs/test/measurements/ui-e2e/` にコミットしてある(D-080)。
画像が追跡対象になるので、E2E は使い捨ての作業ルートに置いた架空のデッキに対してだけ走らせる(D-081)。
リポジトリの `data/`・`out/` は読まない。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_host_check_logs.py ui-e2e --node-modules /mnt/d/prog_works/quiz-sift/web/node_modules` |
| 実行日時 | 2026-09-24T06:00:49+09:00 |
| コミット | `f1a17a991b70` |
| ブラウザ | Chrome/149.0.7827.55 |
| Next.js | 16.3.4(この木の `web/` を写して `next build`、BUILD_ID `FXt6V7nq_zhVkh3vWBDaA`) |
| 実行環境 | ホスト(Linux 6.6.87.2-microsoft-standard-WSL2) |

## 終了コード

`0`

## 手順

| # | 手順 | 結果 | 確認したこと |
|---|---|---|---|
| 1 | デッキ一覧を開く | ok | 一覧に「架空デッキ」が出た |
| 2 | デッキを開いて事前テストを始める | ok | セッション 20260923T210045-c84d03 の画面へ遷移した |
| 3 | 事前テストの全問に「選択肢 A」で答える | ok | 採点表示 q1:正解, q2:不正解, q3:不正解, q4:不正解 |
| 4 | デッキへ戻って事後テストを始める | ok | セッション 20260923T210047-68d090 の画面へ遷移した |
| 5 | 事後テストの全問に正解で答える | ok | 採点表示 q1:正解, q2:正解, q3:正解, q4:正解 |
| 6 | 事前・事後を選んでレポートを開く | ok | レポートに 25.0% (1/4), 100.0% (4/4), +75.0pt が出た |
| 7 | 回答がブラウザ内ストレージでなくサーバ側のストアにある | ok | localStorage + sessionStorage = 0 件、API の回答数 {'20260923T210045-c84d03': 4, '20260923T210047-68d090': 4}、ストア out/learning.sqlite3 が存在 |

## スクリーンショット

| パス | digest |
|---|---|
| `docs/test/measurements/ui-e2e/01-decks.png` | `sha256:c7abb7269052de6c46c719fc67881cc5777cd8f2567a3918cd06e916bb764770` |
| `docs/test/measurements/ui-e2e/02-graded.png` | `sha256:1dc263864925699843ed68fe6721a41a38f8c095b2767f7bca2561b63fcdd249` |
| `docs/test/measurements/ui-e2e/03-session-done.png` | `sha256:88d73c29f5d712bc726a1480e5498bba82adf73851b745d828c54a8a798880b5` |
| `docs/test/measurements/ui-e2e/04-report.png` | `sha256:1ef3258b1d2ede3c9d39a472551cbb078e72027b474dbb64333e28e2ef4ca424` |
