"""処理コストと所要時間の計測(NFR-7)。

実行1回ごとに `run_id, elapsed_sec, calls_by_model, estimated_cost` を JSON Lines の1行として
追記する。推定コストの算出根拠を**ログだけから**追跡できるよう、モデル別の内訳トークンと、
計算に使った単価表(`priced_at` 付き)を同じ行に書く。

トークンは input / output の2値に畳まない(AR-29)。単価表も同じ粒度で持つ。
TTS はローカル OSS なのでコストが乗らない(ADR-004)。上限は初回実測後に決める(D-021)。
"""

import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType

from quiz_sift.ports.clock import Clock
from quiz_sift.ports.llm import TokenUsage

_PER = 1_000_000
"""単価表の単位(USD per 1M tokens)。"""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """1モデルの単価(USD per 1M tokens)。`priced_at` は単価を確かめた日。"""

    input_per_mtok: float
    cache_write_per_mtok: float
    cache_read_per_mtok: float
    output_per_mtok: float
    """思考トークンを含む出力の単価。"""
    priced_at: date

    def cost(self, usage: TokenUsage) -> float:
        return (
            usage.input_tokens * self.input_per_mtok
            + usage.cache_creation_input_tokens * self.cache_write_per_mtok
            + usage.cache_read_input_tokens * self.cache_read_per_mtok
            + usage.output_tokens * self.output_per_mtok
        ) / _PER


PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {
        # ADR-002 の入力 $5.00 / 出力 $25.00。キャッシュは Anthropic の料金体系に従い、
        # 書き込み(5 分 TTL)が入力の 1.25 倍、読み出しが 0.1 倍。
        "claude-opus-5": ModelPrice(
            input_per_mtok=5.00,
            cache_write_per_mtok=6.25,
            cache_read_per_mtok=0.50,
            output_per_mtok=25.00,
            priced_at=date(2026, 9, 22),
        ),
    }
)


class Meter:
    """実行1回分の計測器。`ports.llm.UsageRecorder` を満たし、LLM 実装に結線して使う。"""

    def __init__(self, clock: Clock, *, prices: Mapping[str, ModelPrice] = PRICES) -> None:
        self.run_id = uuid.uuid4().hex
        self._clock = clock
        self._prices = prices
        self._started_at = clock.now()
        self._started = clock.monotonic()
        self._calls: dict[str, int] = {}
        self._tokens: dict[str, TokenUsage] = {}
        self._finished = False

    def record(self, model: str, usage: TokenUsage) -> None:
        # 単価の無いモデルを 0 円で数えると推定コストが黙って過小になる。記録の時点で落とす。
        if model not in self._prices:
            raise ValueError(f"単価表に無いモデル: {model}")
        total = self._tokens.get(model, TokenUsage(0, 0, 0, 0))
        self._tokens[model] = TokenUsage(
            input_tokens=total.input_tokens + usage.input_tokens,
            cache_creation_input_tokens=total.cache_creation_input_tokens + usage.cache_creation_input_tokens,
            cache_read_input_tokens=total.cache_read_input_tokens + usage.cache_read_input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
        )
        self._calls[model] = self._calls.get(model, 0) + 1

    def finish(self, log_path: Path) -> dict[str, object]:
        """実行を締め、1行を `log_path` に追記して、書いた内容を返す。"""
        if self._finished:
            raise RuntimeError("この計測はもう締めてある")
        self._finished = True
        by_model = {model: self._prices[model].cost(tokens) for model, tokens in self._tokens.items()}
        entry: dict[str, object] = {
            "run_id": self.run_id,
            "started_at": self._started_at.isoformat(),
            "elapsed_sec": self._clock.monotonic() - self._started,
            "calls_by_model": {
                model: {"calls": self._calls[model], **asdict(tokens)} for model, tokens in self._tokens.items()
            },
            "estimated_cost": {"currency": "USD", "total": sum(by_model.values()), "by_model": by_model},
            "prices": {
                model: {**asdict(self._prices[model]), "priced_at": self._prices[model].priced_at.isoformat()}
                for model in self._tokens
            },
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
