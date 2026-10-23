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
| 実行日時 | 2026-10-07T15:25:33+09:00 |
| コミット | `519a23ec13be` |
| ブラウザ | Chrome/149.0.7827.55 |
| Next.js | 16.3.4(この木の `web/` を写して `next build`、BUILD_ID `Cz6rnbk9jbeeIcHSfisNQ`) |
| web/ の digest | `sha256:3f0890036e9531cec7a03d87f85b10f80675e6da33b0956f1bd1172de8e914b5` |
| 実行環境 | ホスト(Linux 6.6.87.2-microsoft-standard-WSL2) |

## 終了コード

`0`

## 手順

| # | 手順 | 結果 | 確認したこと |
|---|---|---|---|
| 1 | デッキ一覧を開く | ok | 一覧に「架空デッキ」が出た |
| 2 | デッキを開いて事前テストを始める | ok | セッション 20261007T062524-a58173 の画面へ遷移した |
| 3 | 事前テストの全問に先頭から N 個の選択肢で答える | ok | 採点表示 q1:正解, q2:不正解, q3:不正解, q4:不正解, q5:不正解 |
| 4 | デッキへ戻って事後テストを始める | ok | セッション 20261007T062527-de03e6 の画面へ遷移した |
| 5 | 事後テストの全問に正解で答える | ok | 採点表示 q1:正解, q2:正解, q3:正解, q4:正解, q5:正解 |
| 6 | 5択2正解の問題は「2 個選択」とチェックボックスで出題され、1件・3件では送れず2件で送れた(2度押しても送信は1回) | ok | 「2 個選択」・チェックボックス 5 個、1 件: 送信不可、3 件: 送信不可、2 件: 2度押しで送信して記録は1件 |
| 7 | 事前・事後を選んでレポートを開く | ok | レポートに 20.0% (1/5), 100.0% (5/5), +80.0pt が出た |
| 8 | 回答がブラウザ内ストレージでなくサーバ側のストアにある | ok | localStorage + sessionStorage = 0 件、API の回答数 {'20261007T062524-a58173': 5, '20261007T062527-de03e6': 5}、ストア out/learning.sqlite3 が存在 |

## スクリーンショット

| パス | digest |
|---|---|
| `docs/test/measurements/ui-e2e-nofm/01-decks.png` | `sha256:c7abb7269052de6c46c719fc67881cc5777cd8f2567a3918cd06e916bb764770` |
| `docs/test/measurements/ui-e2e-nofm/02-graded.png` | `sha256:b3f8f133508f0905d5b01e67a9ab17d00b1ce1a45924c9045f519356328867e1` |
| `docs/test/measurements/ui-e2e-nofm/03-session-done.png` | `sha256:161a67af06ebfb3136d190d168b77bd06208ba274dd66f3204cb17bb70c36860` |
| `docs/test/measurements/ui-e2e-nofm/04-nofm-under.png` | `sha256:852d4e10cc4b3bb032ed63494be46adbb79d68f6cdb5ef9549c56334c56f8a65` |
| `docs/test/measurements/ui-e2e-nofm/05-nofm-over.png` | `sha256:7625a65f27cfd8379ee4525020238026d83d8e7fa5d448a19a0c88badde3adc1` |
| `docs/test/measurements/ui-e2e-nofm/06-nofm-graded.png` | `sha256:75acb4b9a3b310c4dcbf99bb95fb89419059e3650d34cc3c44460976887f46d5` |
| `docs/test/measurements/ui-e2e-nofm/07-report.png` | `sha256:a68ae47458136bb716611c4423c06bac31907f4f6f59e74849b9e8eadeb34ef2` |
