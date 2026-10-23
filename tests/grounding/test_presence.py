"""語句の実在性判定(C-003 / C-004 / ADR-010)。日本語と英語の双方で判定できること。"""

from quiz_sift.grounding.presence import is_present

JA_TEXTS = (
    "Amazon EventBridge のルールで S3 のイベントを捕捉する。",
    "EC2 インスタンスの料金設定を見直す。",
)
EN_TEXTS = (
    "Capture S3 events with an Amazon EventBridge rule.",
    "Review the pricing of EC2 instances.",
)


def test_presence_in_ja_and_en() -> None:
    # 日本語: 分かち書きされない本文の中の語句を見つけ、無い語句は無いと言う。
    assert is_present("ルール", JA_TEXTS)
    assert is_present("料金設定", JA_TEXTS)
    assert is_present("S3", JA_TEXTS)
    assert is_present("Amazon EventBridge", JA_TEXTS)
    assert not is_present("オートスケーリング", JA_TEXTS)
    assert not is_present("EC20", JA_TEXTS)

    # 英語: 単語境界を守り、大文字小文字を区別しない。
    assert is_present("rule", EN_TEXTS)
    assert is_present("pricing", EN_TEXTS)
    assert is_present("amazon eventbridge", EN_TEXTS)
    assert not is_present("price", EN_TEXTS)
    assert not is_present("Lambda", EN_TEXTS)


def test_alnum_edges_need_a_boundary() -> None:
    assert not is_present("S3", ["S30 を使う"])
    assert not is_present("cat", ["category"])
    assert is_present("S3", ["S3バケット"])
    assert is_present("設定", ["料金設定"])


def test_normalisation() -> None:
    assert is_present("EC2", ["ＥＣ２インスタンス"])
    assert is_present("Amazon EventBridge", ["Amazon\n  EventBridge"])


def test_empty_term_and_empty_texts() -> None:
    assert not is_present("", JA_TEXTS)
    assert not is_present("  ", JA_TEXTS)
    assert not is_present("S3", [])
