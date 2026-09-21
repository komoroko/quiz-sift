"""スケルトンが組み上がっていることの確認。

品質ゲートが「収集対象ゼロ」で落ちないための最小の1本でもある。製品コードが入り次第、
このファイルは役目を終えるので削除する。
"""

import quiz_sift


def test_package_imports_and_reports_its_version() -> None:
    assert quiz_sift.__version__ == "0.1.0"
