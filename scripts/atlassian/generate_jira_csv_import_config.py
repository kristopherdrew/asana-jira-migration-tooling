#!/usr/bin/env python3
"""Generate a reusable Jira CSV importer configuration."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, Optional

from src.lib.config import (
    load_credentials,
    resolve_credentials_path,
    resolve_jira_base_url,
    resolve_jira_site,
)
from src.lib.jira_client import JiraClient
from src.lib.jira_csv_import_config import (
    load_archive_import_settings,
    normalize_fields,
    profile_import_config_path,
    render_reusable_import_config,
    review_archive_import_settings,
    save_archive_import_settings,
    write_reusable_import_config,
)
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively review Jira mappings and generate a reusable importer "
            "configuration from the task export's canonical columns."
        )
    )
    parser.add_argument(
        "--output",
        help="Configuration file to write (default: the active profile's saved Jira config).",
    )
    parser.add_argument(
        "--csv-name",
        default="jira_tasks.csv",
        help="CSV filename recorded inside the reusable configuration (default: jira_tasks.csv).",
    )
    parser.add_argument("--prod", action="store_true", help="Use the production Jira site.")
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Review every saved field and resolution mapping before generation.",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials()
    if not credentials.atlassian_username or not credentials.atlassian_token:
        raise SystemExit(
            "ATLASSIAN_USERNAME and ATLASSIAN_TOKEN are required to discover Jira CSV mappings."
        )
    selected_site_key = "production_jira" if args.prod and not args.jira_site else args.jira_site
    selected_site = resolve_jira_site(credentials, selected_site_key)
    try:
        jira_base_url = resolve_jira_base_url(
            credentials,
            prod=args.prod,
            site_key=args.jira_site,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not jira_base_url:
        raise SystemExit("Jira base URL is required.")

    client = JiraClient(
        base_url=jira_base_url.rstrip("/"),
        username=credentials.atlassian_username,
        token=credentials.atlassian_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    fields = normalize_fields(client.get_custom_fields())
    env_path = resolve_credentials_path()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else profile_import_config_path(env_path, site_key=selected_site.key)
    )
    saved_settings = load_archive_import_settings(env_path, selected_site.key)
    reviewed = review_archive_import_settings(
        saved_settings,
        fields,
        selected_site.name,
        lambda settings: render_reusable_import_config(settings, args.csv_name),
        force_configure=args.configure,
    )
    if not reviewed:
        logger.info("Jira CSV importer configuration generation cancelled.")
        return

    export_settings, reusable_settings = reviewed
    save_archive_import_settings(env_path, selected_site.key, reusable_settings)
    config_path = write_reusable_import_config(
        output_path,
        export_settings,
        args.csv_name,
    )
    logger.info("Wrote reusable Jira CSV importer configuration to %s", config_path)

    raw = input(f"Rename config file? [{config_path.name}] (press Enter to keep): ").strip()
    if raw:
        new_name = raw if raw.endswith(".txt") else f"{raw}.txt"
        new_path = config_path.with_name(new_name)
        config_path.rename(new_path)
        logger.info("Renamed to %s", new_path)


if __name__ == "__main__":
    main()
