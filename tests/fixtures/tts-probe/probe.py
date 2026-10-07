"""TTS プローブ(T-069 / C-007 R-7、D-215)。ホストで実 CosyVoice を使って走らせる。

`script.json` の合成の台本1本(文と間を含み、教材を含まない)を、製品の audio 段と同じ経路
(`audio.synthesize.synthesize_tracks`: 合成 → 間の挿入 → mp3 への符号化 → `decode_mp3` → `has_pause_structure`)
に通し、その結果を `docs/test/measurements/tts-probe.md` に書く。LLM は呼ばない。

    PYTHONPATH=src:/home/koich/CosyVoice:/home/koich/CosyVoice/third_party/Matcha-TTS \\
        .venv/bin/python tests/fixtures/tts-probe/probe.py \\
        --tts-model /home/koich/CosyVoice/pretrained_models/CosyVoice-300M-SFT --out /tmp/tts-probe

`synthesize_tracks` は無音判定に落ちたトラックを `SynthesisFailed` で止めるので、記録が書かれた時点で判定は
真。判定の区間列は書き出した mp3 を復号し直して記録する。生成した mp3 は `--out` に残る(人が聞く用)。
"""

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from quiz_sift.adapters.tts_cosyvoice import MODEL_MODULE, CosyVoiceTts
from quiz_sift.audio.codec import decode_mp3
from quiz_sift.audio.silence import PAUSE_THRESHOLD_SECONDS, detect_structure, has_pause_structure
from quiz_sift.audio.synthesize import synthesize_tracks
from quiz_sift.domain.ids import ClusterId, DeckId, OptionId, QuestionId
from quiz_sift.domain.language import Language
from quiz_sift.domain.script import Answer, Discrimination, Pause, Prompt
from quiz_sift.ports.tts import PcmAudio, Tts
from quiz_sift.script.generate import PAUSE_SECONDS
from quiz_sift.script.segments import QuestionScript, Track

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.json"
RECORD = ROOT / "docs" / "test" / "measurements" / "tts-probe.md"
PROBE = "tests/fixtures/tts-probe/probe.py"


def probe_track(spec: dict[str, Any]) -> Track:
    """`script.json` の台本1本を、台本段と同じ型(`Prompt, Pause, Answer, Discrimination`)のトラックにする。"""
    deck = DeckId(spec["deck_id"])
    qid = QuestionId(deck, spec["question_id"])
    question = QuestionScript(
        qid,
        Prompt(spec["prompt"]),
        Pause(PAUSE_SECONDS),
        Answer(tuple(spec["answers"])),
        Discrimination(spec["discrimination"], tuple(OptionId(qid, d) for d in spec["distractors"])),
    )
    return Track(ClusterId(deck, spec["cluster_id"]), 0, (question,))


@dataclass
class Recording:
    """TTS の呼び出しを数え、出力のサンプルレートを覚える(復号し直すときに同じレートで読む)。"""

    inner: Tts
    calls: int = 0
    sample_rates: set[int] = field(default_factory=set)

    def synthesize(self, text: str, voice_id: str) -> PcmAudio:
        audio = self.inner.synthesize(text, voice_id)
        self.calls += 1
        self.sample_rates.add(audio.sample_rate)
        return audio


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="TTS プローブ(T-069)")
    parser.add_argument("--tts-model", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None, help="mp3 を書くディレクトリ(既定は一時ディレクトリ)")
    args = parser.parse_args()

    torch: Any = importlib.import_module("torch")
    module_file = importlib.import_module(MODEL_MODULE).__file__
    assert module_file is not None
    cosyvoice_root = Path(module_file).resolve().parents[2]
    spec = json.loads(SCRIPT.read_text(encoding="utf-8"))
    track = probe_track(spec)
    out = args.out or Path(tempfile.mkdtemp(prefix="tts-probe-"))
    started = datetime.now(UTC).isoformat(timespec="seconds")
    tts = Recording(CosyVoiceTts(str(args.tts_model)))
    report = synthesize_tracks(tts, [track], language=Language(spec["language"]), voice_id=spec["voice"], out_dir=out)
    (written,) = report.succeeded
    (sample_rate,) = tts.sample_rates
    decoded = decode_mp3(written.path, sample_rate)
    runs = detect_structure(decoded.samples, decoded.sample_rate)

    record = {
        "probe": PROBE,
        "started_at": started,
        "commit": _git(ROOT, "rev-parse", "HEAD"),
        "python": sys.version.split()[0],
        "torch_version": str(torch.__version__),
        "cuda_available": torch.cuda.is_available(),
        "cosyvoice_root": str(cosyvoice_root),
        "cosyvoice_commit": _git(cosyvoice_root, "rev-parse", "HEAD"),
        "cosyvoice_dirty": bool(_git(cosyvoice_root, "status", "--porcelain", "--untracked-files=no")),
        "model_dir": str(args.tts_model),
        "voice": spec["voice"],
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        "segments": [type(s).__name__ for s in track.segments],
        "tts_calls": tts.calls,
        "inserted_pause_seconds": PAUSE_SECONDS,
        "threshold_seconds": PAUSE_THRESHOLD_SECONDS,
        "sample_rate": decoded.sample_rate,
        "mp3": str(written.path),
        "mp3_sha256": hashlib.sha256(written.path.read_bytes()).hexdigest(),
        "track_seconds": round(written.seconds, 3),
        "runs": [{"voiced": r.voiced, "start": round(r.start, 3), "seconds": round(r.seconds, 3)} for r in runs],
        "has_pause_structure": has_pause_structure(runs),
        "llm_calls": 0,
    }
    RECORD.write_text(render(record), encoding="utf-8")
    print(RECORD)
    return 0 if record["has_pause_structure"] else 1


def render(record: dict[str, Any]) -> str:
    verdict = "真" if record["has_pause_structure"] else "偽"
    return f"""# 実測ログ: TTS プローブ(実 CosyVoice、torch 2.10)

フル処理の前のリハーサル(T-069 / C-007 R-7、D-215)。`tests/fixtures/tts-probe/script.json` の合成の台本1本
(問いかけ → 間 → 答え(2要素)→ 識別ポイント。教材を含まない)を、製品の audio 段と同じ経路
(`audio.synthesize.synthesize_tracks`: 合成 → 間の挿入 → mp3 への符号化 → `decode_mp3` → `has_pause_structure`)
でホストの実 CosyVoice に通した。LLM は呼んでいない。`{PROBE}` が書き、
`tests/measurements/test_tts_probe_logs.py` が機械判定する。

`runs` は書き出した mp3 を復号し直して `audio.silence.detect_structure` に掛けた区間列。

## 記録

```yaml
{yaml.safe_dump(record, allow_unicode=True, sort_keys=False).rstrip()}
```

## 結論

無音判定: {verdict}
"""


if __name__ == "__main__":
    sys.exit(main())
