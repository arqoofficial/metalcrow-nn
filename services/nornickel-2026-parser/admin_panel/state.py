"""In-memory panel state and error ring buffer."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.presentation.schemas import StatisticsResponse


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class ErrorSource(str, Enum):
    api = "api"
    services = "services"
    panel = "panel"
    action = "action"


class ErrorEntry(BaseModel):
    timestamp: datetime
    severity: Severity
    source: ErrorSource
    message: str


class ServiceRow(BaseModel):
    component: str
    status: str
    details: str
    updated_at: datetime


class QueueDepthRow(BaseModel):
    component: str
    queue_key: str
    depth: int | None = None


class PanelState(BaseModel):
    services: list[ServiceRow] = Field(default_factory=list)
    queue_depths: list[QueueDepthRow] = Field(default_factory=list)
    statistics: StatisticsResponse | None = None
    errors: list[ErrorEntry] = Field(default_factory=list)
    last_refresh_at: datetime | None = None
    last_refresh_ms: float | None = None

    def add_error(
        self,
        message: str,
        *,
        severity: Severity = Severity.ERROR,
        source: ErrorSource = ErrorSource.panel,
        max_size: int = 50,
    ) -> None:
        self.errors.insert(
            0,
            ErrorEntry(
                timestamp=datetime.now().astimezone(),
                severity=severity,
                source=source,
                message=message,
            ),
        )
        if len(self.errors) > max_size:
            self.errors = self.errors[:max_size]
