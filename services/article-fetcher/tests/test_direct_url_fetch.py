"""Tests for the direct-URL download branch of article-fetcher's _run_fetch.

When FetchRequest carries a `url` (arXiv pdf_url), `_run_fetch` downloads the PDF
directly via requests.get and uploads it WITHOUT calling the scidownl/Sci-Hub
`fetch_article` path. When `url` is absent, the existing DOI path is unchanged.
"""
from unittest.mock import MagicMock, patch


def _fake_requests_get(content=b"%PDF-1.4 direct content", status_code=200, content_type="application/pdf"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


def test_run_fetch_direct_url_uploads_without_scidownl():
    """url set -> requests.get downloads PDF, storage.upload_pdf called, fetch_article NOT called."""
    from app import main

    with (
        patch.object(main, "storage") as mock_storage,
        patch.object(main, "redis_client") as mock_redis,
        patch.object(main, "fetch_article") as mock_fetch_article,
        patch.object(main, "requests") as mock_requests,
        patch.object(main.settings, "article_processor_webhook_url", ""),
    ):
        mock_redis.get.return_value = '{"job_id": "job1", "doi": "arXiv:2401.1", "status": "running"}'
        mock_requests.get.return_value = _fake_requests_get()

        main._run_fetch("job1", "arXiv:2401.1", conversation_id="conv1", url="https://arxiv.org/pdf/2401.1")

        # Direct path used — scidownl/Sci-Hub fetch_article must NOT be called.
        mock_fetch_article.assert_not_called()
        mock_requests.get.assert_called_once()
        assert mock_requests.get.call_args[0][0] == "https://arxiv.org/pdf/2401.1"
        # PDF uploaded under the same {job_id}.pdf object key as the DOI path.
        mock_storage.upload_pdf.assert_called_once()
        assert mock_storage.upload_pdf.call_args[0][0] == "job1.pdf"
        assert mock_storage.upload_pdf.call_args[0][1] == b"%PDF-1.4 direct content"


def test_run_fetch_no_url_uses_fetch_article():
    """url absent -> existing DOI/Sci-Hub path (fetch_article) used, requests.get NOT called."""
    from app import main

    with (
        patch.object(main, "storage") as mock_storage,
        patch.object(main, "redis_client") as mock_redis,
        patch.object(main, "fetch_article", return_value=b"%PDF-scihub") as mock_fetch_article,
        patch.object(main, "requests") as mock_requests,
        patch.object(main.settings, "article_processor_webhook_url", ""),
    ):
        mock_redis.get.return_value = '{"job_id": "job2", "doi": "10.1/x", "status": "running"}'

        main._run_fetch("job2", "10.1/x", conversation_id="conv1")

        mock_fetch_article.assert_called_once_with("10.1/x")
        mock_requests.get.assert_not_called()
        mock_storage.upload_pdf.assert_called_once_with("job2.pdf", b"%PDF-scihub")


def test_run_fetch_direct_url_non_pdf_fails():
    """A non-PDF response on the direct path marks the job failed and does not upload."""
    from app import main

    with (
        patch.object(main, "storage") as mock_storage,
        patch.object(main, "redis_client") as mock_redis,
        patch.object(main, "fetch_article") as mock_fetch_article,
        patch.object(main, "requests") as mock_requests,
    ):
        mock_redis.get.return_value = '{"job_id": "job3", "status": "running"}'
        mock_requests.get.return_value = _fake_requests_get(
            content=b"<html>not a pdf</html>", content_type="text/html"
        )

        main._run_fetch("job3", "arXiv:x", conversation_id="conv1", url="https://arxiv.org/pdf/x")

        mock_fetch_article.assert_not_called()
        mock_storage.upload_pdf.assert_not_called()
