# 実測ログ: ブラウザからのテスト実施と結果閲覧(E2E)

R-12(C-012)の「ローカル起動した Web UI からテストを実施し、結果を閲覧でき、N-of-M の問題は
ちょうど N 件のときだけ送れる」ことを、実際のブラウザで通しで操作して確かめた記録(ADR-012)。
node と Next.js はサンドボックスイメージに無いので、ホストで1回走らせた。
`tests/measurements/test_host_check_logs.py` がこの記録を機械判定する。

ローカル API(`python -m quiz_sift.server`)は使い捨ての作業ルートに空きポートで立て、架空の5問の
デッキ(NFR-2 の対象外。q5 は5択2正解)を出題させた。5択2正解の問題では、選択が1件・3件のとき
「回答する」が押せず、押しても記録が増えないこと、2件で送れたことを確かめた(ADR-021)。
Next.js はこの木の `web/` を一時ディレクトリへ写し、
`QUIZ_SIFT_API` をその API に向けて `next build` してから `next start` で立てた(rewrites は
ビルド時に固定されるため)。
ブラウザは Playwright が置いた chromium をヘッドレスで起動し、`adapters/browser_cdp.CdpSession`
で CDP 越しにクリックした。スクリーンショットは `docs/test/measurements/ui-e2e-nofm/` にコミットしてある(D-080)。
画像が追跡対象になるので、E2E は使い捨ての作業ルートに置いた架空のデッキに対してだけ走らせる(D-081)。
リポジトリの `data/`・`out/` は読まない。

## 実行条件

| 項目 | 値 |
|---|---|
| コマンド | `PYTHONPATH=src python tests/measurements/test_host_check_logs.py ui-e2e --node-modules /mnt/d/prog_works/quiz-sift/web/node_modules` |
| 実行日時 | 2026-10-07T15:04:17+09:00 |
| コミット | `9dfd0bace658` |
| ブラウザ | Chrome/149.0.7827.55 |
| Next.js | 16.3.4(この木の `web/` を写して `next build`、BUILD_ID `3ko5HkEQlSCGvmZUGM5mE`) |
| 実行環境 | ホスト(Linux 6.6.87.2-microsoft-standard-WSL2) |

## 終了コード

`0`

## 手順

| # | 手順 | 結果 | 確認したこと |
|---|---|---|---|
| 1 | デッキ一覧を開く | ok | 一覧に「架空デッキ」が出た |
| 2 | デッキを開いて事前テストを始める | ok | セッション 20261007T060410-24c6bf の画面へ遷移した |
| 3 | 事前テストの全問に先頭から N 個の選択肢で答える | ok | 採点表示 q1:正解, q2:不正解, q3:不正解, q4:不正解, q5:不正解 |
| 4 | デッキへ戻って事後テストを始める | ok | セッション 20261007T060412-e4416f の画面へ遷移した |
| 5 | 事後テストの全問に正解で答える | ok | 採点表示 q1:正解, q2:正解, q3:正解, q4:正解, q5:正解 |
| 6 | 5択2正解の問題は「2 個選択」とチェックボックスで出題され、1件・3件では送れず2件で送れた | ok | 「2 個選択」・チェックボックス 5 個、1 件: 送信不可、3 件: 送信不可、2 件: 送信して記録された |
| 7 | 事前・事後を選んでレポートを開く | ok | レポートに 20.0% (1/5), 100.0% (5/5), +80.0pt が出た |
| 8 | 回答がブラウザ内ストレージでなくサーバ側のストアにある | ok | localStorage + sessionStorage = 0 件、API の回答数 {'20261007T060410-24c6bf': 5, '20261007T060412-e4416f': 5}、ストア out/learning.sqlite3 が存在 |

## スクリーンショット

| パス | digest |
|---|---|
| `docs/test/measurements/ui-e2e-nofm/01-decks.png` | `sha256:c7abb7269052de6c46c719fc67881cc5777cd8f2567a3918cd06e916bb764770` |
| `docs/test/measurements/ui-e2e-nofm/02-graded.png` | `sha256:432af9897e0ae4690fc060ca7e3036dabc47f8535eccc54517be1599ef034b4f` |
| `docs/test/measurements/ui-e2e-nofm/03-session-done.png` | `sha256:161a67af06ebfb3136d190d168b77bd06208ba274dd66f3204cb17bb70c36860` |
| `docs/test/measurements/ui-e2e-nofm/04-nofm-under.png` | `sha256:795b1cd0249af784e379002be4f2d8903a09f9ac154a614b8018fb06b58ff7e1` |
| `docs/test/measurements/ui-e2e-nofm/05-nofm-over.png` | `sha256:692a5d1157246af67b0a196ba18e9e3f9e419218b5f02138deee0352bce659a7` |
| `docs/test/measurements/ui-e2e-nofm/06-nofm-graded.png` | `sha256:fa4212aaf9a57f8d12e76adf4a1fe3b7b3139d920f88822a0a64b8c506ee713d` |
| `docs/test/measurements/ui-e2e-nofm/07-report.png` | `sha256:8fcf102e1bdd50b345a75fa7d90058741a99442533415a074c6c08c25082d4a5` |
