"""A-7: 選択・正誤・所要時間がストア越しに永続記録され、プロセスをまたいで読み戻せる。"""

import json
import os
import subprocess
import sys
from pathlib import Path

from quiz_sift.domain.deck import Deck
from quiz_sift.scoring.session import Quizmaster
from quiz_sift.store.deck_scope import DeckScopedStore

from .conftest import DECK, PRE, FakeClock, wrong_option

READER = """
import json, sys
from pathlib import Path
from quiz_sift.domain.ids import DeckId
from quiz_sift.store.deck_scope import DeckScopedStore
rows = DeckScopedStore(DeckId(sys.argv[1]), Path(sys.argv[2])).attempts()
print(json.dumps([
    [a.session_id.local_id, a.question_id.local_id, a.chosen.local_id, a.correct, a.elapsed_seconds]
    for a in rows
]))
"""


def test_attempts_survive_a_restart(tmp_path: Path, deck: Deck, clock: FakeClock) -> None:
    quizmaster = Quizmaster(deck, DeckScopedStore(DECK, tmp_path), clock)
    right, wrong = deck.questions[0], deck.questions[3]
    quizmaster.present(PRE, right.question_id)
    clock.seconds += 2.5
    quizmaster.submit(PRE, right.question_id, right.correct_option_id)
    quizmaster.present(PRE, wrong.question_id)
    clock.seconds += 11.0
    quizmaster.submit(PRE, wrong.question_id, wrong_option(wrong))
    del quizmaster

    # 書いたプロセスとは別のプロセスが、同じ所在から読み戻す。
    src = Path(__file__).resolve().parents[2] / "src"
    out = subprocess.run(
        [sys.executable, "-c", READER, DECK, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    assert json.loads(out.stdout) == [
        [PRE.local_id, right.question_id.local_id, right.correct_option_id.local_id, True, 2.5],
        [PRE.local_id, wrong.question_id.local_id, wrong_option(wrong).local_id, False, 11.0],
    ]
