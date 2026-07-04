"""Step 07 - reindex.sh script tests."""

import json
import os
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REINDEX_SCRIPT = REPO_ROOT / "reindex.sh"


def _write_config(tmp_path: Path, shared_root: Path, port: int = 8114) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "shared_root": str(shared_root),
                "queues": {
                    "raw2docling_raw": "parser:jobs:raw2docling_raw",
                    "docling_raw2docling_clean00": "parser:jobs:docling_raw2docling_clean00",
                },
                "api": {"host": "127.0.0.1", "port": port},
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
    return config_path


def _install_fake_curl(tmp_path: Path, *, status_code: str = "202", body: dict | None = None) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    body = body or {"enqueued": 3}
    script = bin_dir / "curl"
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
        + status_code
        + """'
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run_script(
    tmp_path: Path,
    config_path: Path,
    *,
    args: list[str] | None = None,
    api_base_url: str | None = "http://127.0.0.1:8114",
    fake_curl_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(config_path)
    env["ENV_PATH"] = str(tmp_path / ".env")
    (tmp_path / ".env").write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    if api_base_url is not None:
        env["API_BASE_URL"] = api_base_url
    else:
        env.pop("API_BASE_URL", None)
    if fake_curl_dir is not None:
        env["PATH"] = f"{fake_curl_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        [str(REINDEX_SCRIPT), *(args or [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )


def test_reindex_posts_empty_body(tmp_path: Path, shared_root: Path) -> None:
    config_path = _write_config(tmp_path, shared_root)
    curl_dir = _install_fake_curl(tmp_path)
    result = _run_script(tmp_path, config_path, fake_curl_dir=curl_dir)
    assert result.returncode == 0
    assert "enqueued=" in result.stdout


def test_reindex_uses_configured_api_url(tmp_path: Path, shared_root: Path) -> None:
    config_path = _write_config(tmp_path, shared_root, port=9001)
    curl_dir = tmp_path / "bin"
    curl_dir.mkdir()
    log = curl_dir / "curl.log"
    script = curl_dir / "curl"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
output=""
prev=""
for arg in "$@"; do
  if [[ "${{prev}}" == "-o" ]]; then
    output="${{arg}}"
  fi
  if [[ "${{arg}}" == http://* ]]; then
    echo "${{arg}}" >> "{log}"
  fi
  prev="${{arg}}"
done
if [[ -z "${{output}}" ]]; then
  output="$(mktemp)"
fi
printf '{{"enqueued": 1}}' > "${{output}}"
printf '202'
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    result = _run_script(tmp_path, config_path, api_base_url=None, fake_curl_dir=curl_dir)
    assert result.returncode == 0
    assert "http://127.0.0.1:9001/api/v1/reindex" in log.read_text(encoding="utf-8")


def test_reindex_nonzero_exit_on_api_failure(tmp_path: Path, shared_root: Path) -> None:
    config_path = _write_config(tmp_path, shared_root)
    curl_dir = _install_fake_curl(tmp_path, status_code="500", body={"detail": "fail"})
    result = _run_script(tmp_path, config_path, fake_curl_dir=curl_dir)
    assert result.returncode == 1


def test_reindex_prints_summary(tmp_path: Path, shared_root: Path) -> None:
    config_path = _write_config(tmp_path, shared_root)
    curl_dir = _install_fake_curl(tmp_path, body={"enqueued": 7})
    result = _run_script(tmp_path, config_path, fake_curl_dir=curl_dir)
    assert "enqueued=7" in result.stdout
