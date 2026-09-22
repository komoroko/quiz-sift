# tests/domain/ にも test_studied.py がある。パッケージにしておかないと、同じ basename の
# テストモジュールが pytest の rootdir 解決と mypy のモジュール名で衝突する。
