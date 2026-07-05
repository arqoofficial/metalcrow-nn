"""Step 00 - integration baseline: contract docs present and decision lock consistent."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MANDATORY_CONTRACT_DOCS = [
    "docs/SPECIFICATION.md",
    "docs/LAYER_PRESENTATION.md",
    "docs/LAYER_SERVICES.md",
    "docs/LAYER_DATA.md",
    "docs/LAYER_CONFIG.md",
    "docs/ADMIN_PANEL.md",
    "docs/LAYER_INFRASTRUCTURE.md",
    "IMPLEMENTATION_NOTES.md",
]

# Each rule must appear in all listed docs (at least one pattern match per doc).
DECISION_LOCK_RULES: dict[str, dict[str, list[str]]] = {
    "outside SHARED -> 400": {
        "docs/SPECIFICATION.md": ["400", "outside `SHARED`"],
        "docs/LAYER_PRESENTATION.md": ["400", "outside `SHARED`"],
        "IMPLEMENTATION_NOTES.md": ["400", "outside `SHARED`"],
    },
    "limit <= 1000": {
        "docs/SPECIFICATION.md": ["limit <= 1000", "<= 1000"],
        "docs/LAYER_PRESENTATION.md": ["limit <= 1000", "<= 1000"],
        "IMPLEMENTATION_NOTES.md": ["limit <= 1000"],
    },
    "max_depth <= 10": {
        "docs/SPECIFICATION.md": ["max_depth <= 10", "<= 10"],
        "docs/LAYER_PRESENTATION.md": ["max_depth <= 10", "<= 10"],
        "IMPLEMENTATION_NOTES.md": ["max_depth <= 10"],
    },
    "hidden files excluded": {
        "docs/SPECIFICATION.md": ["hidden", "hide"],
        "docs/LAYER_PRESENTATION.md": ["Hidden", "excluded"],
        "IMPLEMENTATION_NOTES.md": ["hidden", "Hidden"],
    },
    "lock files always hidden": {
        "docs/SPECIFICATION.md": ["lock", "upload.lock", "worker.lock"],
        "docs/LAYER_PRESENTATION.md": ["Lock files", "lock"],
        "IMPLEMENTATION_NOTES.md": ["lock", "upload.lock", "worker.lock"],
    },
    "no symlink traversal": {
        "docs/SPECIFICATION.md": ["symlink", "not follow"],
        "docs/LAYER_PRESENTATION.md": ["Symlinks are not followed", "symlink"],
        "IMPLEMENTATION_NOTES.md": ["symlink", "not follow"],
    },
    "QueueJob fields": {
        "docs/LAYER_SERVICES.md": ["job_id", "requested_path", "resolved_path"],
        "IMPLEMENTATION_NOTES.md": ["job_id", "requested_path", "resolved_path"],
    },
    "version token __vNN": {
        "docs/SPECIFICATION.md": ["__vNN"],
        "IMPLEMENTATION_NOTES.md": ["__vNN"],
    },
}


def _read_doc(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_docs_contract_files_present() -> None:
    missing = [
        relative_path
        for relative_path in MANDATORY_CONTRACT_DOCS
        if not (REPO_ROOT / relative_path).is_file()
    ]
    assert not missing, f"Missing mandatory contract docs: {missing}"


def test_decision_lock_consistency() -> None:
    doc_cache: dict[str, str] = {}
    violations: list[str] = []

    for rule_name, doc_patterns in DECISION_LOCK_RULES.items():
        for relative_path, patterns in doc_patterns.items():
            if relative_path not in doc_cache:
                doc_cache[relative_path] = _read_doc(relative_path)
            content = doc_cache[relative_path]
            if not any(pattern in content for pattern in patterns):
                violations.append(
                    f"{rule_name!r} not found in {relative_path} "
                    f"(expected one of {patterns!r})"
                )

    assert not violations, "Decision lock inconsistency across docs:\n" + "\n".join(violations)
