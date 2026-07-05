"""Parse and serialize OKF markdown files with Pydantic-validated YAML frontmatter."""

from __future__ import annotations

from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.data.okf_parser import ParserOkfDocument, ParserOkfFrontmatter
from app.data.okf_standard import OkfDocument, OkfFrontmatterStandard

T = TypeVar("T", bound=BaseModel)


class OkfFormatError(ValueError):
    """Raised when OKF file structure or frontmatter validation fails."""


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split OKF markdown into YAML block and body (§4)."""
    if not text.startswith("---"):
        raise OkfFormatError("OKF file must start with '---' frontmatter delimiter")

    try:
        first_end = text.index("\n---", 3)
    except ValueError as exc:
        raise OkfFormatError("Malformed frontmatter: missing closing '---'") from exc

    yaml_block = text[4:first_end]
    body = text[first_end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    return yaml_block, body


def _load_yaml_mapping(yaml_block: str) -> dict:
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise OkfFormatError(f"Invalid YAML frontmatter: {exc}") from exc

    if not isinstance(data, dict):
        raise OkfFormatError("Frontmatter must be a YAML mapping")
    return data


def validate_frontmatter(model: type[T], yaml_block: str) -> T:
    """Parse YAML and validate with a Pydantic frontmatter model."""
    data = _load_yaml_mapping(yaml_block)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise OkfFormatError(f"Frontmatter validation failed: {exc}") from exc


def parse_okf_standard(text: str) -> OkfDocument:
    yaml_block, body = split_frontmatter(text)
    frontmatter = validate_frontmatter(OkfFrontmatterStandard, yaml_block)
    return OkfDocument(frontmatter=frontmatter, body=body)


def parse_okf(text: str) -> ParserOkfDocument:
    """Parse parser OKF file; YAML frontmatter validated via Pydantic."""
    yaml_block, body = split_frontmatter(text)
    frontmatter = validate_frontmatter(ParserOkfFrontmatter, yaml_block)
    return ParserOkfDocument(frontmatter=frontmatter, body=body)


def serialize_okf(doc: ParserOkfDocument) -> str:
    """Render parser OKF document to markdown with YAML frontmatter."""
    payload = doc.frontmatter.model_dump(mode="json", exclude_none=True)
    yaml_block = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{yaml_block}\n---\n{doc.body}"
