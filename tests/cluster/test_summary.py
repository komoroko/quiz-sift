"""クラスタ要約の語句実在性と含意判定(C-003 / C-013 / R-3 / NFR-1 / A-5 / A-6 / D-161)。"""

from collections import Counter
from pathlib import Path

import pytest

from quiz_sift.cluster.run import Clustering, cluster_deck
from quiz_sift.cluster.summary import UngroundedSummary, ungrounded_terms
from quiz_sift.domain.deck import Deck
from quiz_sift.domain.ids import DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.question import Option, Question
from quiz_sift.domain.studied import StudiedFlag
from quiz_sift.ports.llm import ClassificationResult, Entailment, GroupProposal
from quiz_sift.safety.entailment import EntailmentRejected

type Groups = dict[str, tuple[QuestionId, ...]]

# 各グループの問題文に語句が実在する要約。
GROUNDED = {
    "s3": "S3 のストレージクラスはアクセス頻度で選ぶ",
    "ec2": "EC2 の購入オプション",
    "iam": "IAM ポリシーでは明示的な拒否が優先される",
    "rds": "RDS のマルチ AZ 配置とリードレプリカ",
    "eventbridge": "EventBridge のルールで Lambda を起動する",
}
# Lambda は eventbridge クラスタにはあるが s3 クラスタの問題には無い。
UNGROUNDED_S3 = "S3 のストレージクラスは Lambda で選ぶ"
# 語句はすべて s3 クラスタに実在するが、正解・解説(標準-IA はミリ秒単位で取り出せる)と矛盾する。
CONTRADICTING_S3 = "S3 標準-IA の取り出しには数時間かかる"


class StubLlm:
    """`summaries` の要約を返す。`sequences` にあるグループは、呼ばれるたびにその列を順に返す(尽きたら最後)。"""

    def __init__(
        self,
        groups: Groups,
        summaries: dict[str, str],
        contradicted: frozenset[str] = frozenset(),
        sequences: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.groups = groups
        self.summaries = summaries
        self.contradicted = contradicted
        self.sequences = sequences or {}
        self.checked: list[tuple[str, str]] = []
        self.summarized: list[str] = []

    def classify_clusters(self, deck: Deck) -> ClassificationResult:
        return ClassificationResult(tuple(GroupProposal(k, v) for k, v in self.groups.items()), ())

    def summarize_cluster(self, members: Deck) -> str:
        (label,) = {k for k, ids in self.groups.items() if members.questions[0].question_id in ids}
        self.summarized.append(label)
        if label in self.sequences:
            sequence = self.sequences[label]
            return sequence[min(self.summarized.count(label), len(sequence)) - 1]
        return self.summaries[label]

    def check_entailment(self, reference: str, statement: str) -> Entailment:
        self.checked.append((reference, statement))
        return Entailment.CONTRADICTED if statement in self.contradicted else Entailment.CONSISTENT


class Writer:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, result: Clustering) -> Path:
        self.path.write_text("\n".join(c.summary for c in result.clusters), encoding="utf-8")
        return self.path


def test_summary_terms_must_exist_in_the_cluster(known5: tuple[Deck, Groups], tmp_path: Path) -> None:
    deck, groups = known5
    s3_members = Deck(
        deck.deck_id, deck.language, None, tuple(q for q in deck.questions if q.question_id in groups["s3"])
    )
    assert ungrounded_terms(GROUNDED["s3"], s3_members) == ()
    # 他のクラスタにある語でも、このクラスタの問題に無ければ実在しない。
    assert ungrounded_terms(UNGROUNDED_S3, s3_members) == ("Lambda",)

    # 実在する要約だけなら書き出される。
    ok = tmp_path / "ok.txt"
    cluster_deck(StubLlm(groups, GROUNDED), deck, Writer(ok))
    assert ok.read_text(encoding="utf-8").splitlines() == list(GROUNDED.values())

    # 実在しない語を含む要約があれば、含意判定より前に止まり、何も書かれない。
    bad = tmp_path / "bad.txt"
    llm = StubLlm(groups, {**GROUNDED, "s3": UNGROUNDED_S3})
    with pytest.raises(UngroundedSummary) as exc:
        cluster_deck(llm, deck, Writer(bad))
    assert exc.value.code not in (None, 0)
    assert "Lambda" not in str(exc.value)
    assert not bad.exists()
    assert llm.checked == []


def test_summary_goes_through_entailment(known5: tuple[Deck, Groups], tmp_path: Path) -> None:
    deck, groups = known5
    s3_members = Deck(
        deck.deck_id, deck.language, None, tuple(q for q in deck.questions if q.question_id in groups["s3"])
    )
    # 仕込んだ矛盾は語句の実在性では止まらない。止めるのは含意判定だけ。
    assert ungrounded_terms(CONTRADICTING_S3, s3_members) == ()

    out = tmp_path / "clusters.txt"
    llm = StubLlm(groups, {**GROUNDED, "s3": CONTRADICTING_S3}, contradicted=frozenset({CONTRADICTING_S3}))
    with pytest.raises(EntailmentRejected) as exc:
        cluster_deck(llm, deck, Writer(out))
    assert exc.value.verdict is Entailment.CONTRADICTED
    assert exc.value.code not in (None, 0)
    assert not out.exists()
    # 要約はそのクラスタの正解・公式解説を照合先として判定されている。
    (reference, statement) = next(pair for pair in llm.checked if pair[1] == CONTRADICTING_S3)
    assert "S3 標準-IA" in reference and "ミリ秒単位で取り出せる" in reference
    assert "Lambda" not in reference

    # 矛盾が無ければ、全クラスタの要約が1件ずつ判定されてから書き出される。
    llm = StubLlm(groups, GROUNDED)
    cluster_deck(llm, deck, Writer(out))
    assert out.exists()
    assert sorted(statement for _, statement in llm.checked) == sorted(GROUNDED.values())


def _deck(language: Language, rows: dict[str, tuple[str, str]]) -> tuple[Deck, Groups]:
    """合成の問題から、1問1グループのデッキを作る。`rows` はグループ → (問題文, 解説)。"""
    deck_id = DeckId("synthetic")
    questions = []
    for label, (stem, explanation) in rows.items():
        qid = QuestionId(deck_id, label)
        options = tuple(Option(OptionId(qid, x), f"{label} {x}") for x in "ABCD")
        questions.append(Question(qid, stem, options, (options[0].option_id,), explanation, StudiedFlag.UNKNOWN))
    deck = Deck(deck_id, language, None, tuple(questions))
    return deck, {q.question_id.local_id: (q.question_id,) for q in questions}


def _members(deck: Deck, label: str) -> Deck:
    return Deck(deck.deck_id, deck.language, None, tuple(q for q in deck.questions if q.question_id.local_id == label))


JA_ROWS = {
    "iam": ("IAM ポリシーで確認すべきポイントはどれか。", "明示的な拒否は許可より優先される。"),
    "sqs": ("SQS のキューでメッセージを遅延させる設定はどれか。", "遅延キューは配信を遅らせる。"),
}
EN_ROWS = {
    "iam": ("Which IAM policy element denies access?", "An explicit deny overrides any allow."),
    "sqs": ("Which SQS setting delays messages in a queue?", "A delay queue postpones delivery."),
}


def test_generic_nouns_absent_from_the_cluster_do_not_stop_the_summary(tmp_path: Path) -> None:
    deck, groups = _deck(Language.JA, JA_ROWS)
    # 「判断」「要点」は本文に無い漢字だけの語。「確認ポイント」は句全体では本文に無いが、ポイントはある。
    summary = "IAM ポリシーの判断の要点は、確認ポイントとして明示的な拒否が優先されることである。"
    assert ungrounded_terms(summary, _members(deck, "iam")) == ()

    out = tmp_path / "clusters.txt"
    summaries = {"iam": summary, "sqs": "SQS の遅延キューは配信を遅らせる。"}
    llm = StubLlm(groups, summaries)
    cluster_deck(llm, deck, Writer(out))
    assert out.read_text(encoding="utf-8").splitlines() == list(summaries.values())
    assert sorted(statement for _, statement in llm.checked) == sorted(summaries.values())


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (Language.JA, "IAM ポリシーではパーミッションバウンダリーが優先される。"),
        (Language.JA, "IAM ポリシーは Lambda の拒否を優先する。"),
        (Language.EN, "An explicit deny in an IAM policy overrides Lambda permissions."),
        (Language.EN, "An explicit deny in an IAM policy overrides any SCP."),
    ],
)
def test_invented_technical_terms_still_stop_the_summary(language: Language, summary: str, tmp_path: Path) -> None:
    deck, groups = _deck(language, JA_ROWS if language is Language.JA else EN_ROWS)
    (term,) = ungrounded_terms(summary, _members(deck, "iam"))

    out = tmp_path / "clusters.txt"
    llm = StubLlm(groups, {"iam": summary, "sqs": "SQS"})
    with pytest.raises(UngroundedSummary) as exc:
        cluster_deck(llm, deck, Writer(out))
    assert exc.value.code not in (None, 0)
    assert term not in str(exc.value)
    assert not out.exists()
    assert llm.checked == []


def test_general_words_are_not_checked_and_term_free_summaries_still_go_through_entailment(tmp_path: Path) -> None:
    deck, _ = _deck(Language.EN, EN_ROWS)
    # 小文字の一般語(precedence, rule)と、文頭の大文字始まりの一般語(Precedence, Always)は照合しない。
    summary = "Precedence matters for this rule. Always remember that an explicit deny wins in IAM."
    assert ungrounded_terms(summary, _members(deck, "iam")) == ()

    deck, groups = _deck(Language.JA, JA_ROWS)
    term_free = "明示的な拒否は許可より優先されるという原則。"
    assert ungrounded_terms(term_free, _members(deck, "iam")) == ()
    out = tmp_path / "clusters.txt"
    llm = StubLlm(groups, {"iam": term_free, "sqs": "SQS"}, contradicted=frozenset({term_free}))
    with pytest.raises(EntailmentRejected):
        cluster_deck(llm, deck, Writer(out))
    assert term_free in [statement for _, statement in llm.checked]
    assert not out.exists()


RACK_ROWS = {
    Language.JA: "42U のラックに 1U/2U のサーバーを S3 のバックアップ用に収める構成はどれか。",
    Language.EN: "Which layout fits 1U/2U servers into a 42U rack for S3 backups?",
}


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (Language.JA, "2026 年の構成では 42U のラックと 1U/2U、S3 を使う。"),
        (Language.EN, "In 2026 the layout puts 1U/2U servers into a 42U rack for S3 backups."),
    ],
)
def test_alphanumeric_words_starting_with_a_digit_are_checked_whole(language: Language, summary: str) -> None:
    deck, _ = _deck(language, {"rack": (RACK_ROWS[language], "")})
    # 42U や 1U/2U は数字始まりの語全体で照合する(末尾の U だけを切り出さない)。数字だけの 2026 は照合しない。
    assert ungrounded_terms(summary, _members(deck, "rack")) == ()
    if language is Language.JA:
        # 日本語デッキは英数字の語全体で照合するので、本文に無い 48U は止まる。
        assert ungrounded_terms(summary.replace("42U", "48U"), _members(deck, "rack")) == ("48U",)


def test_summary_reference_lists_every_correct_option(tmp_path: Path) -> None:
    """A-7: クラスタ要約の reference は、クラスタ内の各問題の正解をすべて含む(先頭の1件だけにしない)。"""
    deck_id = DeckId("multi")
    questions = []
    for local_id, corrects in (("m1", "AC"), ("m2", "BD")):
        qid = QuestionId(deck_id, local_id)
        options = tuple(Option(OptionId(qid, x), f"{local_id} の選択肢 {x}") for x in "ABCD")
        correct = tuple(o.option_id for o in options if o.option_id.local_id in corrects)
        questions.append(Question(qid, f"{local_id} の問題文", options, correct, None, StudiedFlag.UNKNOWN))
    deck = Deck(deck_id, Language.JA, None, tuple(questions))
    groups: Groups = {"multi": tuple(q.question_id for q in questions)}
    llm = StubLlm(groups, {"multi": "m1 の問題文"})
    cluster_deck(llm, deck, Writer(tmp_path / "clusters.txt"))

    ((reference, _),) = llm.checked
    for local_id, corrects, wrong in (("m1", "AC", "BD"), ("m2", "BD", "AC")):
        assert f"正解: {local_id} の選択肢 {corrects[0]}、{local_id} の選択肢 {corrects[1]}" in reference
        assert not any(f"{local_id} の選択肢 {x}" in reference for x in wrong)


def test_rejected_summary_is_regenerated_for_that_cluster_only(known5: tuple[Deck, Groups], tmp_path: Path) -> None:
    """A-1: 実在性か含意判定に落ちたクラスタだけ要約を作り直し、2回落ちてから通れば3回目の要約で書く。"""
    deck, groups = known5
    for failing in (UNGROUNDED_S3, CONTRADICTING_S3):
        out = tmp_path / "clusters.txt"
        llm = StubLlm(
            groups,
            GROUNDED,
            contradicted=frozenset({CONTRADICTING_S3}),
            sequences={"s3": (failing, failing, GROUNDED["s3"])},
        )
        cluster_deck(llm, deck, Writer(out))
        assert Counter(llm.summarized) == {**dict.fromkeys(GROUNDED, 1), "s3": 3}, failing
        assert out.read_text(encoding="utf-8").splitlines() == list(GROUNDED.values())
        out.unlink()

    # 実在性 → 含意判定 → 通る、の混ざった落ち方も同じ上限を共有する。
    llm = StubLlm(
        groups,
        GROUNDED,
        contradicted=frozenset({CONTRADICTING_S3}),
        sequences={"s3": (UNGROUNDED_S3, CONTRADICTING_S3, GROUNDED["s3"])},
    )
    cluster_deck(llm, deck, Writer(tmp_path / "mixed.txt"))
    assert llm.summarized.count("s3") == 3
    # 実在性に落ちた要約は判定にかけない。
    assert UNGROUNDED_S3 not in [statement for _, statement in llm.checked]


@pytest.mark.parametrize(
    ("sequence", "error"),
    [
        ((CONTRADICTING_S3,), EntailmentRejected),
        ((UNGROUNDED_S3,), UngroundedSummary),
        # 落ち方が混ざれば、最後に落ちた検査で決める。
        ((UNGROUNDED_S3, UNGROUNDED_S3, CONTRADICTING_S3), EntailmentRejected),
        ((CONTRADICTING_S3, CONTRADICTING_S3, UNGROUNDED_S3), UngroundedSummary),
    ],
)
def test_summary_regeneration_exhausted_still_stops(
    known5: tuple[Deck, Groups], tmp_path: Path, sequence: tuple[str, ...], error: type[SystemExit]
) -> None:
    """A-2: 3回続けて落ちたクラスタがあれば、最後に落ちた検査に応じた例外で止まり、何も書かない。"""
    deck, groups = known5
    out = tmp_path / "clusters.txt"
    llm = StubLlm(groups, GROUNDED, contradicted=frozenset({CONTRADICTING_S3}), sequences={"s3": sequence})
    with pytest.raises(error) as exc:
        cluster_deck(llm, deck, Writer(out))
    assert type(exc.value) is error
    assert exc.value.code not in (None, 0)
    if isinstance(exc.value, EntailmentRejected):
        assert exc.value.verdict is Entailment.CONTRADICTED
    assert llm.summarized.count("s3") == 3
    assert not out.exists()
