from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import keybank  # noqa: E402


SAMPLE_YAML = """\
keys:
  - id: service-prod
    description: Production API, personal project
    notes: Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.
    aliases: [prod, production]
    maps_to: SERVICE_API_KEY
    public:
      SERVICE_API_URL: https://api.example.com
  - id: service-dev
    description: Development environment
    notes: Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.
    aliases:
      - dev
      - development
    maps_to: SERVICE_API_KEY
    public:
      SERVICE_API_URL: https://api.dev.example.com
  - id: platform-work
    description: Work platform account
    aliases: [platform]
    maps_to: PLATFORM_API_KEY
"""

SECRET_PROD = "secret_prod_value"
SECRET_DEV = "secret_dev_value"
SECRET_PLATFORM = "secret_platform_value"


class KeybankTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "bank"
        self.env_patch = mock.patch.dict(os.environ, {"KEYBANK_HOME": str(self.home)})
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        in_buf = StringIO(stdin or "")
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
            sys, "stderr", stderr
        ), mock.patch.object(sys, "stdin", in_buf):
            in_buf.isatty = lambda: False  # type: ignore[method-assign]
            code = keybank.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def init_bank(self) -> None:
        code, _, err = self.run_cli("init")
        self.assertEqual(code, 0, err)

    def seed(self) -> None:
        self.init_bank()
        keybank.catalog_path().write_text(SAMPLE_YAML, encoding="utf-8")
        keybank.save_secrets(
            {
                "service-prod": SECRET_PROD,
                "service-dev": SECRET_DEV,
                "platform-work": SECRET_PLATFORM,
            }
        )

    def assert_no_secrets(self, *texts: str) -> None:
        blob = "\n".join(texts)
        for secret in (SECRET_PROD, SECRET_DEV, SECRET_PLATFORM):
            self.assertNotIn(secret, blob)

    def test_init_creates_safe_files(self) -> None:
        code, out, err = self.run_cli("init")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.home.is_dir())
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertTrue(keybank.catalog_path().is_file())
        self.assertTrue(keybank.secrets_path().is_file())
        self.assertEqual(stat.S_IMODE(keybank.secrets_path().stat().st_mode), 0o600)
        self.assertIn("catalog.yaml", out)
        self.assertEqual(keybank.load_catalog(), [])

    def test_yaml_roundtrip_and_block_aliases(self) -> None:
        self.seed()
        keys = keybank.load_catalog()
        self.assertEqual(
            [key.id for key in keys],
            ["service-prod", "service-dev", "platform-work"],
        )
        dev = keys[1]
        self.assertEqual(dev.aliases, ["dev", "development"])
        self.assertEqual(dev.public["SERVICE_API_URL"], "https://api.dev.example.com")
        self.assertEqual(
            dev.notes,
            "Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.",
        )
        self.assertEqual(keys[2].notes, "")
        dumped = keybank.dump_catalog(keys)
        again = keybank.entries_from_data(keybank.parse_simple_yaml(dumped))
        self.assertEqual([key.id for key in again], [key.id for key in keys])
        self.assertEqual(again[0].aliases, ["prod", "production"])
        self.assertEqual(again[0].notes, keys[0].notes)
        self.assertNotIn("notes:", dumped.split("platform-work", 1)[1])

    def test_list_json_hides_secrets(self) -> None:
        self.seed()
        code, out, err = self.run_cli("list", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(len(payload), 3)
        self.assertTrue(all(item["has_secret"] for item in payload))
        self.assertIn("notes", payload[0])
        self.assertEqual(
            payload[0]["notes"],
            "Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.",
        )
        self.assertEqual(payload[2]["notes"], "")
        self.assert_no_secrets(out, err)

    def test_list_table_and_show(self) -> None:
        self.seed()
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("service-dev", out)
        self.assertIn("Development environment", out)
        self.assertIn("yes", out)
        self.assert_no_secrets(out, err)
        code, out, err = self.run_cli("show", "development")
        self.assertEqual(code, 0, err)
        self.assertIn("id:          service-dev", out)
        self.assertIn("notes:       Load env vars as-is.", out)
        self.assertIn("SERVICE_API_URL=https://api.dev.example.com", out)
        self.assert_no_secrets(out, err)

    def test_resolve_alias_description_and_ambiguous(self) -> None:
        self.seed()
        code, out, err = self.run_cli("resolve", "dev")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "service-dev")
        code, out, err = self.run_cli("resolve", "personal project")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "service-prod")
        code, out, err = self.run_cli("resolve", "service")
        self.assertEqual(code, 2)
        self.assertIn("ambiguous", err)
        self.assertIn("service-dev", err)
        self.assertIn("service-prod", err)
        self.assert_no_secrets(out, err)
        code, _, err = self.run_cli("resolve", "does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("no key matched", err)
        code, _, err = self.run_cli("resolve", "path suffix")
        self.assertEqual(code, 1)
        self.assertIn("no key matched", err)

    def test_add_and_set_secret(self) -> None:
        self.init_bank()
        code, out, err = self.run_cli(
            "add",
            "billing-test",
            "--description",
            "Billing test mode",
            "--notes",
            "Set BILLING_MODE from public. Do not rename it.",
            "--alias",
            "billing",
            "--maps-to",
            "BILLING_API_KEY",
            "--public",
            "BILLING_MODE=test",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Paste the secret", out)
        self.assertIn("billing-test=<secret>", out)
        code, out, err = self.run_cli("set-secret", "billing", stdin="test_secret_123")
        self.assertEqual(code, 0, err)
        self.assertEqual(keybank.load_secrets()["billing-test"], "test_secret_123")
        self.assert_no_secrets(out, err)
        code, _, err = self.run_cli(
            "add", "billing-test", "--description", "changed"
        )
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, _, err = self.run_cli(
            "add",
            "billing-test",
            "--update",
            "--description",
            "Billing test mode, updated",
        )
        self.assertEqual(code, 0, err)
        entry = keybank.require_one(keybank.load_catalog(), "billing-test")
        self.assertEqual(entry.description, "Billing test mode, updated")
        self.assertEqual(entry.notes, "Set BILLING_MODE from public. Do not rename it.")
        self.assertEqual(entry.aliases, ["billing"])
        self.assertEqual(keybank.load_secrets()["billing-test"], "test_secret_123")
        code, _, err = self.run_cli(
            "add",
            "billing-test",
            "--update",
            "--notes",
            "Pass BILLING_API_KEY through unchanged.",
        )
        self.assertEqual(code, 0, err)
        entry = keybank.require_one(keybank.load_catalog(), "billing-test")
        self.assertEqual(entry.description, "Billing test mode, updated")
        self.assertEqual(entry.notes, "Pass BILLING_API_KEY through unchanged.")

    def test_load_writes_runtime_names_and_chmod(self) -> None:
        self.seed()
        dest_dir = Path(self.tmp.name) / "script"
        dest_dir.mkdir()
        code, out, err = self.run_cli("load", "development", "--into", str(dest_dir))
        self.assertEqual(code, 0, err)
        env_path = dest_dir / ".env"
        self.assertTrue(env_path.is_file())
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        parsed = keybank.parse_env_file(env_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["SERVICE_API_KEY"], SECRET_DEV)
        self.assertEqual(parsed["SERVICE_API_URL"], "https://api.dev.example.com")
        self.assertNotIn("service-dev", parsed)
        self.assertIn("Do not open", out)
        self.assert_no_secrets(out, err)

    def test_load_as_and_merge(self) -> None:
        self.seed()
        env_path = Path(self.tmp.name) / "custom.env"
        env_path.write_text("KEEP=yes\nSERVICE_API_KEY=old\n", encoding="utf-8")
        code, _, err = self.run_cli(
            "load", "service-dev", "--into", str(env_path), "--as", "CUSTOM_SERVICE_KEY"
        )
        self.assertEqual(code, 0, err)
        parsed = keybank.parse_env_file(env_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["KEEP"], "yes")
        self.assertEqual(parsed["CUSTOM_SERVICE_KEY"], SECRET_DEV)
        self.assertEqual(parsed["SERVICE_API_KEY"], "old")

    def test_load_multiple_and_conflict(self) -> None:
        self.seed()
        dest = Path(self.tmp.name) / "multi.env"
        code, _, err = self.run_cli(
            "load", "service-dev", "platform-work", "--into", str(dest)
        )
        self.assertEqual(code, 0, err)
        parsed = keybank.parse_env_file(dest.read_text(encoding="utf-8"))
        self.assertEqual(parsed["SERVICE_API_KEY"], SECRET_DEV)
        self.assertEqual(parsed["PLATFORM_API_KEY"], SECRET_PLATFORM)
        code, _, err = self.run_cli(
            "load", "service-dev", "service-prod", "--into", str(dest)
        )
        self.assertEqual(code, 1)
        self.assertIn("both write SERVICE_API_KEY", err)

    def test_load_refuses_bank_files(self) -> None:
        self.seed()
        code, _, err = self.run_cli(
            "load", "service-dev", "--into", str(keybank.secrets_path())
        )
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)
        self.assertEqual(keybank.load_secrets()["service-dev"], SECRET_DEV)

    def test_load_missing_secret(self) -> None:
        self.init_bank()
        code, _, err = self.run_cli(
            "add",
            "empty-key",
            "--description",
            "No secret yet",
            "--maps-to",
            "EMPTY_KEY",
        )
        self.assertEqual(code, 0, err)
        dest = Path(self.tmp.name) / "out.env"
        code, _, err = self.run_cli("load", "empty-key", "--into", str(dest))
        self.assertEqual(code, 1)
        self.assertIn("has no secret", err)
        self.assertFalse(dest.exists())

    def test_run_injects_env(self) -> None:
        self.seed()
        script = (
            "import os,sys; "
            "sys.stdout.write(os.environ['SERVICE_API_KEY']); "
            "sys.stdout.write('\\n'); "
            "sys.stdout.write(os.environ['SERVICE_API_URL'])"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "keybank",
                "run",
                "development",
                "--",
                sys.executable,
                "-c",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "KEYBANK_HOME": str(self.home),
                "PYTHONPATH": str(ROOT),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], SECRET_DEV)
        self.assertEqual(lines[1], "https://api.dev.example.com")

    def test_run_passes_child_help(self) -> None:
        self.seed()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "keybank",
                "run",
                "development",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('child-ok')",
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "KEYBANK_HOME": str(self.home),
                "PYTHONPATH": str(ROOT),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "child-ok")
        self.assertNotIn("usage:", result.stdout)

    def test_remove_requires_yes_and_accepts_alias(self) -> None:
        self.seed()
        code, _, err = self.run_cli("remove", "development")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertTrue(any(key.id == "service-dev" for key in keybank.load_catalog()))
        code, _, err = self.run_cli("remove", "development", "--yes")
        self.assertEqual(code, 0, err)
        self.assertFalse(any(key.id == "service-dev" for key in keybank.load_catalog()))
        self.assertNotIn("service-dev", keybank.load_secrets())

    def test_invalid_id_and_missing_bank(self) -> None:
        code, _, err = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertIn("no keybank", err)
        self.init_bank()
        code, _, err = self.run_cli(
            "add", "Bad_ID", "--description", "nope", "--maps-to", "X"
        )
        self.assertEqual(code, 1)
        self.assertIn("invalid id", err)

    def test_quoted_description_roundtrip(self) -> None:
        self.init_bank()
        code, _, err = self.run_cli(
            "add",
            "weird-key",
            "--description",
            'Has: a colon, # hash, and "quotes"',
            "--maps-to",
            "WEIRD_KEY",
        )
        self.assertEqual(code, 0, err)
        keys = keybank.load_catalog()
        self.assertEqual(keys[0].description, 'Has: a colon, # hash, and "quotes"')

    def test_secret_with_quotes_roundtrip(self) -> None:
        self.init_bank()
        self.run_cli(
            "add",
            "quoted-secret",
            "--description",
            "Needs quoting",
            "--maps-to",
            "QUOTED_KEY",
        )
        secret = r'abc"def\ghi'
        code, _, err = self.run_cli("set-secret", "quoted-secret", stdin=secret)
        self.assertEqual(code, 0, err)
        self.assertEqual(keybank.load_secrets()["quoted-secret"], secret)
        dest = Path(self.tmp.name) / "quoted.env"
        code, _, err = self.run_cli("load", "quoted-secret", "--into", str(dest))
        self.assertEqual(code, 0, err)
        parsed = keybank.parse_env_file(dest.read_text(encoding="utf-8"))
        self.assertEqual(parsed["QUOTED_KEY"], secret)

    def test_doctor_reports_missing_secret(self) -> None:
        self.init_bank()
        self.run_cli(
            "add",
            "only-meta",
            "--description",
            "Catalog only",
            "--maps-to",
            "ONLY_META",
        )
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 1, err)
        self.assertIn("MISSING SECRET", out)
        self.assertNotIn(SECRET_DEV, out)

    def test_setup_installs_selected_agents(self) -> None:
        fake_home = Path(self.tmp.name) / "home"
        fake_home.mkdir()
        with mock.patch("keybank.cli.Path.home", return_value=fake_home):
            code, out, err = self.run_cli("setup", "--agents", "claude,grok")
        self.assertEqual(code, 0, err)
        self.assertTrue(
            (fake_home / ".claude" / "skills" / "keybank" / "SKILL.md").is_file()
        )
        self.assertTrue(
            (fake_home / ".grok" / "skills" / "keybank" / "SKILL.md").is_file()
        )
        self.assertFalse((fake_home / ".cursor" / "skills" / "keybank").exists())
        self.assertIn("Tell an agent", out)

    def test_setup_requires_agents_when_not_tty(self) -> None:
        code, _, err = self.run_cli("setup")
        self.assertEqual(code, 1)
        self.assertIn("--agents", err)

    def test_home_and_catalog_path(self) -> None:
        code, out, err = self.run_cli("home")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(self.home))
        code, out, err = self.run_cli("catalog-path")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(self.home / "catalog.yaml"))


if __name__ == "__main__":
    unittest.main()
