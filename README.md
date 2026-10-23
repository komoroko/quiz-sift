# quiz-sift

4択問題集を AI が分析し、背後にある知識・出題パターン・紛らわしい選択肢の違いへ圧縮したうえで
音声化する。机に向かえない時間を復習に使えるようにし、学習の前後で正答率を測定して記録する。

- 要件: [docs/10-requirements.md](docs/10-requirements.md)
- 設計: [docs/20-design.md](docs/20-design.md)
- 開発の進め方: Loose Rein(`.rein/AGENTS.rein.md`)

## 使い方

### 準備

```bash
uv sync                                   # 依存を同期
pnpm -C web install                       # Web UI を使うとき
```

- LLM は `claude -p`(Claude Code の CLI)を子プロセスで呼ぶ。`claude` が PATH にあり、ログイン済みであること。
  API キーは使わず、サブスクリプションの枠を消費する。
- 音声を作るときだけ、CosyVoice と TTS の依存が要る(後述)。

パッケージはインストールされないので、`src/` を `PYTHONPATH` に通して起動する。

```bash
export PYTHONPATH=src
uv run python -m quiz_sift.cli --help
```

### 作業ルートとデッキ

すべての段は作業ルート(`--root`、既定はカレントディレクトリ)の `data/` を読み、`out/` へ書く。
`out/` が作業ルートの `.gitignore` の対象でないと書き出しを拒む(問題文を git に載せないため)。

```text
<root>/
  .gitignore              # data/ と out/ を書く
  data/<deck>/
    deck.yaml             # deck_id(ディレクトリ名と同じ)・title・language(ja / en)
    *.jsonl               # 1行1問: id・stem・options[{id,text}]・answers[選択肢 ID]・explanation
  out/<deck>/             # 成果物(段ごとのキャッシュ・音声・学習記録)
```

問題は M 択(M ∈ {4, 5, 6})・正解 N 件(1 ≤ N < M)。形式の例は
[tests/fixtures/decks/synthetic-nofm/](tests/fixtures/decks/synthetic-nofm/)(合成の16問、うち複数正解8問)にある。

### 段

`python -m quiz_sift.cli [--root DIR] <段> <deck> [オプション]`

| 段 | 内容 | LLM / TTS |
|---|---|---|
| `ingest` | 取り込み・正規化・重複除去 | なし |
| `cluster` | 出題の論点ごとにクラスタリングし、要約する | LLM |
| `recall` | 問題ごとの想起素材 | LLM |
| `glossary` | 用語集 | LLM |
| `script` | 台本 | なし(前段がキャッシュに無ければ前段の LLM を呼ぶ) |
| `audio` | 台本の音声 | TTS |
| `glossary-audio` | 用語集の音声 | TTS |
| `review` | 学習記録から誤答率の高い順の復習リスト | なし |
| `serve` | Web UI 用のローカル API(`127.0.0.1:8765`) | なし |

- 各段は前段の成果物を `out/<deck>/<段>/<digest>` のキャッシュから読み、無ければその場で作る。
  入力・プロンプト・モデルが同じなら作り直さない。
- LLM / TTS を呼びうる段は「実行の列」に記録される。`--fresh-run` で新しい列を始め(run_id は stderr に出る)、
  同じ列の続きは `--resume <run_id>` で足す。`recall` は問題ごとの途中結果を残すので、止まっても `--resume` で
  続きから進む。
  列の digest には run_id が入るので、列の成果物はその run_id を渡したときだけ読まれる。

デッキ全体を音声まで通す例:

```bash
R=<作業ルート>
uv run python -m quiz_sift.cli --root $R cluster  <deck> --fresh-run   # run_id を控える
uv run python -m quiz_sift.cli --root $R recall   <deck> --resume <run_id>
uv run python -m quiz_sift.cli --root $R glossary <deck> --resume <run_id>
uv run python -m quiz_sift.cli --root $R script   <deck> --resume <run_id>
```

### 音声

CosyVoice を `$HOME/CosyVoice` に clone してモデルを置く。CosyVoice は pip で配布されていないので、
本体と Matcha-TTS を `PYTHONPATH` に足し、TTS の依存は `--extra tts` で入れる(`uv run` に毎回付ける。
付けないと既定の依存に同期し直されて外れる)。

```bash
PYTHONPATH=src:$HOME/CosyVoice:$HOME/CosyVoice/third_party/Matcha-TTS \
  uv run --extra tts python -m quiz_sift.cli --root $R audio <deck> --resume <run_id> \
  --tts-model $HOME/CosyVoice/pretrained_models/CosyVoice-300M-SFT
```

音声は `out/<deck>/audio/`、用語集の音声(`glossary-audio`)は `out/<deck>/glossary/` に出る。
`--voice` を省くとデッキの言語の既定の話者になる。

### Web UI で解く

API と UI を別々の端末で起動し、http://localhost:3000 を開く。デッキの一覧・出題・採点・前後比較のレポートが
使え、回答は `out/` の学習記録に残る。出題は `data/` だけを読むので、LLM の段を通していなくても使える。

```bash
uv run python -m quiz_sift.cli --root $R serve   # 端末1
pnpm -C web dev                                   # 端末2(API の宛先は QUIZ_SIFT_API で変えられる)
```

## 開発

```bash
make install      # uv を入れる
uv sync           # 依存を同期
make pre-commit   # ruff / mypy
make test         # pytest
```
