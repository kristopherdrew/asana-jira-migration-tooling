import io
import json
from typing import Dict, Optional
from unittest import TestCase
from urllib import error

from src.lib.http_client import AsanaHttpClient, AsanaHttpError


class FakeResponse:
    def __init__(self, payload: Dict, headers: Optional[Dict[str, str]] = None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SuccessOpener:
    def __init__(self, response: FakeResponse):
        self._response = response

    def open(self, request, timeout=None):
        return self._response


class Always429Opener:
    def __init__(self, headers: Dict[str, str]):
        self.headers = headers
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        return _raise_http_error(request.full_url, self.headers)


def _raise_http_error(url: str, headers: Dict[str, str]):
    class HeaderDict(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    hdrs = HeaderDict(headers)
    body = io.BytesIO(b'{"errors": []}')
    raise error.HTTPError(url, 429, "Too Many Requests", hdrs, body)


class HttpClientRateLimitTest(TestCase):
    def test_last_rate_limit_populates_on_success(self) -> None:
        headers = {"X-RateLimit-Limit": "1500", "X-RateLimit-Remaining": "1499", "X-RateLimit-Reset": "60"}
        response = FakeResponse({"data": []}, headers=headers)
        client = AsanaHttpClient(token="token")
        client._opener = SuccessOpener(response)  # type: ignore[attr-defined]

        payload = client.get("/users/me")

        self.assertEqual(payload["data"], [])
        self.assertIsNotNone(client.last_rate_limit)
        assert client.last_rate_limit is not None
        self.assertEqual(client.last_rate_limit.limit, 1500)
        self.assertEqual(client.last_rate_limit.remaining, 1499)
        self.assertEqual(client.last_rate_limit.reset, 60)

    def test_rate_limit_error_captures_metadata(self) -> None:
        headers = {
            "Retry-After": "0.001",
            "X-RateLimit-Limit": "1500",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "60",
        }
        client = AsanaHttpClient(token="token")
        client._opener = Always429Opener(headers)  # type: ignore[attr-defined]

        with self.assertRaises(AsanaHttpError) as ctx:
            client.get("/users/me")

        error_obj = ctx.exception
        self.assertIsNotNone(error_obj.rate_limit)
        assert error_obj.rate_limit is not None
        self.assertEqual(error_obj.rate_limit.limit, 1500)
        self.assertEqual(error_obj.rate_limit.remaining, 0)
        self.assertEqual(error_obj.rate_limit.reset, 60)
        self.assertIsNotNone(client.last_rate_limit)
        assert client.last_rate_limit is not None
        self.assertAlmostEqual(client.last_rate_limit.retry_after or 0.0, 0.001, places=3)
