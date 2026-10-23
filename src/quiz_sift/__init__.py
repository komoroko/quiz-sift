"""quiz-sift: 4択問題集を、耳だけでも効率的に学習できる形へ AI で変換する。

このファイルは `quiz_sift` を**正規パッケージ**にするために要る(T-007 A-1)。
無いと `quiz_sift` は名前空間パッケージになり、`mypy .` が `src/quiz_sift/domain/ids.py`
を `domain.ids` と `quiz_sift.domain.ids` の二重に解決して全チェックを中断する。
`src/quiz_sift/**` に書き込むタスクはすべて `types` ステップの緑をここに依存している。
"""

__version__ = "0.1.1"
