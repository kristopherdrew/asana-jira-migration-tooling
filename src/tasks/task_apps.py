"""Helpers for probing task app-component attachment data from Asana."""

from __future__ import annotations

from typing import Any, Dict, List

from src.lib.http_client import AsanaHttpClient, AsanaHttpError


TASK_APP_ATTACHMENT_FIELDS = [
    "gid",
    "resource_type",
    "name",
    "resource_subtype",
    "created_at",
    "download_url",
    "permanent_url",
    "host",
    "service",
    "size",
    "view_url",
    "connected_to_app",
    "parent.gid",
    "parent.resource_type",
    "parent.name",
    "parent.resource_subtype",
]

APP_LIKE_ATTACHMENT_SUBTYPES = {"external", "gdrive"}


def fetch_task_app_attachment_probe(
    client: AsanaHttpClient,
    task_gid: str,
    include_details: bool = True,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Fetch task attachments with app-component-relevant fields.

    Asana's task App Components surface is backed by URL/file attachments, so this
    probe focuses on the attachment list plus optional per-attachment detail calls.
    """
    params = {
        "parent": task_gid,
        "opt_fields": ",".join(TASK_APP_ATTACHMENT_FIELDS),
        "limit": str(page_size),
    }
    attachments = list(client.get_paginated("/attachments", params=params))
    detail_results: List[Dict[str, Any]] = []

    if include_details:
        detail_params = {"opt_fields": ",".join(TASK_APP_ATTACHMENT_FIELDS)}
        for attachment in attachments:
            attachment_gid = attachment.get("gid") if isinstance(attachment, dict) else None
            if not attachment_gid:
                continue
            try:
                payload = client.get(f"/attachments/{attachment_gid}", params=detail_params)
                detail_results.append(
                    {
                        "attachment_gid": attachment_gid,
                        "ok": True,
                        "data": payload.get("data", payload),
                    }
                )
            except AsanaHttpError as exc:
                detail_results.append(
                    {
                        "attachment_gid": attachment_gid,
                        "ok": False,
                        "status": exc.status,
                        "body": exc.body,
                    }
                )

    return {
        "task_gid": task_gid,
        "summary": summarize_app_attachments(attachments),
        "attachments": attachments,
        "attachment_details": detail_results,
    }


def summarize_app_attachments(attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a compact count summary for app-like task attachments."""
    by_subtype: Dict[str, int] = {}
    by_host: Dict[str, int] = {}
    connected_to_app = 0
    app_like = 0

    for attachment in attachments:
        subtype = str(attachment.get("resource_subtype") or "")
        host = str(attachment.get("host") or "")
        if subtype:
            by_subtype[subtype] = by_subtype.get(subtype, 0) + 1
        if host:
            by_host[host] = by_host.get(host, 0) + 1
        if attachment.get("connected_to_app") is True:
            connected_to_app += 1
        if subtype in APP_LIKE_ATTACHMENT_SUBTYPES or host in APP_LIKE_ATTACHMENT_SUBTYPES:
            app_like += 1

    return {
        "total_attachments": len(attachments),
        "app_like_attachments": app_like,
        "connected_to_app": connected_to_app,
        "by_resource_subtype": by_subtype,
        "by_host": by_host,
    }
