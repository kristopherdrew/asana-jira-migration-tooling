"""HTTP client utilities for interacting with the Asana API using stdlib only."""

from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from typing import Dict, Generator, Iterable, Optional
from urllib import error, parse, request


API_BASE_URL = "https://app.asana.com/api/1.0"
USER_AGENT = "asana-export-tool/0.1 (+https://asana.com)"


@dataclass(frozen=True)
class RateLimitInfo:
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset: Optional[int] = None
    retry_after: Optional[float] = None


class AsanaHttpError(Exception):
    """Raised when the Asana API returns a non-success status code."""

    def __init__(
        self,
        status: int,
        message: str,
        body: Optional[dict] = None,
        rate_limit: Optional[RateLimitInfo] = None,
    ):
        super().__init__(f"Asana API error {status}: {message}")
        self.status = status
        self.body = body or {}
        self.rate_limit = rate_limit


class AsanaHttpClient:
    """Minimal HTTP client wrapping urllib with retry support."""

    def __init__(self, token: str, skip_ssl_verification: bool = False, timeout: int = 30):
        self._token = token
        self._timeout = timeout
        self._last_rate_limit: Optional[RateLimitInfo] = None
        context = ssl.create_default_context()
        if skip_ssl_verification:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        https_handler = request.HTTPSHandler(context=context)
        self._opener = request.build_opener(https_handler)

    @property
    def last_rate_limit(self) -> Optional[RateLimitInfo]:
        """Return the most recent rate limit metadata from the API."""
        return self._last_rate_limit

    def _build_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
    ) -> request.Request:
        query = f"?{parse.urlencode(params)}" if params else ""
        url = f"{API_BASE_URL}{endpoint}{query}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        return request.Request(url=url, headers=headers, method=method, data=data)

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        max_retries: int = 5,
        backoff_factor: float = 1.5,
    ) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            req = self._build_request(method, endpoint, params, data=data)
            try:
                with self._opener.open(req, timeout=self._timeout) as response:
                    content = response.read().decode("utf-8")
                    self._last_rate_limit = self._parse_rate_limit_headers(response.headers)
                    return json.loads(content)
            except error.HTTPError as exc:
                body_text = exc.read().decode("utf-8") if exc.fp else ""
                try:
                    parsed_body = json.loads(body_text) if body_text else {}
                except json.JSONDecodeError:
                    parsed_body = {"raw": body_text}

                if exc.code == 429:
                    rate_limit = self._parse_rate_limit_headers(exc.headers)
                    retry_after = self._parse_retry_after(exc)
                    if rate_limit:
                        self._last_rate_limit = RateLimitInfo(
                            limit=rate_limit.limit,
                            remaining=rate_limit.remaining,
                            reset=rate_limit.reset,
                            retry_after=retry_after,
                        )
                    else:
                        self._last_rate_limit = RateLimitInfo(retry_after=retry_after)
                    sleep_for = retry_after or (backoff_factor ** attempt)
                    time.sleep(sleep_for)
                    last_error = AsanaHttpError(exc.code, "Rate limit exceeded.", parsed_body, rate_limit=self._last_rate_limit)
                    continue

                if 500 <= exc.code < 600:
                    sleep_for = backoff_factor ** attempt
                    time.sleep(sleep_for)
                    self._last_rate_limit = self._parse_rate_limit_headers(exc.headers)
                    last_error = AsanaHttpError(exc.code, "Server error.", parsed_body, rate_limit=self._last_rate_limit)
                    continue

                self._last_rate_limit = self._parse_rate_limit_headers(exc.headers)
                raise AsanaHttpError(exc.code, exc.reason, parsed_body, rate_limit=self._last_rate_limit) from exc
            except error.URLError as exc:
                sleep_for = backoff_factor ** attempt
                time.sleep(sleep_for)
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("Request failed without raising an exception.")

    @staticmethod
    def _parse_retry_after(exc: error.HTTPError) -> Optional[float]:
        retry_header = exc.headers.get("Retry-After") if exc.headers else None
        if not retry_header:
            return None
        try:
            return float(retry_header)
        except ValueError:
            return None

    @staticmethod
    def _parse_rate_limit_headers(headers) -> Optional[RateLimitInfo]:
        if not headers:
            return None

        def to_int(value: Optional[str]) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        limit = to_int(headers.get("X-RateLimit-Limit"))
        remaining = to_int(headers.get("X-RateLimit-Remaining"))
        reset = to_int(headers.get("X-RateLimit-Reset"))

        if limit is None and remaining is None and reset is None:
            return None
        return RateLimitInfo(limit=limit, remaining=remaining, reset=reset)

    def get(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> dict:
        """Perform a GET request and return the parsed JSON body."""
        return self._request_with_retry("GET", endpoint, params=params)

    def post_json(self, endpoint: str, payload: dict, params: Optional[Dict[str, str]] = None) -> dict:
        """Perform a POST request with a JSON body and return the parsed JSON response."""
        body = json.dumps(payload).encode("utf-8")
        return self._request_with_retry("POST", endpoint, params=params, data=body)

    def get_paginated(
        self,
        endpoint: str,
        params: Optional[Dict[str, str]] = None,
    ) -> Generator[dict, None, None]:
        """Iterate through paginated responses and yield individual records."""
        query = dict(params or {})
        while True:
            payload = self.get(endpoint, params=query)
            data: Iterable[dict] = payload.get("data", [])
            for item in data:
                yield item
            next_page = payload.get("next_page")
            if not next_page or "offset" not in next_page:
                break
            query["offset"] = next_page["offset"]
