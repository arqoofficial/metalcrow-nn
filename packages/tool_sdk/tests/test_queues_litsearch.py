from tool_sdk.queues import build_task_routes, queue_for_task


def test_queue_for_task_routes_litsearch_to_litsearch_queue() -> None:
    assert queue_for_task("litsearch.monitor") == "litsearch"


def test_build_task_routes_includes_litsearch() -> None:
    assert "litsearch.*" in build_task_routes()
