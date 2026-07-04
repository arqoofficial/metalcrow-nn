"""S3/MinIO storage client for the article-fetcher — immers-ready single-client build.

Handed to metalcrow (litellm-gw) by chemcrow-deploy, 2026-07-03.

Differences from Cosmetica's original (which targeted in-cluster MinIO):
  - **path-style addressing** (`addressing_style="path"`) — REQUIRED by immers S3
    (`s3.msk.immers.cloud`); boto3 defaults to virtual-hosted for custom endpoints.
  - **full-URL endpoint** — pass the whole `https://s3.msk.immers.cloud` (the old
    build hardcoded `http://{endpoint}`, which breaks against an https public S3).
  - **single client** — for a public S3 the read/write endpoint == the presign
    endpoint, so the internal/presign client split (and the SigV4-host presign
    gotcha) disappears. One client signs everything against the reachable host.
  - **region** configurable (default `ru-msk`). immers is lenient (us-east-1 also
    works) but signing the real region is correct + safe.

Drop-in: same public method names as the original (`ensure_bucket`, `upload_pdf`,
`presign_url`), so `main.py`'s call sites don't change.

Env wiring (article-fetcher config.py / compose):
    MINIO_ENDPOINT         = https://s3.msk.immers.cloud   # full URL, WITH scheme
    MINIO_PUBLIC_ENDPOINT  = https://s3.msk.immers.cloud   # same for public S3 (or omit)
    MINIO_ACCESS_KEY / MINIO_SECRET_KEY  = from ~/.config/immers/s3-ec2.env
    MINIO_BUCKET           = metalcrow-articles
    MINIO_REGION           = ru-msk
Then construct:  StorageClient(endpoint_url=settings.minio_endpoint, ...).
"""
import logging

import boto3
from botocore.client import Config

logger = logging.getLogger(__name__)


class StorageClient:
    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "ru-msk",
        public_endpoint: str | None = None,
    ):
        """`endpoint_url` is a FULL URL incl. scheme, e.g. https://s3.msk.immers.cloud.

        `public_endpoint` is only needed if the URL clients use to WRITE differs from
        the one a browser uses to DOWNLOAD (e.g. internal MinIO vs a public gateway).
        For a public S3 leave it None → one client does everything.
        """
        self._bucket = bucket
        cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # immers requires path-style
            # immers S3 rejects boto3 1.36+'s default aws-chunked streaming checksums
            # on PutObject (-> "MissingContentLength"). Restrict checksums to ops that
            # require them so PutObject sends a plain request with a Content-Length.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=cfg,
            region_name=region,
        )
        # Only build a second client if a distinct public endpoint is supplied.
        if public_endpoint and public_endpoint != endpoint_url:
            self._presign_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=cfg,
                region_name=region,
            )
        else:
            self._presign_client = self._client

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            logger.warning("Bucket %s not found, creating", self._bucket)
            self._client.create_bucket(Bucket=self._bucket)

    def upload_pdf(self, key: str, data: bytes) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType="application/pdf",
            ContentLength=len(data),
        )
        logger.info("Uploaded %s to bucket %s", key, self._bucket)

    def presign_url(self, key: str, expires_in: int = 3600) -> str:
        # SigV4 signs the Host; with a single public-endpoint client there is no
        # post-hoc host rewrite, so the presigned URL validates cleanly.
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
