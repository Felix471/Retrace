from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import retrace

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        importlib.metadata.version("build")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("build is not installed")
    output = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(output), str(REPO_ROOT)],
        cwd=output.parent,
        check=True,
    )
    assert len(list(output.glob("*.tar.gz"))) == 1
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contents_and_vendor_licenses(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
        required = {
            "retrace/adapters/builtin/ag2.yaml",
            "retrace/ui/index.html",
            "retrace/ui/app.js",
            "retrace/ui/logic.js",
            "retrace/ui/vendor/preact.module.js",
            "retrace/ui/vendor/htm.module.js",
            "retrace/adapters/builtin/" + "ava" + "lon.yaml",
            "retrace/adapters/builtin/support_pipeline.yaml",
        }
        assert required <= names
        assert any(name == "retrace/core/ingest.py" for name in names)
        forbidden = ("tests/", "fixtures/", "demo/", "scripts/", "docs/")
        assert not any(name.startswith(forbidden) for name in names)
        for name in (
            "retrace/ui/vendor/preact.module.js",
            "retrace/ui/vendor/htm.module.js",
        ):
            first_line = archive.read(name).decode("utf-8").splitlines()[0]
            assert "MIT" in first_line


def test_version_uses_distribution_metadata() -> None:
    assert retrace.__version__ == importlib.metadata.version("retrace-logs")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RETRACE_SKIP_SLOW") is not None, reason="slow tests disabled")
def test_clean_venv_wheel_smoke() -> None:
    try:
        importlib.metadata.version("build")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("build is not installed")
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/wheel_smoke.py"), "--repo", str(REPO_ROOT)],
        cwd=REPO_ROOT,
        check=True,
    )
