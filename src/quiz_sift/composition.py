"""既定の `Llm` の唯一の選択点(NFR-8 (c)、ADR-013)。このモジュールにはこの関数以外を置かない。

分岐・環境変数参照・動的解決を持たない。別の実装が要るテストは、ここを通さずに直接構築する。
"""

from quiz_sift.adapters.llm_claude_cli import ClaudeCliLlm
from quiz_sift.ports.llm import Llm
from quiz_sift.ports.metering import RunMeter


def build_default_llm(meter: RunMeter) -> Llm:
    return ClaudeCliLlm(meter)
