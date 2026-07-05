import json

import httpx
import respx

from app.core.config import settings
from app.services import litsearch_client

BASE = settings.ARTICLE_FETCHER_URL


# --- search -----------------------------------------------------------------


@respx.mock
def test_search_parses_results() -> None:
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"doi": "10.1/x", "title": "Ni leaching"}]},
        )
    )
    assert litsearch_client.search("nickel leaching", 5) == [
        {"doi": "10.1/x", "title": "Ni leaching"}
    ]


@respx.mock
def test_search_sends_query_params() -> None:
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    litsearch_client.search("nickel leaching", 3)
    assert route.calls.last.request.url.params["query"] == "nickel leaching"
    assert route.calls.last.request.url.params["max_results"] == "3"


@respx.mock
def test_search_http_error_returns_empty_list() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(500))
    assert litsearch_client.search("nickel leaching", 5) == []


@respx.mock
def test_search_connection_error_returns_empty_list() -> None:
    respx.get(f"{BASE}/search").mock(side_effect=httpx.ConnectError("down"))
    assert litsearch_client.search("nickel leaching", 5) == []


@respx.mock
def test_search_non_dict_json_returns_empty_list() -> None:
    """200 with a bare JSON array (not an object) must degrade to [], not
    raise AttributeError from calling .get() on a list."""
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=[]))
    assert litsearch_client.search("nickel leaching", 5) == []


# --- search_ru ----------------------------------------------------------


@respx.mock
def test_search_ru_parses_results() -> None:
    respx.get(f"{BASE}/search_ru").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "doi": None,
                        "title": "Извлечение никеля",
                        "authors": "А. Б.",
                        "year": 2020,
                        "abstract": "аннотация",
                        "fulltext": "полный текст статьи" * 100,
                        "pdf_url": None,
                        "citation_count": None,
                        "source": "cyberleninka",
                        "url": "https://cyberleninka.ru/article/n/x",
                    }
                ]
            },
        )
    )
    results = litsearch_client.search_ru("никель", 5)
    assert len(results) == 1
    assert results[0]["title"] == "Извлечение никеля"
    assert results[0]["doi"] is None
    assert "полный текст" in results[0]["fulltext"]


@respx.mock
def test_search_ru_sends_query_params() -> None:
    route = respx.get(f"{BASE}/search_ru").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    litsearch_client.search_ru("никель", 3)
    assert route.calls.last.request.url.params["query"] == "никель"
    assert route.calls.last.request.url.params["max_results"] == "3"


@respx.mock
def test_search_ru_http_error_returns_empty_list() -> None:
    respx.get(f"{BASE}/search_ru").mock(return_value=httpx.Response(500))
    assert litsearch_client.search_ru("никель", 5) == []


@respx.mock
def test_search_ru_connection_error_returns_empty_list() -> None:
    respx.get(f"{BASE}/search_ru").mock(side_effect=httpx.ConnectError("down"))
    assert litsearch_client.search_ru("никель", 5) == []


@respx.mock
def test_search_ru_non_dict_json_returns_empty_list() -> None:
    respx.get(f"{BASE}/search_ru").mock(return_value=httpx.Response(200, json=[]))
    assert litsearch_client.search_ru("никель", 5) == []


# --- resolve ------------------------------------------------------------


@respx.mock
def test_resolve_parses_payload() -> None:
    respx.get(f"{BASE}/resolve").mock(
        return_value=httpx.Response(
            200, json={"doi": "10.1/x", "title": "Ni leaching", "year": 2020}
        )
    )
    assert litsearch_client.resolve("Ni leaching") == {
        "doi": "10.1/x",
        "title": "Ni leaching",
        "year": 2020,
    }


@respx.mock
def test_resolve_http_error_returns_none() -> None:
    respx.get(f"{BASE}/resolve").mock(return_value=httpx.Response(500))
    assert litsearch_client.resolve("Ni leaching") is None


@respx.mock
def test_resolve_not_found_returns_none() -> None:
    respx.get(f"{BASE}/resolve").mock(
        return_value=httpx.Response(404, json={"detail": "No DOI found"})
    )
    assert litsearch_client.resolve("Ni leaching") is None


@respx.mock
def test_resolve_connection_error_returns_none() -> None:
    respx.get(f"{BASE}/resolve").mock(side_effect=httpx.ConnectError("down"))
    assert litsearch_client.resolve("Ni leaching") is None


# --- fetch_async --------------------------------------------------------


@respx.mock
def test_fetch_async_returns_job_id() -> None:
    respx.post(f"{BASE}/fetch").mock(
        return_value=httpx.Response(202, json={"job_id": "j1", "status": "pending"})
    )
    result = litsearch_client.fetch_async("10.1/x", url=None, conversation_id="conv1")
    assert result == "j1"


@respx.mock
def test_fetch_async_sends_payload() -> None:
    route = respx.post(f"{BASE}/fetch").mock(
        return_value=httpx.Response(202, json={"job_id": "j1", "status": "pending"})
    )
    litsearch_client.fetch_async(
        "10.1/x", url="https://example.com/x.pdf", conversation_id="conv1"
    )
    body = route.calls.last.request.content
    payload = json.loads(body)
    assert payload == {
        "doi": "10.1/x",
        "url": "https://example.com/x.pdf",
        "conversation_id": "conv1",
    }


@respx.mock
def test_fetch_async_http_error_returns_none() -> None:
    respx.post(f"{BASE}/fetch").mock(return_value=httpx.Response(500))
    assert (
        litsearch_client.fetch_async("10.1/x", url=None, conversation_id="conv1")
        is None
    )


@respx.mock
def test_fetch_async_connection_error_returns_none() -> None:
    respx.post(f"{BASE}/fetch").mock(side_effect=httpx.ConnectError("down"))
    assert (
        litsearch_client.fetch_async("10.1/x", url=None, conversation_id="conv1")
        is None
    )


@respx.mock
def test_fetch_async_non_dict_json_returns_none() -> None:
    """200 with a bare JSON string (not an object) must degrade to None, not
    raise AttributeError from calling .get() on a str."""
    respx.post(f"{BASE}/fetch").mock(return_value=httpx.Response(202, json="oops"))
    assert (
        litsearch_client.fetch_async("10.1/x", url=None, conversation_id="conv1")
        is None
    )


# --- job_status -----------------------------------------------------------


@respx.mock
def test_job_status_parses_payload() -> None:
    respx.get(f"{BASE}/jobs/j1").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "j1",
                "status": "done",
                "object_key": "j1.pdf",
                "url": "https://minio/j1.pdf",
                "error": None,
            },
        )
    )
    result = litsearch_client.job_status("j1")
    assert result is not None
    assert result["status"] == "done"
    assert result["object_key"] == "j1.pdf"


@respx.mock
def test_job_status_not_found_returns_none() -> None:
    respx.get(f"{BASE}/jobs/j1").mock(
        return_value=httpx.Response(404, json={"detail": "Job not found"})
    )
    assert litsearch_client.job_status("j1") is None


@respx.mock
def test_job_status_connection_error_returns_none() -> None:
    respx.get(f"{BASE}/jobs/j1").mock(side_effect=httpx.ConnectError("down"))
    assert litsearch_client.job_status("j1") is None


# --- fetch_sync -----------------------------------------------------------


@respx.mock
def test_fetch_sync_parses_payload() -> None:
    respx.post(f"{BASE}/fetch/sync").mock(
        return_value=httpx.Response(
            200,
            json={
                "doi": "10.1/x",
                "object_key": "abc.pdf",
                "url": "https://minio/abc.pdf",
            },
        )
    )
    result = litsearch_client.fetch_sync("10.1/x", url=None)
    assert result == {
        "doi": "10.1/x",
        "object_key": "abc.pdf",
        "url": "https://minio/abc.pdf",
    }


@respx.mock
def test_fetch_sync_http_error_returns_none() -> None:
    respx.post(f"{BASE}/fetch/sync").mock(
        return_value=httpx.Response(502, json={"detail": "No PDF found"})
    )
    assert litsearch_client.fetch_sync("10.1/x", url=None) is None


@respx.mock
def test_fetch_sync_connection_error_returns_none() -> None:
    respx.post(f"{BASE}/fetch/sync").mock(side_effect=httpx.ConnectError("down"))
    assert litsearch_client.fetch_sync("10.1/x", url=None) is None
