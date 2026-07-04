"""OKF v0.1 standard frontmatter — strict subset of the official spec.

Spec: docs/OKF_STANDARD_EXTERNAL.md
Upstream: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

OKF_SPEC_VERSION = "0.1"
OKF_SPEC_URL = (
    "https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md"
)


class OkfFrontmatterStandard(BaseModel):
    """OKF v0.1 concept frontmatter (§4.1).

    Required: ``type`` (non-empty).
    Recommended: ``title``, ``description``, ``resource``, ``tags``, ``timestamp``.
    No extension fields — use :class:`OkfFrontmatterStandardExtra` to read foreign bundles.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str = Field(
        ...,
        min_length=1,
        description="REQUIRED. Kind of concept, e.g. 'Parsed Document'.",
    )
    title: str | None = Field(
        default=None,
        description="Human-readable display name.",
    )
    description: str | None = Field(
        default=None,
        description="One-line summary.",
    )
    resource: str | None = Field(
        default=None,
        description="Canonical URI of the underlying asset.",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Cross-cutting categorization tags.",
    )
    timestamp: datetime | None = Field(
        default=None,
        description="ISO 8601 datetime of last meaningful change.",
    )


class OkfFrontmatterStandardExtra(OkfFrontmatterStandard):
    """OKF v0.1 frontmatter reader that preserves unknown extension keys (§4.1)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class OkfDocument(BaseModel):
    """OKF v0.1 concept document: standard frontmatter + markdown body (§4)."""

    model_config = ConfigDict(extra="forbid")

    frontmatter: OkfFrontmatterStandard
    body: str = Field(..., description="Markdown content after frontmatter.")
