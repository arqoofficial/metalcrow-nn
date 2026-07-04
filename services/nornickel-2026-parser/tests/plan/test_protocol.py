"""Step 11 - plan protocol meta-tests."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = REPO_ROOT / "docs/plan"

REQUIRED_SECTIONS = (
    "## Goal",
    "## Prerequisites",
    "## Acceptance Criteria",
    "## Required Tests",
    "## Verification Command",
)


def _read_step(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _step_files() -> list[Path]:
    return sorted(PLAN_DIR.glob("step_*.md"))


def test_every_step_file_exists() -> None:
    expected = {PLAN_DIR / f"step_{index:02d}.md" for index in range(11)}
    missing = sorted(path for path in expected if not path.is_file())
    assert not missing, f"Missing plan steps: {missing}"


@pytest.mark.parametrize("step_path", _step_files(), ids=lambda p: p.name)
def test_every_step_has_required_sections(step_path: Path) -> None:
    content = _read_step(step_path)
    missing = [section for section in REQUIRED_SECTIONS if section not in content]
    assert not missing, f"{step_path.name} missing sections: {missing}"


@pytest.mark.parametrize("step_path", _step_files(), ids=lambda p: p.name)
def test_every_step_defines_verification_command(step_path: Path) -> None:
    content = _read_step(step_path)
    assert "pytest" in content
    assert "## Verification Command" in content or "## Integration Verification Command" in content


def test_step_completion_report_template_documented() -> None:
    step11 = _read_step(PLAN_DIR / "step_11.md")
    for field in ("Implemented", "Tests", "Risks", "Files changed"):
        assert field in step11
