"""学習記録ストア(R-8 / R-10 / R-11 / R-12)。CLI と server の唯一の共有点(D-023)。

所在とスキーマは `location.py`、学習記録の読み書きは `deck_scope.DeckScopedStore` が持つ。
store/ の外は `DeckScopedStore` だけを使い、接続には触れない(AR-18)。
"""
