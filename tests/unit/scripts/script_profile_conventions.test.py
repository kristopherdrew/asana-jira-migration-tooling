from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase

SCRIPTS_ROOT = Path(__file__).resolve().parents[3] / "scripts"
DATABASE_FLAG = '"--database"'
OPENS_DATABASE = re.compile(r"\bDatabase\(")
HARDCODED_DB = re.compile(r"""Database\(\s*(?:db_path\s*=\s*)?(?:DEFAULT_DB_PATH\b|["']data/)""")
LITERAL_DB_DEFAULT = re.compile(r"""default\s*=\s*DEFAULT_DB_PATH\b""")
POSITIONAL_SITE_ARG = re.compile(r"resolve_jira_base_url\(\s*[\w.]+\s*,\s*(?!prod\s*=|site_key\s*=)[^\s)]")
DEFINES_MAIN = re.compile(r"^def main\(", re.MULTILINE)
DRIVER_SCRIPTS = {"interactive_cli.py"}


def _script_files() -> list[Path]:
    return sorted(p for p in SCRIPTS_ROOT.rglob("*.py") if p.name != "__init__.py")


class ScriptProfileConventionsTest(TestCase):
    def test_no_script_hardcodes_the_database_path(self) -> None:
        offenders = [
            str(path.relative_to(SCRIPTS_ROOT))
            for path in _script_files()
            if HARDCODED_DB.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "Scripts must resolve the DB via --database or resolve_default_db_path()")

    def test_database_flag_default_is_profile_aware(self) -> None:
        offenders = [
            str(path.relative_to(SCRIPTS_ROOT))
            for path in _script_files()
            if DATABASE_FLAG in path.read_text(encoding="utf-8")
            and LITERAL_DB_DEFAULT.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "--database must default to resolve_default_db_path(), not the literal path")

    def test_scripts_that_open_the_database_expose_a_database_flag(self) -> None:
        offenders = []
        for path in _script_files():
            if path.name in DRIVER_SCRIPTS:
                continue
            source = path.read_text(encoding="utf-8")
            if DEFINES_MAIN.search(source) and OPENS_DATABASE.search(source) and DATABASE_FLAG not in source:
                offenders.append(str(path.relative_to(SCRIPTS_ROOT)))
        self.assertEqual(offenders, [], "Scripts that open the DB must accept --database")

    def test_jira_base_url_site_is_passed_by_keyword(self) -> None:
        offenders = [
            str(path.relative_to(SCRIPTS_ROOT))
            for path in _script_files()
            if POSITIONAL_SITE_ARG.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "Pass site_key= by name; the second positional parameter is prod")
