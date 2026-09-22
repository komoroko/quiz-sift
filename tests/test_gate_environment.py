"""品質ゲートの環境そのものについての検査。

ここに置くテストは製品の振る舞いではなく、**証拠を作る仕掛けが壊れていないこと**を見る。
壊れると、以降のすべてのテストの緑が意味を失う種類のもの。
"""

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_python_version_matches_the_pinned_runtime() -> None:
    assert sys.version_info[:2] == (3, 13)


def test_pyproject_declares_the_src_layout() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]


def test_baked_requirements_match_the_lockfile() -> None:
    """イメージに焼く requirements.txt が uv.lock から導出したままであること。

    ビルドコンテキストの都合で uv.lock を直接 COPY できないため、派生物を
    .rein/oci/quiz-sift/ に置いている。手で編集された版が黙って焼かれると、
    ホスト・CI・サンドボックスが別々の依存集合で走ることになる(AR-1)。
    """
    baked = ROOT / ".rein" / "oci" / "quiz-sift" / "requirements.txt"
    assert baked.is_file(), "焼き込み用の requirements.txt が無い"

    uv = shutil.which("uv")
    if uv is None:
        # 品質ゲートのサンドボックスには uv があるが、無い環境でも壊れないようにする。
        # ここで飛ばすのは「比較の手段が無い」場合だけで、比較できるなら必ず比較する。
        import pytest

        pytest.skip("uv が無いので lock との比較ができない")

    exported = subprocess.run(
        [uv, "export", "--frozen", "--no-emit-project", "--all-groups", "--format", "requirements-txt"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    # 生成コマンドを記録するヘッダ行は -o の有無で変わるので、依存行だけを比べる。
    def deps(text: str) -> list[str]:
        return sorted(line for line in text.splitlines() if line and not line.startswith(("#", " ")))

    assert deps(baked.read_text(encoding="utf-8")) == deps(exported)


def test_the_gate_script_exposes_every_step_the_config_uses() -> None:
    """scripts/gate.sh が config.yaml の quality_gate が呼ぶ引数を全部持つこと(AR-6)。"""
    script = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
    config = (ROOT / ".rein" / "config.yaml").read_text(encoding="utf-8")

    used = {line.split("gate.sh,")[1].strip().rstrip("]").strip() for line in config.splitlines() if "gate.sh," in line}
    assert used, "config.yaml が gate.sh を呼んでいない"
    for step in used:
        assert f"  {step})" in script, f"gate.sh に {step} のケースが無い"
