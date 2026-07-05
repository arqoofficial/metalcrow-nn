"""Retrieval-layer data models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalCandidate(BaseModel):
    id: str
    document: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
