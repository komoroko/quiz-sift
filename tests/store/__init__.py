# tests/server/ にも test_concurrency.py が来る(T-017)。パッケージにしておかないと、同じ basename の
# テストモジュールが pytest の rootdir 解決と mypy のモジュール名で衝突する。
