"""Test the pdf-parser webhook timeout config default.

The old hardcoded 5s timeout caused ReadTimeouts that silently dropped parse
jobs. The default is now a configurable 30.0s.
"""
from app.config import settings


def test_webhook_timeout_default():
    assert settings.webhook_timeout == 30.0
