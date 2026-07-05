"""Step 08 - panel.sh wrapper tests."""

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL_SCRIPT = REPO_ROOT / "panel.sh"


def test_panel_sh_default_invocation(tmp_path: Path, monkeypatch) -> None:
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
echo "UV $*"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["UV"] = str(fake_uv)
    env["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    env["ENV_FILE"] = str(tmp_path / ".env")
    env["REFRESH_SEC"] = "5"
    (tmp_path / "config.yaml").write_text("shared_root: /tmp\n", encoding="utf-8")
    result = subprocess.run(
        [str(PANEL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "--refresh-sec" in result.stdout
    assert "5" in result.stdout


def test_panel_sh_argument_passthrough(tmp_path: Path) -> None:
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
echo "UV $*"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["UV"] = str(fake_uv)
    env["CONFIG_PATH"] = str(tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text("shared_root: /tmp\n", encoding="utf-8")
    result = subprocess.run(
        [str(PANEL_SCRIPT), "once", "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "once" in result.stdout
    assert "--json" in result.stdout
    assert str(tmp_path / "config.yaml") in result.stdout


def test_panel_sh_falls_back_to_local_config(tmp_path: Path) -> None:
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
echo "UV $*"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    local_config = tmp_path / "config" / "local.yaml"
    local_config.parent.mkdir(parents=True)
    local_config.write_text("shared_root: /tmp\n", encoding="utf-8")
    panel_script = tmp_path / "panel.sh"
    panel_script.write_text(
        (REPO_ROOT / "panel.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    panel_script.chmod(panel_script.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["UV"] = str(fake_uv)
    env.pop("CONFIG_PATH", None)
    result = subprocess.run(
        [str(panel_script), "once"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert str(local_config) in result.stdout