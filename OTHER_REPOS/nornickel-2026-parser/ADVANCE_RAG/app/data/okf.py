"""OKF metadata models and parser utilities."""

from __future__ import annotations

from pathlib import Path

import frontmatter
import yaml
from pydantic import BaseModel, Field, ValidationError


class OkfMeta(BaseModel):
    type: str
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    timestamp: str | None = None


class ParsedOkf(BaseModel):
    meta: OkfMeta
    body: str


class OkfParseError(BaseModel):
    code: str
    message: str


def parse_okf_content(content: str) -> ParsedOkf | OkfParseError:
    try:
        post = frontmatter.loads(content)
    except (yaml.YAMLError, ValueError) as exc:
        return OkfParseError(code="malformed_yaml", message=str(exc))

    if not isinstance(post.metadata, dict):
        return OkfParseError(code="invalid_frontmatter", message="Frontmatter must be a mapping")

    if "type" not in post.metadata:
        return OkfParseError(code="missing_type", message="Required field 'type' is missing")

    try:
        meta = OkfMeta.model_validate(post.metadata)
    except ValidationError as exc:
        return OkfParseError(code="validation_error", message=str(exc))

    return ParsedOkf(meta=meta, body=post.content.strip())


def parse_okf_file(path: Path) -> ParsedOkf | OkfParseError:
    if not path.is_file():
        return OkfParseError(code="not_found", message=f"File not found: {path}")
    return parse_okf_content(path.read_text(encoding="utf-8"))


def okf_meta_for_response(meta: OkfMeta) -> dict[str, object]:
    payload = meta.model_dump(exclude_none=True)
    payload["type"] = meta.type
    return payload
