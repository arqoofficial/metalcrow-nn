import io
import pytest
from unittest.mock import MagicMock, patch, call


def test_upload_pdf_calls_put_object():
    mock_client = MagicMock()
    with patch("app.storage.boto3.client", return_value=mock_client):
        from app.storage import StorageClient
        client = StorageClient(
            endpoint="localhost:9000",
            access_key="key",
            secret_key="secret",
            bucket="articles",
            public_endpoint="http://localhost:9000",
        )
        client.upload_pdf("job123.pdf", b"%PDF-1.4 test content")
        mock_client.put_object.assert_called_once_with(
            Bucket="articles",
            Key="job123.pdf",
            Body=b"%PDF-1.4 test content",
            ContentType="application/pdf",
        )


def test_presign_url_calls_generate_presigned_url():
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "http://localhost:9092/articles/job123.pdf?sig=abc"
    with patch("app.storage.boto3.client", return_value=mock_client):
        from app.storage import StorageClient
        client = StorageClient(
            endpoint="articles-minio:9000",
            access_key="key",
            secret_key="secret",
            bucket="articles",
            public_endpoint="http://localhost:9092",
        )
        url = client.presign_url("job123.pdf", expires_in=3600)
        assert "job123.pdf" in url
        # The presign must run on the PUBLIC-endpoint client so the SigV4
        # signature is valid for the host the browser actually reaches.
        client._presign_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "articles", "Key": "job123.pdf"},
            ExpiresIn=3600,
        )


def test_presign_client_uses_public_endpoint_no_string_rewrite():
    """The presign client must be built with endpoint_url == public endpoint,
    and presign_url must NOT string-rewrite the host (which would break SigV4)."""
    created = []

    def fake_boto_client(service, **kwargs):
        m = MagicMock()
        m._kwargs = kwargs
        created.append(m)
        # Return a presigned URL already pointing at the configured endpoint.
        m.generate_presigned_url.return_value = (
            kwargs["endpoint_url"].rstrip("/") + "/articles/job123.pdf?X-Amz-Signature=abc"
        )
        return m

    with patch("app.storage.boto3.client", side_effect=fake_boto_client):
        from app.storage import StorageClient
        client = StorageClient(
            endpoint="articles-minio:9000",
            access_key="key",
            secret_key="secret",
            bucket="articles",
            public_endpoint="http://localhost:9092",
        )
        url = client.presign_url("job123.pdf")

    # A dedicated presign client must exist, signed for the public host.
    assert client._presign_client._kwargs["endpoint_url"] == "http://localhost:9092"
    # The internal client signs/uploads against the internal endpoint.
    assert client._client._kwargs["endpoint_url"] == "http://articles-minio:9000"
    # The returned URL points at the public host (signed for it, not rewritten).
    assert url.startswith("http://localhost:9092/articles/job123.pdf")
    assert "articles-minio" not in url


def test_ensure_bucket_creates_if_missing():
    mock_client = MagicMock()
    mock_client.head_bucket.side_effect = Exception("NoSuchBucket")
    with patch("app.storage.boto3.client", return_value=mock_client):
        from app.storage import StorageClient
        client = StorageClient(
            endpoint="localhost:9000",
            access_key="key",
            secret_key="secret",
            bucket="articles",
            public_endpoint="http://localhost:9000",
        )
        client.ensure_bucket()
        mock_client.create_bucket.assert_called_once_with(Bucket="articles")
