import tempfile
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.lib import keychain
from src.lib.config import (
    Credentials,
    JiraSite,
    file_has_plaintext_tokens,
    has_active_marker,
    list_credential_profiles,
    list_project_profiles,
    load_credentials,
    migrate_home_profiles_into_project,
    migrate_tokens_to_keychain,
    named_profile_env_path,
    profile_slug,
    project_profile_path,
    read_active_slug,
    read_profile_jira_urls,
    read_profile_name,
    resolve_credentials_path,
    resolve_jira_base_url,
    resolve_jira_site,
    slugify,
    write_active_slug,
    write_credentials_file,
    write_profile_default_jira_site,
    write_project_env_pointer,
)


class LoadCredentialsTest(TestCase):
    def setUp(self) -> None:
        # Force the env-file backend so these tests never touch the real Keychain.
        patcher = patch.object(keychain, "is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        original_site = environ.get("ASANA_JIRA_SITE_KEY")
        self.addCleanup(
            lambda: environ.pop("ASANA_JIRA_SITE_KEY", None)
            if original_site is None
            else environ.__setitem__("ASANA_JIRA_SITE_KEY", original_site)
        )

    def test_loads_and_normalizes_boolean_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=abc123",
                        "ASANA_WORKSPACE_GID=456",
                        "SKIP_SSL_VERIFICATION=true",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                credentials = load_credentials(project_root)

            self.assertIsInstance(credentials, Credentials)
            self.assertEqual(credentials.token, "abc123")
            self.assertEqual(credentials.workspace_gid, "456")
            self.assertTrue(credentials.skip_ssl_verification)

    def test_missing_required_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".asana_jira_env"
            env_path.write_text("ASANA_TOKEN=abc\n", encoding="utf-8")

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                with self.assertRaises(KeyError):
                    load_credentials(project_root)

    def test_loads_from_env_file_when_credentials_json_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        'export ASANA_TOKEN="env-token"',
                        "ASANA_WORKSPACE_GID=env-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                        "ATLASSIAN_USERNAME=user@example.com",
                        "ATLASSIAN_TOKEN=jira-token",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "env-token")
            self.assertEqual(credentials.workspace_gid, "env-workspace")
            self.assertFalse(credentials.skip_ssl_verification)
            self.assertEqual(credentials.atlassian_username, "user@example.com")
            self.assertEqual(credentials.atlassian_token, "jira-token")

    def test_process_environment_overrides_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=env-file-token",
                        "ASANA_WORKSPACE_GID=env-file-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(
                environ,
                {
                    "ASANA_JIRA_ENV_FILE": str(env_path),
                    "ASANA_TOKEN": "process-token",
                    "ASANA_WORKSPACE_GID": "process-workspace",
                    "SKIP_SSL_VERIFICATION": "true",
                },
                clear=False,
            ):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "process-token")
            self.assertEqual(credentials.workspace_gid, "process-workspace")
            self.assertTrue(credentials.skip_ssl_verification)

    def test_project_pointer_file_selects_client_specific_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / "client-a.env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=client-token",
                        "ASANA_WORKSPACE_GID=client-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")

            with patch.dict(environ, {}, clear=True):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "client-token")
            self.assertEqual(credentials.workspace_gid, "client-workspace")
            self.assertFalse(credentials.skip_ssl_verification)

    def test_missing_env_sources_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            with patch.dict(environ, {}, clear=True):
                with self.assertRaises(FileNotFoundError):
                    load_credentials(project_root)

    def test_loads_jira_base_urls_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=t",
                        "ASANA_WORKSPACE_GID=w",
                        "SKIP_SSL_VERIFICATION=false",
                        "JIRA_BASE_URL=https://prod.atlassian.net",
                        "JIRA_SANDBOX_BASE_URL=https://sandbox.atlassian.net",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                credentials = load_credentials(project_root)
            self.assertEqual(credentials.jira_base_url, "https://prod.atlassian.net")
            self.assertEqual(credentials.jira_sandbox_base_url, "https://sandbox.atlassian.net")

    def test_resolve_jira_base_url_selects_environment(self) -> None:
        creds = Credentials(
            token="t",
            workspace_gid="w",
            skip_ssl_verification=False,
            jira_base_url="https://prod.atlassian.net/",
            jira_sandbox_base_url="https://sandbox.atlassian.net",
        )
        # Default is sandbox; --prod selects production. Trailing slash is stripped.
        self.assertEqual(resolve_jira_base_url(creds), "https://sandbox.atlassian.net")
        self.assertEqual(resolve_jira_base_url(creds, prod=True), "https://prod.atlassian.net")

    def test_named_jira_sites_resolve_and_persist_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            creds = Credentials(
                token="t", workspace_gid="w", skip_ssl_verification=False,
                jira_sites=(
                    JiraSite("production_jira", "Production Jira", "https://prod.example/"),
                    JiraSite("archive_jira", "Archive Jira", "https://archive.example"),
                ),
                jira_default_site="archive_jira",
            )
            write_credentials_file(env_path, creds)
            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                loaded = load_credentials(tmpdir)
            self.assertEqual(resolve_jira_site(loaded).key, "archive_jira")
            self.assertEqual(resolve_jira_base_url(loaded, site_key="production_jira"), "https://prod.example")
            write_profile_default_jira_site(env_path, "production_jira")
            self.assertIn("JIRA_DEFAULT_SITE=production_jira", env_path.read_text(encoding="utf-8"))

    def test_resolve_jira_base_url_missing_url_raises(self) -> None:
        creds = Credentials(token="t", workspace_gid="w", skip_ssl_verification=False)
        with self.assertRaises(ValueError):
            resolve_jira_base_url(creds)  # no sandbox URL
        with self.assertRaises(ValueError):
            resolve_jira_base_url(creds, prod=True)  # no production URL

    def test_write_credentials_file_persists_jira_base_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            credentials = Credentials(
                token="t",
                workspace_gid="w",
                skip_ssl_verification=False,
                jira_base_url="https://prod.atlassian.net",
                jira_sandbox_base_url="https://sandbox.atlassian.net",
            )
            write_credentials_file(env_path, credentials)
            contents = env_path.read_text(encoding="utf-8")
            self.assertIn("export JIRA_BASE_URL=https://prod.atlassian.net", contents)
            self.assertIn("export JIRA_SANDBOX_BASE_URL=https://sandbox.atlassian.net", contents)

    def test_write_credentials_file_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            credentials = Credentials(
                token="token-123",
                workspace_gid="workspace-456",
                skip_ssl_verification=True,
                atlassian_username="user@example.com",
                atlassian_token="jira-token",
            )

            with patch.dict(environ, {}, clear=True):
                written_env_path = write_credentials_file(env_path, credentials)
                pointer_path = write_project_env_pointer(project_root, env_path)

                self.assertEqual(written_env_path, env_path)
                self.assertEqual(resolve_credentials_path(project_root), env_path)
                self.assertEqual(
                    env_path.read_text(encoding="utf-8"),
                    "\n".join(
                        [
                            "export ASANA_TOKEN=token-123",
                            "export ASANA_WORKSPACE_GID=workspace-456",
                            "export SKIP_SSL_VERIFICATION=true",
                            "export ATLASSIAN_USERNAME=user@example.com",
                            "export ATLASSIAN_TOKEN=jira-token",
                        ]
                    )
                    + "\n",
                )
                self.assertEqual(pointer_path.read_text(encoding="utf-8"), f"{env_path}\n")

    def test_write_and_load_client_name_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".asana_jira_env_acme"
            credentials = Credentials(
                token="token-123",
                workspace_gid="workspace-456",
                skip_ssl_verification=False,
                client_name="Acme Corp",
            )
            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=True):
                write_credentials_file(env_path, credentials)
                contents = env_path.read_text(encoding="utf-8")
                self.assertIn("export CLIENT_NAME='Acme Corp'", contents)
                # CLIENT_NAME is prepended to the file.
                self.assertTrue(contents.startswith("export CLIENT_NAME="))
                reloaded = load_credentials(Path(tmpdir))
            self.assertEqual(reloaded.client_name, "Acme Corp")

    def test_read_profile_name_returns_none_without_client_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".asana_jira_env"
            env_path.write_text("ASANA_TOKEN=t\nASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            self.assertIsNone(read_profile_name(env_path))
            self.assertIsNone(read_profile_name(Path(tmpdir) / "missing"))

    def test_read_profile_jira_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".asana_jira_env_acme"
            env_path.write_text(
                "JIRA_BASE_URL=https://prod.example.net\n"
                "JIRA_SANDBOX_BASE_URL=https://sandbox.example.net\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_profile_jira_urls(env_path),
                ("https://prod.example.net", "https://sandbox.example.net"),
            )
            # Missing file or missing keys yield (None, None) / partial None.
            self.assertEqual(read_profile_jira_urls(Path(tmpdir) / "missing"), (None, None))


class ProfileHelpersTest(TestCase):
    def test_slugify_normalizes_to_safe_characters(self) -> None:
        self.assertEqual(slugify("Acme Corp"), "acme_corp")
        self.assertEqual(slugify("  A/B & C!  "), "ab__c")
        self.assertEqual(slugify("Globex-2"), "globex-2")
        self.assertEqual(slugify(""), "client")
        self.assertEqual(slugify("***"), "client")

    def test_slugify_guards_reserved_default(self) -> None:
        # "default" is reserved for the default profile's file/account.
        self.assertEqual(slugify("Default"), "default-client")
        self.assertEqual(slugify("default"), "default-client")

    def test_named_profile_env_path_uses_slug(self) -> None:
        self.assertEqual(named_profile_env_path("Acme Corp").name, ".asana_jira_env_acme_corp")

    def test_profile_slug_from_env_path(self) -> None:
        # Current project names.
        self.assertEqual(profile_slug(Path("/x/profiles/default.env")), "")
        self.assertEqual(profile_slug(Path("/x/profiles/acme.env")), "acme")
        self.assertEqual(profile_slug(Path("/x/weird-name.env")), "weird-name")
        # Legacy home names map to the same slugs (keeps the per-profile DB stable).
        self.assertEqual(profile_slug(Path.home() / ".asana_jira_env"), "")
        self.assertEqual(profile_slug(Path.home() / ".asana_jira_env_acme_corp"), "acme_corp")

    def test_project_profile_path_for_default_and_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.assertEqual(project_profile_path("", root), root / "profiles" / "default.env")
            self.assertEqual(project_profile_path("acme", root), root / "profiles" / "acme.env")

    def test_active_marker_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.assertFalse(has_active_marker(root))
            self.assertEqual(read_active_slug(root), "")

            write_active_slug("acme", root)
            self.assertTrue(has_active_marker(root))
            self.assertEqual(read_active_slug(root), "acme")
            self.assertEqual((root / ".asana_jira_active").read_text(encoding="utf-8"), "acme\n")

            # The default profile is written/read as "default" -> "".
            write_active_slug("", root)
            self.assertTrue(has_active_marker(root))
            self.assertEqual(read_active_slug(root), "")
            self.assertEqual((root / ".asana_jira_active").read_text(encoding="utf-8"), "default\n")

    def test_list_project_profiles_discovers_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            default = project_profile_path("", root)
            default.parent.mkdir(parents=True, exist_ok=True)
            default.write_text("ASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            project_profile_path("acme", root).write_text(
                "CLIENT_NAME=Acme\nASANA_WORKSPACE_GID=w\n", encoding="utf-8"
            )
            found = {path.name: name for path, name in list_project_profiles(root)}
            self.assertEqual(found, {"default.env": None, "acme.env": "Acme"})

    def test_list_credential_profiles_discovers_and_excludes_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            keychain, "is_available", return_value=False
        ), patch("src.lib.config.DEFAULT_ENV_PATH", Path(tmpdir) / ".asana_jira_env"):
            home = Path(tmpdir)
            default = home / ".asana_jira_env"
            default.write_text("ASANA_TOKEN=t\nASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            named = home / ".asana_jira_env_acme"
            write_credentials_file(
                named,
                Credentials(token="t", workspace_gid="w", skip_ssl_verification=False, client_name="Acme"),
            )
            # The project pointer is excluded even if it lands in the same directory.
            (home / ".asana_jira_env_path").write_text("/somewhere\n", encoding="utf-8")
            # A directory matching the glob is skipped.
            (home / ".asana_jira_env_dir").mkdir()

            profiles = list_credential_profiles()

            found = {path.name: name for path, name in profiles}
            self.assertEqual(found, {".asana_jira_env": None, ".asana_jira_env_acme": "Acme"})


class ResolveEnvPathOrderTest(TestCase):
    """`resolve_credentials_path` precedence with the project profile layout."""

    def setUp(self) -> None:
        patcher = patch.object(keychain, "is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_active_marker_selects_project_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = project_profile_path("acme", root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("ASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            write_active_slug("acme", root)
            with patch.dict(environ, {}, clear=True):
                self.assertEqual(resolve_credentials_path(root), target)

    def test_marker_present_but_file_missing_falls_back_to_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "src.lib.config.DEFAULT_ENV_PATH", Path(tmpdir) / "home" / ".asana_jira_env"
        ):
            root = Path(tmpdir) / "project"
            root.mkdir(parents=True)
            home_file = Path(tmpdir) / "home" / ".asana_jira_env_acme"
            home_file.parent.mkdir(parents=True, exist_ok=True)
            home_file.write_text("ASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            write_active_slug("acme", root)
            with patch.dict(environ, {}, clear=True):
                self.assertEqual(resolve_credentials_path(root), home_file)

    def test_default_project_file_used_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            default = project_profile_path("", root)
            default.parent.mkdir(parents=True, exist_ok=True)
            default.write_text("ASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            with patch.dict(environ, {}, clear=True):
                self.assertEqual(resolve_credentials_path(root), default)

    def test_legacy_pointer_used_when_no_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "legacy.env"
            target.write_text("ASANA_WORKSPACE_GID=w\n", encoding="utf-8")
            (root / ".asana_jira_env_path").write_text(f"{target}\n", encoding="utf-8")
            with patch.dict(environ, {}, clear=True):
                self.assertEqual(resolve_credentials_path(root), target)


class MigrateHomeProfilesTest(TestCase):
    """`migrate_home_profiles_into_project` relocates home profiles + re-keys secrets."""

    def setUp(self) -> None:
        self.store: dict[str, str] = {}
        patchers = [
            patch.object(keychain, "is_available", return_value=True),
            patch.object(keychain, "get_secret", side_effect=self.store.get),
            patch.object(keychain, "set_secret", side_effect=self.store.__setitem__),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_migrates_files_and_rekeys_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "src.lib.config.DEFAULT_ENV_PATH", Path(tmpdir) / "home" / ".asana_jira_env"
        ):
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            default_home = home / ".asana_jira_env"
            default_home.write_text("ASANA_WORKSPACE_GID=w0\n", encoding="utf-8")
            acme_home = home / ".asana_jira_env_acme"
            acme_home.write_text("CLIENT_NAME=Acme\nASANA_WORKSPACE_GID=w1\n", encoding="utf-8")
            # Pre-migration secrets stored under the legacy basename accounts.
            self.store[".asana_jira_env:ASANA_TOKEN"] = "default-secret"
            self.store[".asana_jira_env_acme:ASANA_TOKEN"] = "acme-secret"

            root = Path(tmpdir) / "project"
            root.mkdir()
            migrated = migrate_home_profiles_into_project(root)

            slugs = sorted(slug for slug, _h, _t in migrated)
            self.assertEqual(slugs, ["", "acme"])
            # Files copied into profiles/ (originals left intact).
            self.assertTrue(project_profile_path("", root).exists())
            self.assertTrue(project_profile_path("acme", root).exists())
            self.assertTrue(default_home.exists())
            self.assertTrue(acme_home.exists())
            # Keychain re-keyed to the slug accounts.
            self.assertEqual(self.store["default:ASANA_TOKEN"], "default-secret")
            self.assertEqual(self.store["acme:ASANA_TOKEN"], "acme-secret")

    def test_is_idempotent_and_skips_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "src.lib.config.DEFAULT_ENV_PATH", Path(tmpdir) / "home" / ".asana_jira_env"
        ):
            home = Path(tmpdir) / "home"
            home.mkdir(parents=True)
            (home / ".asana_jira_env_acme").write_text("ASANA_WORKSPACE_GID=w1\n", encoding="utf-8")
            root = Path(tmpdir) / "project"
            root.mkdir()

            first = migrate_home_profiles_into_project(root)
            self.assertEqual([slug for slug, _h, _t in first], ["acme"])
            # Second run finds the target already present and migrates nothing.
            self.assertEqual(migrate_home_profiles_into_project(root), [])


class KeychainCredentialsTest(TestCase):
    """Behavior when the macOS Keychain backend is available (faked in-memory)."""

    def setUp(self) -> None:
        self.store: dict[str, str] = {}
        patchers = [
            patch.object(keychain, "is_available", return_value=True),
            patch.object(keychain, "get_secret", side_effect=self.store.get),
            patch.object(keychain, "set_secret", side_effect=self.store.__setitem__),
            patch.object(keychain, "delete_secret", side_effect=lambda a: bool(self.store.pop(a, None))),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_write_stores_tokens_in_keychain_and_omits_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            write_credentials_file(
                env_path,
                Credentials(
                    token="asana-secret",
                    workspace_gid="ws-1",
                    skip_ssl_verification=False,
                    atlassian_username="u@example.com",
                    atlassian_token="jira-secret",
                    jira_base_url="https://prod.example",
                ),
            )
            contents = env_path.read_text(encoding="utf-8")
            self.assertNotIn("ASANA_TOKEN", contents)
            self.assertNotIn("ATLASSIAN_TOKEN", contents)
            self.assertIn("ASANA_WORKSPACE_GID", contents)
            self.assertIn("ATLASSIAN_USERNAME", contents)
            # Accounts are namespaced by profile slug (client.env -> "client").
            self.assertEqual(self.store["client:ASANA_TOKEN"], "asana-secret")
            self.assertEqual(self.store["client:ATLASSIAN_TOKEN"], "jira-secret")

    def test_load_reads_tokens_from_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            env_path.write_text(
                "export ASANA_WORKSPACE_GID=ws-1\nexport ATLASSIAN_USERNAME=u@example.com\n",
                encoding="utf-8",
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")
            self.store["client:ASANA_TOKEN"] = "asana-secret"
            self.store["client:ATLASSIAN_TOKEN"] = "jira-secret"

            with patch.dict(environ, {}, clear=True):
                creds = load_credentials(project_root)
            self.assertEqual(creds.token, "asana-secret")
            self.assertEqual(creds.atlassian_token, "jira-secret")
            self.assertEqual(creds.workspace_gid, "ws-1")

    def test_load_reads_tokens_from_legacy_basename_account(self) -> None:
        # Pre-migration entries keyed by the file basename still resolve.
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            env_path.write_text("export ASANA_WORKSPACE_GID=ws-1\n", encoding="utf-8")
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")
            self.store["client.env:ASANA_TOKEN"] = "legacy-secret"

            with patch.dict(environ, {}, clear=True):
                creds = load_credentials(project_root)
            self.assertEqual(creds.token, "legacy-secret")

    def test_process_env_overrides_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            env_path.write_text("export ASANA_WORKSPACE_GID=ws-1\n", encoding="utf-8")
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")
            self.store["client:ASANA_TOKEN"] = "from-keychain"

            with patch.dict(environ, {"ASANA_TOKEN": "from-env"}, clear=True):
                creds = load_credentials(project_root)
            self.assertEqual(creds.token, "from-env")

    def test_migrate_moves_file_tokens_to_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            env_path.write_text(
                "\n".join(
                    [
                        "export ASANA_TOKEN=asana-secret",
                        "export ASANA_WORKSPACE_GID=ws-1",
                        "export ATLASSIAN_TOKEN=jira-secret",
                        "export JIRA_BASE_URL=https://prod.example",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(file_has_plaintext_tokens(env_path))

            migrated = migrate_tokens_to_keychain(env_path)
            self.assertEqual(sorted(migrated), ["ASANA_TOKEN", "ATLASSIAN_TOKEN"])
            self.assertEqual(self.store["client:ASANA_TOKEN"], "asana-secret")
            self.assertEqual(self.store["client:ATLASSIAN_TOKEN"], "jira-secret")

            contents = env_path.read_text(encoding="utf-8")
            self.assertNotIn("ASANA_TOKEN", contents)
            self.assertNotIn("ATLASSIAN_TOKEN", contents)
            self.assertIn("ASANA_WORKSPACE_GID=ws-1", contents)
            self.assertIn("JIRA_BASE_URL=https://prod.example", contents)
            self.assertFalse(file_has_plaintext_tokens(env_path))
