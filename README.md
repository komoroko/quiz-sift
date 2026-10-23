# quiz-sift

4択問題集を AI が分析し、背後にある知識・出題パターン・紛らわしい選択肢の違いへ圧縮したうえで
音声化する。机に向かえない時間を復習に使えるようにし、学習の前後で正答率を測定して記録する。

- 要件: [docs/10-requirements.md](docs/10-requirements.md)
- 設計: [docs/20-design.md](docs/20-design.md)
- 開発の進め方: Loose Rein(`.rein/AGENTS.rein.md`)

## 開発

```bash
make install      # uv を入れる
uv sync           # 依存を同期
make pre-commit   # ruff / mypy
make test         # pytest
```
