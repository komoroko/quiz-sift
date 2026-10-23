"""名詞句の切り出し(C-003 / C-004 / ADR-010)。

形態素解析だけで名詞を取る実装は `S3` を `S` と `3` に割ったまま返す(D-056 の実測)。
略語が1語句として残り、割れた断片が語句として出てこないことを確かめる。
"""

from quiz_sift.domain.language import Language
from quiz_sift.grounding.terms import acronyms, extract_terms


def test_acronyms_survive_tokenisation() -> None:
    terms = extract_terms(
        "Amazon EventBridge のルールで S3 のイベントを捕捉し、EC2 インスタンスを起動する", Language.JA
    )

    assert "S3" in terms
    assert "EC2" in terms
    # 形態素解析が割った断片は語句として出てこない。
    assert not {"S", "3", "EC", "2"} & set(terms)
    # 形態素解析の名詞は取れている。
    assert {"Amazon", "EventBridge", "ルール", "イベント", "捕捉", "インスタンス"} <= set(terms)
    # 助詞・動詞は語句にならない。
    assert not {"の", "で", "を", "し", "する", "起動する"} & set(terms)


def test_acronym_glued_to_japanese_and_full_width() -> None:
    terms = extract_terms("ＥＣ２のIAMロールとS3バケット", Language.JA)

    assert {"EC2", "IAM", "S3"} <= set(terms)
    assert "バケット" in terms
    assert not {"S", "3", "EC", "2"} & set(terms)


def test_compound_nouns_join_and_bare_numbers_drop() -> None:
    terms = extract_terms("料金設定を3つ変える", Language.JA)

    assert "料金設定" in terms
    assert "3" not in terms


def test_terms_keep_order_without_duplicates() -> None:
    assert extract_terms("S3 と S3 と IAM", Language.JA) == ("S3", "IAM")


def test_acronym_heuristic() -> None:
    assert acronyms("S3, EC2, IAM と DynamoDB、Lambda と a3") == ("S3", "EC2", "IAM", "DynamoDB")


def test_english_splits_on_word_boundaries_and_drops_function_words() -> None:
    terms = extract_terms("Use an S3 bucket policy to restrict EC2 access.", Language.EN)

    assert terms == ("Use", "S3", "bucket", "policy", "restrict", "EC2", "access")
