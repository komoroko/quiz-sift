"""外部に触る依存の Protocol(D-008)。

`Llm` / `Tts` / `Clock` と、LLM 呼び出しを計測する `RunMeter`(NFR-7)を置く。Browser は
置かない(AR-26)。実装は `quiz_sift.adapters` にあり、外部 SDK の import はそこに閉じる。
この層は `domain` にしか依存しない。
"""
