"""Filesystem scanning and exact path resolution for API handlers."""

from __future__ import annotations

import re
from pathlib import Path

from app.locks.files import worker_lock_path
from app.paths import (
    SOURCE_RAW,
    SOURCE_UPLOAD,
    SOURCES,
    STAGE_FOLDERS,
    ConcreteRawPath,
    LogicalPath,
    PathValidationError,
    format_versioned_filename,
    is_archive_path,
    is_docling_input_path,
    next_version,
    parse_concrete_okf_path,
    parse_concrete_raw_path,
    parse_logical_path,
    raw_to_stage0_okf,
)
from app.queue.job import QueueJob
from app.queue.redis_queue import JobQueue

_LOCK_SUFFIXES = (".upload.lock", ".worker.lock")
_STAGE_FOLDER_NAMES = frozenset(STAGE_FOLDERS.values())


def is_upload_lock_or_worker_lock(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _LOCK_SUFFIXES)


def list_raw_concrete_paths(shared_root: str) -> list[str]:
    root = Path(shared_root)
    results: list[str] = []
    for source in SOURCES:
        source_dir = root / source
        if not source_dir.is_dir():
            continue
        for path in source_dir.rglob("*"):
            if not path.is_file() or is_upload_lock_or_worker_lock(path.name):
                continue
            relative = path.relative_to(root).as_posix()
            try:
                parse_concrete_raw_path(relative)
            except PathValidationError:
                continue
            if not is_docling_input_path(relative):
                continue
            results.append(relative)
    return results


def is_concrete_raw_path(path: str) -> bool:
    try:
        parse_concrete_raw_path(path.strip("/"))
        return True
    except PathValidationError:
        return False


def is_concrete_okf_path(path: str) -> bool:
    try:
        parse_concrete_okf_path(path.strip("/"))
        return True
    except PathValidationError:
        return False


def reject_concrete_upload_path(path: str) -> None:
    normalized = path.strip("/")
    if normalized.startswith(tuple(f"{source}/" for source in SOURCES)):
        raise PathValidationError("upload accepts logical path only")
    if "__v" in Path(normalized).name:
        raise PathValidationError("upload accepts logical path only")
    if is_archive_path(normalized):
        raise PathValidationError("upload does not accept archive files")


def reject_non_docling_upload_path(path: str) -> None:
    normalized = path.strip("/")
    if not is_docling_input_path(normalized):
        raise PathValidationError("upload accepts docling-supported document types only")


def validate_selector_consistency(path: str) -> None:
    normalized = path.strip("/")
    if is_concrete_okf_path(normalized) or is_concrete_raw_path(normalized):
        return
    if normalized.endswith(".md"):
        raw_candidate = normalized[: -len(".md")]
        if is_concrete_raw_path(raw_candidate) or is_concrete_okf_path(raw_candidate):
            return
    filename = Path(normalized).name
    has_version_token = bool(re.search(r"__v\d+", filename))
    has_source_prefix = normalized.startswith(tuple(f"{source}/" for source in SOURCES))
    if has_version_token and not has_source_prefix:
        raise PathValidationError("inconsistent logical and concrete selectors")


def _raw_path_candidates(path: str) -> list[str]:
    normalized = path.strip("/")
    if normalized.startswith(f"{SOURCE_UPLOAD}/") or normalized.startswith(f"{SOURCE_RAW}/"):
        return [normalized]
    return [f"{SOURCE_UPLOAD}/{normalized}", f"{SOURCE_RAW}/{normalized}"]


def resolve_exact_raw_path(path: str, shared_root: str) -> ConcreteRawPath | None:
    """Resolve exact raw file on disk; logical paths try UPLOAD_DATA then RAW_DATA."""
    validate_selector_consistency(path)
    normalized = path.strip("/")
    root = Path(shared_root)

    if is_concrete_okf_path(normalized):
        raw_relative = parse_concrete_okf_path(normalized).raw.relative
        if (root / raw_relative).is_file() and is_docling_input_path(raw_relative):
            return parse_concrete_raw_path(raw_relative)
        return None

    if normalized.endswith(".md"):
        raw_candidate = normalized[: -len(".md")]
        if is_concrete_raw_path(raw_candidate):
            if (root / raw_candidate).is_file() and is_docling_input_path(raw_candidate):
                return parse_concrete_raw_path(raw_candidate)
            return None

    for candidate in _raw_path_candidates(normalized):
        if (root / candidate).is_file():
            resolved = parse_concrete_raw_path(candidate)
            if is_docling_input_path(resolved.relative):
                return resolved
            return None
    return None


def resolve_exact_okf_path(okf_path: str, shared_root: str) -> str | None:
    """Return exact OKF relative path on disk for markdown reads."""
    validate_selector_consistency(okf_path)
    normalized = okf_path.strip("/")
    root = Path(shared_root)

    parts = Path(normalized).parts
    if parts and parts[0] in _STAGE_FOLDER_NAMES:
        if (root / normalized).is_file():
            return normalized
        return None

    if normalized.endswith(".md"):
        raw_candidate = normalized[: -len(".md")]
        if is_concrete_okf_path(raw_candidate):
            if (root / raw_candidate).is_file():
                return raw_candidate
            return None

    resolved = resolve_exact_raw_path(normalized, shared_root)
    if resolved is None:
        return None
    okf_relative = raw_to_stage0_okf(resolved.relative)
    if (root / okf_relative).is_file():
        return okf_relative
    return None


def _collect_upload_versions(target_dir: Path, parsed: LogicalPath) -> list[int]:
    existing_versions: list[int] = []
    if not target_dir.is_dir():
        return existing_versions
    pattern = f"{parsed.stem}__v*{parsed.extension}"
    for candidate in target_dir.glob(pattern):
        relative = "/".join(
            part
            for part in [SOURCE_UPLOAD, parsed.directory, candidate.name]
            if part
        )
        try:
            existing_versions.append(parse_concrete_raw_path(relative).version)
        except PathValidationError:
            continue
    return existing_versions


def allocate_upload_resolved_path(shared_root: str, logical_path: str) -> str:
    reject_concrete_upload_path(logical_path)
    reject_non_docling_upload_path(logical_path)
    parsed = parse_logical_path(logical_path.strip("/"))
    directory = Path(SOURCE_UPLOAD) / parsed.directory if parsed.directory else Path(SOURCE_UPLOAD)
    target_dir = Path(shared_root) / directory
    simple_filename = f"{parsed.stem}{parsed.extension}"
    simple_path = target_dir / simple_filename
    simple_relative = f"{directory.as_posix()}/{simple_filename}".replace("//", "/")
    existing_versions = _collect_upload_versions(target_dir, parsed)

    if not simple_path.is_file() and not existing_versions:
        return simple_relative

    version = next_version(existing_versions)
    filename = format_versioned_filename(parsed.stem, version, parsed.extension)
    return f"{directory.as_posix()}/{filename}".replace("//", "/")


def queued_resolved_paths(queue: JobQueue) -> set[str]:
    raw_items = queue._client.lrange(queue.queue_key, 0, -1)
    resolved: set[str] = set()
    for item in raw_items:
        payload = item.decode() if isinstance(item, bytes) else item
        resolved.add(QueueJob.from_json(payload).resolved_path)
    return resolved


def worker_lock_exists(
    shared_root: str, resolved_path: str, worker_suffix: str
) -> bool:
    return worker_lock_path(shared_root, resolved_path, worker_suffix).exists()
