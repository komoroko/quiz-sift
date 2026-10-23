"""既習フラグの解決(受入基準 A-3)。"""

import inspect

from quiz_sift.domain import studied as studied_module
from quiz_sift.domain.studied import StudiedFlag, resolve_studied


def test_studied_resolution_order() -> None:
    """受入基準 A-3: 個別 > デッキ既定 > unknown、かつ unknown を変換しない。"""
    # 個別指定が最優先。デッキ既定と食い違っても個別が勝つ。
    assert resolve_studied(StudiedFlag.STUDIED, StudiedFlag.UNSEEN) is StudiedFlag.STUDIED
    assert resolve_studied(StudiedFlag.UNSEEN, StudiedFlag.STUDIED) is StudiedFlag.UNSEEN

    # 個別が無ければデッキ既定。
    assert resolve_studied(None, StudiedFlag.STUDIED) is StudiedFlag.STUDIED
    assert resolve_studied(None, StudiedFlag.UNSEEN) is StudiedFlag.UNSEEN

    # どちらも無ければ unknown。既定値で埋めない。
    assert resolve_studied(None, None) is StudiedFlag.UNKNOWN

    # 個別に unknown と**書かれて**いるのも指定なので、デッキ既定へは落ちない。
    assert resolve_studied(StudiedFlag.UNKNOWN, StudiedFlag.STUDIED) is StudiedFlag.UNKNOWN

    # unknown から studied/unseen へ変換する関数が存在しないこと。このモジュールが
    # 公開する関数は resolve_studied だけで、それは unknown を入れれば unknown を返す。
    public_functions = {
        name for name, member in vars(studied_module).items() if inspect.isfunction(member) and not name.startswith("_")
    }
    assert public_functions == {"resolve_studied"}, f"既習フラグを触る関数が増えている: {sorted(public_functions)}"


def test_the_flag_has_exactly_three_values() -> None:
    """unknown は欠損値ではなく答えの1つ。R-8 のレポートはこの3値で分ける(AR-10)。"""
    assert {flag.value for flag in StudiedFlag} == {"studied", "unseen", "unknown"}
