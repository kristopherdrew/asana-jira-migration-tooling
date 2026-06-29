"""Shared Confluence Cloud REST client (v2 API) with Basic auth and retries.

Mirrors :mod:`src.lib.jira_client`: standard-library-only (``urllib`` + ``ssl``),
honors the resolved ``skip_ssl_verification`` credential, and retries 429/5xx with
jittered backoff. Used by ``publish_markdown_to_confluence.py`` to create/update
pages from already-converted storage-format bodies.

Confluence Cloud lives under ``/wiki`` on the Atlassian site host, so pass the
site base URL (``https://example.atlassian.net``) and this client appends the
``/wiki/api/v2`` prefix itself.
"""

from __future__ import annotations

import base64
import json
import random
import ssl
import time
from typing import Dict, Optional
from urllib import error, parse, request


class ConfluenceApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Confluence API error {status}: {message}")
        self.status = status
        self.body = message


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        token: str,
        skip_ssl_verification: bool = False,
        timeout: int = 30,
        user_agent: str = "asana-jira-migration/1.0",
    ):
        # Accept either the bare site URL or one that already includes /wiki.
        site = base_url.rstrip("/")
        if site.endswith("/wiki"):
            site = site[: -len("/wiki")]
        self._api_base = f"{site}/wiki/api/v2"
        auth_bytes = f"{username}:{token}".encode("utf-8")
        self._auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"
        context = ssl.create_default_context()
        if skip_ssl_verification:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = request.build_opener(request.HTTPSHandler(context=context))
        self._timeout = timeout
        self._user_agent = user_agent

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[bytes] = None,
        max_retries: int = 8,
        backoff: float = 1.5,
    ) -> bytes:
        url = f"{self._api_base}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            req = request.Request(url=url, method=method, data=data)
            req.add_header("Authorization", self._auth_header)
            req.add_header("User-Agent", self._user_agent)
            req.add_header("Accept", "application/json")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    return resp.read()
            except error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = self._parse_retry_after(exc)
                    sleep_for = (retry_after or (backoff ** attempt)) + random.uniform(0, 0.5)
                    time.sleep(sleep_for)
                    last_error = ConfluenceApiError(exc.code, "Rate limited")
                    continue
                if 500 <= exc.code < 600:
                    time.sleep(backoff ** attempt)
                    last_error = ConfluenceApiError(exc.code, "Server error")
                    continue
                body = exc.read().decode("utf-8") if exc.fp else exc.reason
                raise ConfluenceApiError(exc.code, body or exc.reason) from exc
            except error.URLError as exc:
                time.sleep(backoff ** attempt)
                last_error = exc
        if last_error:
            raise last_error
        raise ConfluenceApiError(0, "Request failed without response")

    @staticmethod
    def _parse_retry_after(exc: error.HTTPError) -> Optional[float]:
        retry_header = exc.headers.get("Retry-After") if exc.headers else None
        if not retry_header:
            return None
        try:
            return float(retry_header)
        except ValueError:
            return None

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        raw = self._request(method, path, data=data)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConfluenceApiError(0, f"Unable to parse JSON for {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Spaces + pages
    # ------------------------------------------------------------------
    def get_space_id(self, space_key: str) -> str:
        """Resolve a space key (e.g. ``DOCS``) to its numeric space id."""
        path = f"/spaces?keys={parse.quote(space_key)}"
        data = self._request_json("GET", path)
        results = data.get("results") or []
        if not results:
            raise ConfluenceApiError(404, f"No Confluence space found for key {space_key!r}.")
        return str(results[0]["id"])

    def find_page(self, space_id: str, title: str) -> Optional[Dict[str, object]]:
        """Return the current page matching ``title`` in the space, or ``None``.

        The returned dict includes at least ``id`` and ``version.number`` so the
        caller can issue an in-place update without a separate read."""
        path = (
            f"/pages?space-id={parse.quote(space_id)}"
            f"&title={parse.quote(title)}&status=current"
        )
        data = self._request_json("GET", path)
        for page in data.get("results") or []:
            if str(page.get("title")) == title:
                return page
        return None

    def create_page(
        self,
        space_id: str,
        title: str,
        storage_body: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage_body},
        }
        if parent_id:
            payload["parentId"] = parent_id
        return self._request_json("POST", "/pages", payload=payload)

    def update_page(
        self,
        page_id: str,
        title: str,
        storage_body: str,
        version_number: int,
        parent_id: Optional[str] = None,
    ) -> Dict[str, object]:
        """Update a page in place. ``version_number`` is the *current* version;
        Confluence requires the next version number on write."""
        payload: Dict[str, object] = {
            "id": page_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage_body},
            "version": {"number": version_number + 1},
        }
        if parent_id:
            payload["parentId"] = parent_id
        return self._request_json("PUT", f"/pages/{parse.quote(page_id)}", payload=payload)
