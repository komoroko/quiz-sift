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


def _steps_in_config() -> set[str]:
    config = (ROOT / ".rein" / "config.yaml").read_text(encoding="utf-8")
    return {line.split("gate.sh,")[1].strip().rstrip("]").strip() for line in config.splitlines() if "gate.sh," in line}


def _steps_in_ci() -> set[str]:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return {
        line.split("gate.sh")[1].strip()
        for line in workflow.splitlines()
        if "gate.sh" in line and line.lstrip().startswith("- run:")
    }


def test_the_gate_script_exposes_every_step_the_config_uses() -> None:
    """gate.sh が config.yaml の quality_gate が呼ぶ引数を全部持つこと(AR-6)。"""
    script = (ROOT / "gate.sh").read_text(encoding="utf-8")
    used = _steps_in_config()
    assert used, "config.yaml が gate.sh を呼んでいない"
    for step in used:
        assert f"  {step})" in script, f"gate.sh に {step} のケースが無い"


def test_ci_and_the_quality_gate_run_the_same_steps() -> None:
    """NFR-5 (d): CI の run ステップと品質ゲートの argv が同じ検査を呼ぶこと。

    `gate.sh` を両方から呼ぶだけでは足りない — **引数の集合が食い違えば**
    「同一のコマンド列」は成立しない。CI にだけあるステップ、ゲートにだけあるステップの
    両方を検出する。
    """
    in_config = _steps_in_config()
    in_ci = _steps_in_ci()
    assert in_ci, "ci.yml が gate.sh を呼んでいない"
    assert in_config == in_ci, (
        f"CI とゲートで検査ステップが食い違う: "
        f"CI のみ={sorted(in_ci - in_config)} / ゲートのみ={sorted(in_config - in_ci)}"
    )


def test_the_running_environment_matches_the_baked_requirements() -> None:
    """**走っている環境**が requirements.txt の pin どおりであること(N-1)。

    `test_baked_requirements_match_the_lockfile` が比べているのはリポジトリ内の2ファイル同士で、
    ゲートが実際に走っているイメージの中身ではない。`uv lock` と `uv export` をやり直しても
    `rein oci build --write-config` を忘れれば、2ファイルは一致したままイメージだけが古い。
    その上で取られた緑は「宣言した依存集合についての証拠」ではなくなる。
    """
    import re
    from importlib.metadata import PackageNotFoundError, version

    baked = ROOT / ".rein" / "oci" / "quiz-sift" / "requirements.txt"
    pins: dict[str, str] = {}
    for line in baked.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^ \\;]+)", line)
        if m and ";" not in line:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    assert pins, "requirements.txt から pin を1つも読めていない"

    mismatched: list[str] = []
    for dist, pinned in pins.items():
        try:
            installed = version(dist)
        except PackageNotFoundError:
            # 環境マーカーで除外される配布物がある。ここで落とすと偽陽性になる。
            continue
        if installed != pinned:
            mismatched.append(f"{dist}: installed={installed} pinned={pinned}")
    assert not mismatched, "イメージと requirements.txt が食い違う: " + "; ".join(mismatched)


def test_each_gate_step_invokes_the_tool_it_claims_to() -> None:
    """gate.sh の各ケースが名前どおりのツールを呼ぶこと(S-2)。

    共通スクリプト化は「2箇所がずれる」を「1箇所を緩めれば両方同時に緩む」に置き換えた。
    `lint) ruff check . || true` の1行編集で CI とゲートが同時に無害化されるので、
    ケース本体に何が書いてあるかを固定しておく。
    """
    script = (ROOT / "gate.sh").read_text(encoding="utf-8")
    expected = {
        "lint": "ruff check",
        "format": "ruff format --check",
        "types": "mypy",
        "test": "pytest",
        "docs": "mkdocs build --strict",
    }
    for step, tool in expected.items():
        body = script.split(f"  {step})", 1)[1].split(";;", 1)[0]
        assert tool in body, f"{step} が {tool} を呼んでいない"
        assert "||" not in body, f"{step} の本体が失敗を握りつぶしている"


def test_gate_smoke_subprocess_exits_zero() -> None:
    """`bash gate.sh smoke` が実際に走って終了コード 0 を返すこと(A-1)。

    `src/quiz_sift/__init__.py` の存在を `Path.exists()` で assert するだけでは、
    import できない `__init__.py` でも、`__version__` の無い `__init__.py` でも緑になる。
    smoke ステップが見ているのは「起動経路が生きているか」なので、**subprocess で
    gate.sh を呼ぶこと自体**がこの基準の中身。
    """
    result = subprocess.run(
        ["bash", "gate.sh", "smoke"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"gate.sh smoke が非0終了した: {result.stderr.strip()}"
    assert result.stdout.strip(), "smoke が何も出力していない(__version__ が読めていない)"
