"""Shared Jira Cloud REST client with Basic auth, retries, and issue helpers.

Provides a single ``JiraClient`` used by the attachment sync and the team/project
issue creator. Built on the standard library (``urllib`` + ``ssl``) to honor the
project's stdlib-only constraint, and respects the resolved ``skip_ssl_verification``
credential setting.
"""

from __future__ import annotations

import base64
import json
import random
import ssl
import time
from typing import Dict, List, Optional, Set
from urllib import error, parse, request


class JiraApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Jira API error {status}: {message}")
        self.status = status
        # Raw error body/reason as returned by Jira, so callers can inspect the
        # error payload (e.g. the per-field ``errors`` dict) beyond the message.
        self.body = message


class JiraClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        token: str,
        skip_ssl_verification: bool = False,
        timeout: int = 30,
        user_agent: str = "asana-jira-migration/1.0",
    ):
        self._base_url = base_url.rstrip("/")
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
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        max_retries: int = 8,
        backoff: float = 1.5,
    ) -> bytes:
        url = f"{self._base_url}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            req = request.Request(url=url, method=method, data=data)
            req.add_header("Authorization", self._auth_header)
            req.add_header("User-Agent", self._user_agent)
            req.add_header("Accept", "application/json")
            if headers:
                for key, value in headers.items():
                    req.add_header(key, value)
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    return resp.read()
            except error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = self._parse_retry_after(exc)
                    # Add a little jitter so parallel workers don't all wake up
                    # and re-hit the rate limit in lockstep.
                    sleep_for = (retry_after or (backoff ** attempt)) + random.uniform(0, 0.5)
                    time.sleep(sleep_for)
                    last_error = JiraApiError(exc.code, "Rate limited")
                    continue
                if 500 <= exc.code < 600:
                    time.sleep(backoff ** attempt)
                    last_error = JiraApiError(exc.code, "Server error")
                    continue
                body = exc.read().decode("utf-8") if exc.fp else exc.reason
                raise JiraApiError(exc.code, body or exc.reason) from exc
            except error.URLError as exc:
                time.sleep(backoff ** attempt)
                last_error = exc
        if last_error:
            raise last_error
        raise JiraApiError(0, "Request failed without response")

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
        headers: Dict[str, str] = {}
        data: Optional[bytes] = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        raw = self._request(method, path, headers=headers, data=data)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise JiraApiError(0, f"Unable to parse JSON response for {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Issue creation + create metadata
    # ------------------------------------------------------------------
    def create_issue(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Create a single issue via ``POST /rest/api/3/issue``.

        Returns the parsed response (``{"id", "key", "self"}``)."""
        return self._request_json("POST", "/rest/api/3/issue", payload=payload)

    def get_createmeta_issue_types(self, project_key: str) -> List[Dict[str, object]]:
        """Return the creatable issue types for a project.

        Each entry includes at least ``id`` and ``name``."""
        encoded = parse.quote(project_key, safe="")
        path = f"/rest/api/3/issue/createmeta/{encoded}/issuetypes"
        data = self._request_json("GET", path)
        return list(data.get("issueTypes", []) or data.get("values", []))

    def get_createmeta_fields(self, project_key: str, issue_type_id: str) -> Dict[str, Dict[str, object]]:
        """Return creatable fields for a project + issue type keyed by field name.

        Each value carries ``fieldId``, ``required``, ``schema`` and (when present)
        ``allowedValues`` so callers can format values per field type."""
        encoded_project = parse.quote(project_key, safe="")
        encoded_type = parse.quote(str(issue_type_id), safe="")
        path = f"/rest/api/3/issue/createmeta/{encoded_project}/issuetypes/{encoded_type}"
        fields_by_name: Dict[str, Dict[str, object]] = {}
        next_page_token: Optional[str] = None
        while True:
            page_path = path
            if next_page_token:
                page_path = f"{path}?startAt={parse.quote(str(next_page_token))}"
            data = self._request_json("GET", page_path)
            for field in data.get("fields", data.get("values", [])) or []:
                name = field.get("name")
                if name:
                    fields_by_name[name] = field
            # createmeta field listing is paginated; stop when fully drained.
            total = data.get("total")
            start_at = data.get("startAt")
            max_results = data.get("maxResults")
            if (
                total is not None
                and start_at is not None
                and max_results
                and start_at + max_results < total
            ):
                next_page_token = start_at + max_results
                continue
            break
        return fields_by_name

    # ------------------------------------------------------------------
    # Attachments (used by sync_jira_attachments)
    # ------------------------------------------------------------------
    def list_issue_attachment_names(self, jira_key: str) -> Set[str]:
        encoded_key = parse.quote(jira_key)
        path = f"/rest/api/3/issue/{encoded_key}?fields=attachment"
        payload = self._request("GET", path)
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise JiraApiError(0, f"Unable to parse attachment response for {jira_key}: {exc}") from exc
        attachments = data.get("fields", {}).get("attachment", [])
        names: Set[str] = set()
        for entry in attachments:
            filename = entry.get("filename")
            if filename:
                names.add(filename)
        return names

    def upload_attachment(self, jira_key: str, filename: str, file_path) -> None:
        boundary = f"----AsanaSyncBoundary{time.time_ns()}"
        encoded_key = parse.quote(jira_key)
        path = f"/rest/api/3/issue/{encoded_key}/attachments"
        headers = {
            "X-Atlassian-Token": "no-check",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        with open(file_path, "rb") as handle:
            file_bytes = handle.read()
        body = self._build_multipart(boundary, filename, file_bytes)
        self._request("POST", path, headers=headers, data=body)

    @staticmethod
    def _build_multipart(boundary: str, filename: str, file_bytes: bytes) -> bytes:
        parts = [
            f"--{boundary}".encode("utf-8"),
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"".encode("utf-8"),
            b"Content-Type: application/octet-stream",
            b"",
            file_bytes,
            f"--{boundary}--".encode("utf-8"),
            b"",
        ]
        return b"\r\n".join(parts)
