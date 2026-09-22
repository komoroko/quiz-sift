"""問題データの取り込み(R-1)。reader → validate → dedup の順に通し、`IngestReport` を返す。

入力形式(ADR-011)は `data/<deck_id>/*.jsonl` の1行1問で、スキーマは `reader.py` にある。
"""
