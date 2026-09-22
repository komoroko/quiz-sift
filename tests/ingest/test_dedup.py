"""文面がほぼ同一の重複問題の検出(受入基準 A-4)。"""

from difflib import SequenceMatcher

from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ingest.dedup import NEAR_DUPLICATE_RATIO, DuplicatePair, dedup_key, find_duplicates
from quiz_sift.ingest.normalize import normalize_text

from .conftest import DECK

OPTIONS = ("Amazon S3 を使う", "Amazon EBS を使う", "Amazon EFS を使う", "AWS Storage Gateway を使う")


def question(local_id: str, stem: str, options: tuple[str, ...] = OPTIONS) -> Question:
    question_id = QuestionId(DECK, local_id)
    return Question(
        question_id=question_id,
        stem=stem,
        options=tuple(Option(OptionId(question_id, label), text) for label, text in zip("ABCD", options, strict=True)),
        correct_option_id=OptionId(question_id, "A"),
        explanation=None,
        studied=StudiedFlag.UNKNOWN,
    )


def ratio(a: Question, b: Question) -> float:
    return SequenceMatcher(None, dedup_key(a), dedup_key(b), autojunk=False).ratio()


BASE = (
    "ある企業は、オンプレミスのファイルサーバーに保存している大量の画像データを、"
    "耐久性が高く低コストなストレージへ移行したい。最も適切なサービスはどれか。"
)

# 全角・余分な空白・句読点の違いと、選択肢の並べ替えだけ。正規化後に完全一致する。
EXACT = question(
    "exact",
    "ある企業は　オンプレミスの　ファイルサーバーに保存している大量の画像データを"
    "耐久性が高く低コストなストレージへ移行したい 最も適切なサービスはどれか？",
    ("ＡＷＳ Storage Gateway を使う", "Amazon EFS を使う", "Amazon S3を使う!", "Amazon EBS を使う"),
)
# 1語だけ違う。ratio は 0.95 以上 1 未満。
NEAR = question("near", BASE.replace("大量の", "多数の"))
# 文の後半が違う。ratio は 0.95 未満 — 閾値を下げすぎた実装はここで余計な組を拾う。
FAR = question(
    "far",
    BASE.replace("耐久性が高く低コストなストレージへ移行したい", "社内の利用者から低遅延で参照できるようにしたい"),
)
OTHER = question("other", "VPC 内のプライベートサブネットから DynamoDB へ到達させる方法はどれか。")


def test_near_duplicates_are_counted() -> None:
    """受入基準 A-4: 正規化後の完全一致と ratio 0.95 以上で重複が検出され、件数が報告される。"""
    base = question("base", BASE)
    # フィクスチャが狙った位置にあることを先に確かめる。ここが崩れると下の判定は何も言わない。
    assert dedup_key(EXACT) == dedup_key(base)
    assert EXACT.stem != base.stem
    assert NEAR_DUPLICATE_RATIO <= ratio(base, NEAR) < 1.0
    assert ratio(base, FAR) < NEAR_DUPLICATE_RATIO

    report = find_duplicates(Deck(DECK, Language.JA, None, (base, OTHER, EXACT, NEAR, FAR)))

    base_id, exact_id, near_id = (QuestionId(DECK, name) for name in ("base", "exact", "near"))
    assert set(report.pairs) == {
        DuplicatePair(base_id, exact_id, exact=True),
        DuplicatePair(base_id, near_id, exact=False),
        # 完全一致どうしは同じ鍵なので、NEAR は EXACT とも近い。
        DuplicatePair(exact_id, near_id, exact=False),
    }
    assert report.exact_count == 1
    assert report.near_count == 2


def test_same_boilerplate_stem_with_different_options_is_not_a_duplicate() -> None:
    """定型の問題文でも選択肢が違えば別の問題。問題文だけを比べる実装はここで誤検出する。"""
    stem = "次のうち、正しいものはどれか。"
    a = question("a", stem, ("S3 は結果整合性のみ", "EBS は複数 AZ で共有", "EFS は NFS", "Glacier は即時取得"))
    b = question("b", stem, ("IAM はリージョン単位", "KMS は鍵を自動回転", "SQS は FIFO 不可", "SNS は pull 型"))
    assert find_duplicates(Deck(DECK, Language.JA, None, (a, b))).pairs == ()


def test_normalize_folds_width_space_and_symbols() -> None:
    """全角半角・大文字小文字・空白・記号の違いは鍵に残らない。"""
    assert normalize_text("  ＡＷＳ　Lambda、（関数）!? ") == "awslambda関数"
