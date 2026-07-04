import os
import pytest
from unittest.mock import patch, MagicMock


def test_fetch_returns_pdf_bytes_on_success(tmp_path):
    fake_pdf = b"%PDF-1.4 fake content"

    def fake_download(doi, paper_type, out, **kwargs):
        with open(out, "wb") as f:
            f.write(fake_pdf)

    with patch("app.fetcher.scihub_download", side_effect=fake_download):
        from app.fetcher import fetch_article
        result = fetch_article("10.1234/test")
        assert result == fake_pdf


def test_fetch_raises_on_empty_file(tmp_path):
    def fake_download_empty(doi, paper_type, out, **kwargs):
        open(out, "wb").close()  # empty file

    with patch("app.fetcher.scihub_download", side_effect=fake_download_empty):
        from app.fetcher import fetch_article, FetchError
        with pytest.raises(FetchError, match="empty"):
            fetch_article("10.1234/notfound")


def test_fetch_raises_on_download_exception():
    with patch("app.fetcher.scihub_download", side_effect=Exception("Connection refused")):
        from app.fetcher import fetch_article, FetchError
        with pytest.raises(FetchError, match="Connection refused"):
            fetch_article("10.1234/broken")
