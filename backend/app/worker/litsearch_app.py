"""Celery worker entrypoint for the litsearch queue (task names `litsearch.*`,
queue `litsearch` — see `tool_sdk.queues`). Run with:

    celery -A app.worker.litsearch_app worker -Q litsearch

Reuses the single producer `celery_app` from `app.services.tasks` (same
broker/routes as the rest of the backend) rather than building a second
`Celery(...)` instance — `app.worker.litsearch_tasks` registers its tasks
onto that shared app via the `@celery_app.task` decorator.
"""

import app.worker.litsearch_tasks  # noqa: F401  (import-for-side-effect: registers litsearch.* tasks)
from app.services.tasks import celery_app

__all__ = ["celery_app"]
