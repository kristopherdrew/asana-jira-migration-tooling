"""Shared logging helpers for CLI scripts."""

from __future__ import annotations

import logging
from typing import Optional

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(level: str | int = "INFO") -> None:
    """Configure root logging once with a consistent format."""
    if isinstance(level, str):
        normalized = level.strip().upper()
        numeric_level = logging.getLevelName(normalized)
        if isinstance(numeric_level, str):
            raise ValueError(f"Unknown log level: {level}")
    else:
        numeric_level = level

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=numeric_level, format=_DEFAULT_FORMAT)
    else:
        root.setLevel(numeric_level)


def resolve_log_level(candidate: Optional[str]) -> str:
    """Return a sanitized log level string for CLI arguments."""
    if not candidate:
        return "INFO"
    value = candidate.strip().upper()
    if isinstance(logging.getLevelName(value), int):
        return value
    raise ValueError(f"Unsupported log level: {candidate}")
