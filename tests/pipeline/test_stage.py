"""NFR-4: 段階キャッシュによる再実行性(C-016)。"""

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

import pytest

from quiz_sift.domain.ids import DeckId
from quiz_sift.pipeline.stage import Stage, StageCache, inputs_digest

DECK = DeckId("aws-saa")

STAGES = (
    Stage("cluster", prompt_template="classify {questions}", model="claude-opus-5", params={"max_tokens": 4096}),
    Stage("recall", prompt_template="recall {cluster}", model="claude-opus-5", params={"max_tokens": 2048}),
    Stage("script", prompt_template="script {recall}", model="claude-opus-5", params={"max_tokens": 8192}),
    Stage("audio", model="local-tts", params={"voice_id": "ja-1"}),
)


class CountingUpstream:
    """外部 API のスタブ。ステージ名ごとの呼び出し回数を数える。"""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def call(self, stage: Stage, upstream: bytes) -> bytes:
        self.calls[stage.name] += 1
        return b"%s(%s|%s)" % (stage.name.encode(), stage.prompt_template.encode(), upstream)


def writes(data: bytes) -> Callable[[BinaryIO], None]:
    def produce(out: BinaryIO) -> None:
        out.write(data)

    return produce


def run_pipeline(cache: StageCache, api: CountingUpstream, stages: tuple[Stage, ...]) -> bytes:
    artifact = b"questions"
    for stage in stages:

        def produce(out: BinaryIO, stage: Stage = stage, upstream: bytes = artifact) -> None:
            out.write(api.call(stage, upstream))

        artifact = cache.run(stage, artifact, produce).read_bytes()
    return artifact


@pytest.mark.parametrize("changed", range(len(STAGES)), ids=[s.name for s in STAGES])
def test_rerun_makes_no_upstream_calls(tmp_path: Path, changed: int) -> None:
    cache = StageCache(tmp_path / "out", DECK)
    first = CountingUpstream()
    run_pipeline(cache, first, STAGES)
    assert first.calls == Counter({s.name: 1 for s in STAGES})

    # 何も変えずに再実行すると、どの段階も呼ばれない。
    unchanged = CountingUpstream()
    run_pipeline(cache, unchanged, STAGES)
    assert sum(unchanged.calls.values()) == 0

    # `changed` 番目の段階を直して再実行すると、それより前は 0 回、そこから後は作り直される。
    stages = list(STAGES)
    stages[changed] = replace(stages[changed], prompt_template=stages[changed].prompt_template + " v2")
    rerun = CountingUpstream()
    run_pipeline(cache, rerun, tuple(stages))
    assert all(rerun.calls[s.name] == 0 for s in STAGES[:changed])
    assert all(rerun.calls[s.name] == 1 for s in STAGES[changed:])


def test_digest_covers_prompt_and_model_but_not_code(tmp_path: Path) -> None:
    stage = STAGES[0]
    base = inputs_digest(stage, b"questions")

    # 前段成果物・プロンプト・モデル・生成パラメータのどれを変えても digest が変わる。
    variants = {
        "upstream": inputs_digest(stage, b"questions!"),
        "prompt": inputs_digest(replace(stage, prompt_template="classify v2 {questions}"), b"questions"),
        "model": inputs_digest(replace(stage, model="claude-sonnet-5"), b"questions"),
        "params": inputs_digest(replace(stage, params={"max_tokens": 1024}), b"questions"),
    }
    assert base not in variants.values()
    assert len(set(variants.values())) == len(variants)
    assert inputs_digest(replace(stage, params={"max_tokens": 4096}), b"questions") == base

    # ステージ実装コードは入らない: 実装を差し替えても同じ入力ならキャッシュが返る。
    cache = StageCache(tmp_path / "out", DECK)
    first = cache.run(stage, b"questions", writes(b"v1 implementation"))
    assert first.name == base

    def rewritten(out: BinaryIO) -> None:
        raise AssertionError("実装コードの変更で再実行された")

    assert cache.run(stage, b"questions", rewritten) == first
    assert first.read_bytes() == b"v1 implementation"

    # プロンプトを変えると旧成果物は返らない。
    revised = cache.run(replace(stage, prompt_template="classify v2"), b"questions", writes(b"v2"))
    assert revised != first
    assert revised.read_bytes() == b"v2"


def test_partial_write_does_not_become_a_cache_hit(tmp_path: Path) -> None:
    cache = StageCache(tmp_path / "out", DECK)
    stage = STAGES[-1]
    target = tmp_path / "out" / DECK / stage.name / inputs_digest(stage, b"script")
    seen_mid_write: list[bool] = []

    def crash_mid_write(out: BinaryIO) -> None:
        out.write(b"ID3 truncated")
        out.flush()
        seen_mid_write.append(target.exists())
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cache.run(stage, b"script", crash_mid_write)
    # 書いている最中も digest 名のパスは存在しない(一時パスに書いている)。
    assert seen_mid_write == [False]
    assert not target.exists()

    # プロセスごと落ちて一時ファイルが残った場合も、それはヒットとして読まれない。
    (target.parent / ".leftover.partial").write_bytes(b"ID3 truncated")

    calls: list[bytes] = []

    def complete(out: BinaryIO) -> None:
        calls.append(b"")
        out.write(b"ID3 complete")

    assert cache.run(stage, b"script", complete).read_bytes() == b"ID3 complete"
    assert len(calls) == 1
