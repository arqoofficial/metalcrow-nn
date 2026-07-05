"""Path mapping and versioning under SHARED/."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable, Literal

from pydantic import BaseModel, Field

STAGE_FOLDERS = {
    "docling_raw": "00_docling_raw",
    "docling_clean00": "01_docling_clean00",
}

SOURCE_UPLOAD = "UPLOAD_DATA"
SOURCE_RAW = "RAW_DATA"
SOURCES: tuple[str, ...] = (SOURCE_UPLOAD, SOURCE_RAW)

ARCHIVE_EXTENSIONS = frozenset(
    {
        ".001",
        ".002",
        ".003",
        ".004",
        ".005",
        ".7z",
        ".arj",
        ".bz2",
        ".cab",
        ".cpio",
        ".deb",
        ".dmg",
        ".gz",
        ".iso",
        ".lha",
        ".lzh",
        ".rar",
        ".rpm",
        ".tar",
        ".tbz2",
        ".tgz",
        ".txz",
        ".xz",
        ".z",
        ".zip",
    }
)

DOCLING_INPUT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".html",
        ".htm",
        ".md",
        ".csv",
        ".xlsx",
        ".odt",
        ".ods",
        ".odp",
        ".asciidoc",
        ".adoc",
        ".epub",
        ".latex",
        ".tex",
        ".vtt",
    }
)

_VERSION_FILENAME_RE = re.compile(
    r"^(?P<stem>.+)__v(?P<version>\d+)(?P<ext>\.[^./]+)$"
)


class PathValidationError(ValueError):
    """Invalid or unsafe path input."""


class PathNormalizationWarning(BaseModel):
    code: str
    message: str


class SubtreeRootResult(BaseModel):
    normalized: str
    warnings: list[PathNormalizationWarning] = Field(default_factory=list)


class LogicalPath(BaseModel):
    directory: str
    stem: str
    extension: str

    @property
    def relative(self) -> str:
        return _join_relative(self.directory, f"{self.stem}{self.extension}")


class ConcreteRawPath(BaseModel):
    source: Literal["UPLOAD_DATA", "RAW_DATA"]
    directory: str
    stem: str
    extension: str
    version: int
    versioned: bool = True

    @property
    def logical_key(self) -> str:
        return _join_relative(self.source, self.directory, f"{self.stem}{self.extension}")

    @property
    def relative(self) -> str:
        if self.versioned:
            filename = f"{self.stem}__v{self.version:02d}{self.extension}"
        else:
            filename = f"{self.stem}{self.extension}"
        return _join_relative(self.source, self.directory, filename)

    def with_version(self, version: int) -> ConcreteRawPath:
        return self.model_copy(update={"version": version, "versioned": True})


class ConcreteOkfPath(BaseModel):
    stage_folder: str
    raw: ConcreteRawPath

    @property
    def relative(self) -> str:
        return stage_okf_path(self.stage_folder, self.raw.relative)


def _join_relative(*parts: str) -> str:
    cleaned = [part.strip("/") for part in parts if part.strip("/")]
    return "/".join(cleaned)


def _split_directory(filename: str) -> tuple[str, str]:
    path = PurePosixPath(filename)
    directory = str(path.parent) if path.parent != PurePosixPath(".") else ""
    return directory, path.name


def _parse_filename_parts(filename: str, *, require_version: bool) -> tuple[str, str, int | None]:
    if require_version:
        match = _VERSION_FILENAME_RE.match(filename)
        if match is None:
            raise PathValidationError(f"malformed version token in filename: {filename}")
        return match.group("stem"), match.group("ext"), int(match.group("version"))

    if "__v" in filename:
        raise PathValidationError(f"unexpected version token in logical filename: {filename}")

    path = PurePosixPath(filename)
    if path.suffix == "":
        raise PathValidationError(f"missing extension in path: {filename}")
    return path.stem, path.suffix, None


def parse_logical_path(path: str) -> LogicalPath:
    directory, filename = _split_directory(path.strip("/"))
    stem, extension, _ = _parse_filename_parts(filename, require_version=False)
    return LogicalPath(directory=directory, stem=stem, extension=extension)


def parse_concrete_raw_path(path: str) -> ConcreteRawPath:
    normalized = path.strip("/")
    parts = PurePosixPath(normalized).parts
    if not parts:
        raise PathValidationError("empty concrete raw path")
    source = parts[0]
    if source not in SOURCES:
        raise PathValidationError(f"unknown source prefix: {source}")
    directory, filename = _split_directory("/".join(parts[1:]))
    try:
        stem, extension, version = _parse_filename_parts(filename, require_version=True)
        assert version is not None
        return ConcreteRawPath(
            source=source,
            directory=directory,
            stem=stem,
            extension=extension,
            version=version,
            versioned=True,
        )
    except PathValidationError as exc:
        if "__v" in filename:
            raise exc
        stem, extension, _ = _parse_filename_parts(filename, require_version=False)
        return ConcreteRawPath(
            source=source,
            directory=directory,
            stem=stem,
            extension=extension,
            version=1,
            versioned=False,
        )


def parse_concrete_okf_path(path: str) -> ConcreteOkfPath:
    normalized = path.strip("/")
    if not normalized.endswith(".md"):
        raise PathValidationError("concrete OKF path must end with .md")
    without_md = normalized[: -len(".md")]
    parts = PurePosixPath(without_md).parts
    if not parts:
        raise PathValidationError("empty concrete OKF path")
    stage_folder = parts[0]
    raw_path = "/".join(parts[1:])
    return ConcreteOkfPath(
        stage_folder=stage_folder,
        raw=parse_concrete_raw_path(raw_path),
    )


def build_logical_key(source: str, directory: str, stem: str, extension: str) -> str:
    return _join_relative(source, directory, f"{stem}{extension}")


def parse_version_number(token: str) -> int:
    match = re.fullmatch(r"v(\d+)", token)
    if match is None:
        raise PathValidationError(f"malformed version token: {token}")
    return int(match.group(1))


def compare_versions(left: int | str, right: int | str) -> int:
    left_num = parse_version_number(left) if isinstance(left, str) else left
    right_num = parse_version_number(right) if isinstance(right, str) else right
    return (left_num > right_num) - (left_num < right_num)


def next_version(existing_versions: Iterable[int]) -> int:
    versions = list(existing_versions)
    if not versions:
        return 1
    return max(versions) + 1


def format_versioned_filename(stem: str, version: int, extension: str, width: int = 2) -> str:
    return f"{stem}__v{version:0{width}d}{extension}"


def stage_okf_path(stage_folder: str, raw_absolute_path: str) -> str:
    """OKF path under SHARED/, e.g. ``00_docling_raw/UPLOAD_DATA/.../q1__v02.pdf.md``."""
    return _join_relative(stage_folder, f"{raw_absolute_path.strip('/')}.md")


def raw_to_stage0_okf(raw_absolute_path: str) -> str:
    return stage_okf_path(STAGE_FOLDERS["docling_raw"], raw_absolute_path)


def raw_to_stage1_okf(raw_absolute_path: str) -> str:
    return stage_okf_path(STAGE_FOLDERS["docling_clean00"], raw_absolute_path)


def file_extension(path: str) -> str:
    return PurePosixPath(path.strip("/")).suffix.lower()


def is_archive_path(path: str) -> bool:
    return file_extension(path) in ARCHIVE_EXTENSIONS


def is_docling_input_path(path: str) -> bool:
    extension = file_extension(path)
    return extension in DOCLING_INPUT_EXTENSIONS and extension not in ARCHIVE_EXTENSIONS


def normalize_subtree_root(root: str) -> SubtreeRootResult:
    warnings: list[PathNormalizationWarning] = []
    normalized = root.replace("\\", "/")

    if normalized.strip() == "":
        return SubtreeRootResult(normalized="", warnings=warnings)

    if normalized.startswith("/"):
        normalized = normalized.lstrip("/")
        warnings.append(
            PathNormalizationWarning(
                code="ROOT_NORMALIZED",
                message="Removed leading slash from subtree root.",
            )
        )

    if normalized == "SHARED":
        normalized = ""
        warnings.append(
            PathNormalizationWarning(
                code="ROOT_NORMALIZED",
                message="Stripped implicit SHARED prefix from subtree root.",
            )
        )
    elif normalized.startswith("SHARED/"):
        normalized = normalized[len("SHARED/") :]
        warnings.append(
            PathNormalizationWarning(
                code="ROOT_NORMALIZED",
                message="Stripped implicit SHARED prefix from subtree root.",
            )
        )

    if "//" in normalized:
        normalized = re.sub(r"/+", "/", normalized)
        warnings.append(
            PathNormalizationWarning(
                code="ROOT_NORMALIZED",
                message="Collapsed repeated path separators.",
            )
        )

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise PathValidationError("subtree root escapes SHARED boundary")
            parts.pop()
            continue
        parts.append(part)

    return SubtreeRootResult(normalized="/".join(parts), warnings=warnings)


def reject_outside_shared_root(root: str) -> None:
    normalize_subtree_root(root)
