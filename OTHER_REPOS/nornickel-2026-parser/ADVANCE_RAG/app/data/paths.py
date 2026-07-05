"""SHARED filesystem path utilities and validation."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.config.settings import QueryConfig, SharedConfig


class PathValidationError(BaseModel):
    code: str
    message: str
    path: str = ""


class ResolvedPath(BaseModel):
    absolute: Path
    relative_to_shared: str
    source_subfolder: str
    relative_in_subfolder: str


def resolve_shared_root(shared: SharedConfig, base_dir: Path) -> Path:
    return shared.resolve_root(base_dir)


def normalize_relative_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip("/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal is not allowed")
    return "/".join(parts)


def resolve_path_in_shared(
    shared_root: Path,
    path: str,
    allowed_subfolders: list[str],
    source_subfolder: str | None = None,
) -> ResolvedPath | PathValidationError:
    try:
        relative = normalize_relative_path(path)
    except ValueError as exc:
        return PathValidationError(code="traversal_rejected", message=str(exc), path=path)

    shared_root = shared_root.resolve()

    if source_subfolder is not None:
        if source_subfolder not in allowed_subfolders:
            return PathValidationError(
                code="subfolder_not_allowed",
                message=f"Source subfolder not allowed: {source_subfolder}",
                path=path,
            )
        subfolder_root = (shared_root / source_subfolder).resolve()
        candidate = (subfolder_root / relative).resolve()
        try:
            candidate.relative_to(shared_root)
            relative_in_subfolder = str(candidate.relative_to(subfolder_root)).replace("\\", "/")
        except ValueError:
            return PathValidationError(
                code="outside_subfolder",
                message=f"Path is outside subfolder {source_subfolder}",
                path=path,
            )
        return ResolvedPath(
            absolute=candidate,
            relative_to_shared=f"{source_subfolder}/{relative_in_subfolder}",
            source_subfolder=source_subfolder,
            relative_in_subfolder=relative_in_subfolder,
        )

    candidate = (shared_root / relative).resolve()
    try:
        candidate.relative_to(shared_root)
    except ValueError:
        return PathValidationError(
            code="outside_shared",
            message="Path resolves outside SHARED root",
            path=path,
        )

    for folder in allowed_subfolders:
        folder_path = (shared_root / folder).resolve()
        try:
            relative_in_subfolder = str(candidate.relative_to(folder_path)).replace("\\", "/")
        except ValueError:
            continue
        return ResolvedPath(
            absolute=candidate,
            relative_to_shared=relative,
            source_subfolder=folder,
            relative_in_subfolder=relative_in_subfolder,
        )

    return PathValidationError(
        code="subfolder_not_allowed",
        message="Path is not under an allowed source subfolder",
        path=path,
    )


def resolve_source_subfolder(
    query_config: QueryConfig,
    requested: str | None,
) -> str | PathValidationError:
    effective = requested or query_config.default_source_subfolder
    if effective not in query_config.allowed_source_subfolders:
        return PathValidationError(
            code="subfolder_not_allowed",
            message=f"Source subfolder not allowed: {effective}",
            path=effective,
        )
    return effective
