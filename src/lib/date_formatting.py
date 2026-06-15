"""Helpers for normalizing ISO timestamps in report exports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (AttributeError, ValueError):
        return None


def format_report_date(value: Optional[str]) -> str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return ""
    return parsed.astimezone(timezone.utc).date().isoformat()
