"""Build and smoke-test an installed wheel from outside the repository."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def _run(command: list[str], *, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )


def _venv_executable(venv: Path, name: str) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / scripts / f"{name}{suffix}"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _remove_temporary_tree(root: Path) -> None:
    """Remove a temp tree, allowing Windows a moment to release file locks."""
    for _attempt in range(4):
        try:
            shutil.rmtree(root)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.25)
    shutil.rmtree(root, ignore_errors=True)


def smoke(repo: Path) -> None:
    repo = repo.resolve()
    demo = (repo / "demo").resolve()
    root = Path(tempfile.mkdtemp(prefix="retrace-wheel-")).resolve()
    try:
        artifacts = root / "artifacts"
        _run(
            [sys.executable, "-m", "build", "--outdir", str(artifacts), str(repo)],
            cwd=root,
        )
        wheels = list(artifacts.glob("*.whl"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one wheel, found {wheels}")

        venv = root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        python = _venv_executable(venv, "python")
        pip = _venv_executable(venv, "pip")
        command = _venv_executable(venv, "retrace-logs")
        _run([str(pip), "install", str(wheels[0])], cwd=root)

        cli_version = _run([str(command), "--version"], cwd=root, capture=True).stdout.strip()
        metadata_version = _run(
            [
                str(python),
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('retrace-logs'))",
            ],
            cwd=root,
            capture=True,
        ).stdout.strip()
        if cli_version != f"retrace-logs {metadata_version}":
            raise AssertionError(f"CLI version mismatch: {cli_version!r} != {metadata_version!r}")
        imported = _run(
            [str(python), "-c", "import retrace; print(retrace.__file__)"],
            cwd=root,
            capture=True,
        ).stdout.strip()
        if "site-packages" not in imported.replace("\\", "/") or str(repo) in imported:
            raise AssertionError(f"wheel import leaked outside site-packages: {imported}")

        checked = _run([str(command), "check", str(demo)], cwd=root, capture=True)
        if "Runs: 40 runs" not in checked.stdout:
            raise AssertionError(checked.stdout)

        port = _free_port()
        cache = root / "cache"
        process = subprocess.Popen(
            [
                str(command),
                "view",
                str(demo),
                "--no-browser",
                "--port",
                str(port),
                "--cache-dir",
                str(cache),
            ],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            data = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"viewer exited with status {process.returncode}")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/experiment", timeout=1
                    ) as response:
                        data = json.load(response)
                    break
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.25)
            if data is None:
                raise AssertionError("viewer did not answer within 30 seconds")
            if data.get("run_count", 0) <= 0:
                raise AssertionError(f"unexpected experiment response: {data}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=30)
    finally:
        _remove_temporary_tree(root)
    print(
        "wheel smoke: verified build, isolated site-packages import, version, "
        "40-run check, and live experiment API"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    smoke(parser.parse_args().repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
