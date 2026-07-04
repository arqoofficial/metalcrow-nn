import os

from celery import Celery

from tool_sdk.queues import QUEUE_PARSE_DOCLING, build_task_routes

app = Celery(
    "svc_parse_docling",
    broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    include=["parse_docling.tasks"],
)
app.conf.task_default_queue = QUEUE_PARSE_DOCLING
app.conf.task_routes = build_task_routes()
app.conf.task_ignore_result = True
