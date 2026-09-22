#!/usr/bin/env bash
# NFR-5 (d): CI の run ステップと品質ゲートが「同一のコマンド列」であることを、
# 同じスクリプトを両方から呼ぶことで構造的に保証する(AR-6)。
#
# リポジトリ直下に置いてあるのは、`.rein/config.yaml` の `guard.paths` が `scripts/` を
# 含むため。mandate 承認前に guard 対象パスへ置くのは規則違反になる(N-3)。
#
# 同一性の対象は **検査ステップだけ** で、環境を用意する手順(uv sync 等)は含まない。
# 品質ゲートはネットワーク遮断のサンドボックスで走り、依存はイメージに焼き込まれている
# ため、`uv sync` は原理的に走らない(NFR-6)。この線引きは要件の NFR-5 に明記してある。
set -euo pipefail

case "${1:-}" in
  lint)   ruff check . ;;
  format) ruff format --check . ;;
  types)  mypy . ;;
  test)   pytest -q ;;
  docs)   mkdocs build --strict ;;
  smoke)  python -c 'import sys; sys.path.insert(0, "src"); import quiz_sift; print(quiz_sift.__version__)' ;;
  *)      echo "usage: $0 {lint|format|types|test|docs|smoke}" >&2; exit 2 ;;
esac
