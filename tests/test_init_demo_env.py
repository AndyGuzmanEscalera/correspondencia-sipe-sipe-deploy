"""Private demo environment creation in temporary directories, never repo .env."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
init = importlib.import_module("init_demo_env")


class DemoEnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        template = (ROOT / ".env.example").read_text(encoding="utf-8").replace(
            "BACKUP_ROOT=/var/backups/correspondencia", "BACKUP_ROOT=" + str(self.root / "outside-backups"))
        (self.root / ".env.example").write_text(template, encoding="utf-8")
        for name, value in (("verify_repo", lambda root: None),):
            p = patch.object(init, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(init.platform, "system", return_value="Linux")
        p.start(); self.addCleanup(p.stop)
        # Linux O_NOFOLLOW may not exist in Windows test runtime.
        p = patch.object(init.os, "O_NOFOLLOW", getattr(init.os, "O_NOFOLLOW", 0), create=True)
        p.start(); self.addCleanup(p.stop)

    def test_generates_independent_secrets_only_in_private_file(self):
        # op.validate_env requires backups outside checkout, as in production.
        template = (self.root / ".env.example").read_text().replace(
            "BACKUP_ROOT=" + str(self.root / "outside-backups"),
            "BACKUP_ROOT=" + str(self.root.parent / "corr-demo-unit-backups"))
        (self.root / ".env.example").write_text(template)
        path = init.create_env(self.root)
        if init.os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        values = dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))
        secrets = [values[k] for k in ("POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET")]
        self.assertEqual(len(set(secrets)), 3)
        self.assertTrue(all(len(s) == 64 for s in secrets))
        self.assertEqual(values["INSTALL_MODE"], "demo")
        self.assertEqual(values["POSTGRES_VOLUME"], init.DEMO_DB)

    def test_never_overwrites_existing_env(self):
        path = self.root / ".env"; path.write_text("keep")
        with self.assertRaises(init.op.Refusal):
            init.create_env(self.root)
        self.assertEqual(path.read_text(), "keep")

    def test_non_linux_is_rejected(self):
        with patch.object(init.platform, "system", return_value="Windows"), self.assertRaises(init.op.Refusal):
            init.create_env(self.root)
        self.assertFalse((self.root / ".env").exists())

    def test_wrong_repo_prevents_file_creation(self):
        with patch.object(init, "verify_repo", side_effect=init.op.Refusal("wrong repo")), self.assertRaises(init.op.Refusal):
            init.create_env(self.root)
        self.assertFalse((self.root / ".env").exists())


if __name__ == "__main__":
    unittest.main()
