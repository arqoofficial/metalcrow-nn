"""Step 07 - reindex script integration tests."""

import json
import os
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REINDEX_SCRIPT = REPO_ROOT / "reindex.sh"


def _setup(tmp_path: Path, shared_root: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "shared_root": str(shared_root),
                "queues": {
                    "raw2docling_raw": "parser:jobs:raw2docling_raw",
                    "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
                },
                "api": {"host": "127.0.0.1", "port": 8114},
                "workers": {"raw2docling_raw": 1, "docling_raw2docling_clean00": 1},
                "locks": {
                    "upload_suffix": ".upload.lock",
                    "worker_suffix": ".worker.lock",
                },
                "pipeline": {"stages": ["docling_raw", "docling_clean00"]},
                "runtime": {"process_timeout_seconds": 600},
            }
        ),
        encoding="utf-8",
    )
    curl_dir = tmp_path / "bin"
    curl_dir.mkdir()
    return config_path, curl_dir


def _fake_curl(curl_dir: Path, *, code: str = "202", body: dict | None = None) -> None:
    body = body or {"enqueued": 2}
    script = curl_dir / "curl"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
output=""
prev=""
for arg in "$@"; do
  if [[ "${prev}" == "-o" ]]; then
    output="${arg}"
  fi
  prev="${arg}"
done
if [[ -z "${output}" ]]; then
  output="$(mktemp)"
fi
printf '%s' '"""
        + json.dumps(body)
        + """' > "${output}"
printf '"""
        + code
        + """'
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, config_path: Path, curl_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(config_path)
    env["ENV_PATH"] = str(tmp_path / ".env")
    env["API_BASE_URL"] = "http://127.0.0.1:8114"
    env["PATH"] = f"{curl_dir}:{env.get('PATH', '')}"
    (tmp_path / ".env").write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    return subprocess.run(
        [str(REINDEX_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


def test_reindex_script_default(tmp_path: Path, shared_root: Path) -> None:
    config_path, curl_dir = _setup(tmp_path, shared_root)
    _fake_curl(curl_dir)
    result = _run(tmp_path, config_path, curl_dir)
    assert result.returncode == 0


def test_reindex_script_nonzero_on_api_error(tmp_path: Path, shared_root: Path) -> None:
    config_path, curl_dir = _setup(tmp_path, shared_root)
    _fake_curl(curl_dir, code="503", body={"detail": "down"})
    result = _run(tmp_path, config_path, curl_dir)
    assert result.returncode == 1


def test_reindex_script_outputs_summary_for_operators(tmp_path: Path, shared_root: Path) -> None:
    config_path, curl_dir = _setup(tmp_path, shared_root)
    _fake_curl(curl_dir, body={"enqueued": 4})
    result = _run(tmp_path, config_path, curl_dir)
    assert "Reindex accepted" in result.stdout
    assert "enqueued=4" in result.stdout
