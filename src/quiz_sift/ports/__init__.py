"""外部に触る依存の Protocol(D-008)。

`Llm` / `Tts` / `Clock` の3つだけを置く。Browser は置かない(AR-26)。実装は
`quiz_sift.adapters` にあり、外部 SDK の import はそこに閉じる。この層は `domain` にしか
依存しない。
"""
