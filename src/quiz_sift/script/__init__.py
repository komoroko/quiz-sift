"""台本(R-6 / NFR-1)。

`generate` が想起素材からクラスタごとの台本をセグメント列として組み立て、`split` で3分以内の
トラックに割り、`label_guard` のラベル列挙判定と含意判定(`safety/entailment`)をすべて通ったときだけ
成果物を書く。再生時間の推定は `duration`、1問分の順序の保証は `segments` が持つ。
"""
