"""Safety regressions for build/backup/migration/rollback. No Docker/DB calls."""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("operations", ROOT / "scripts/operations.py")
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)
N = "n1b2c3d4e5f6"
GRAPH = {"heads": [N], "parents": {"g2": [], "h1": ["g2"], "i1": ["g2"],
          "j1": ["i1"], "k1": ["j1"], "l1": ["k1"], "m1": ["h1", "l1"], N: ["m1"]}}


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.v = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                      if line and not line.startswith("#"))
        self.v.update(POSTGRES_DB="test_db", POSTGRES_USER="test_user",
                      POSTGRES_PASSWORD="synthetic_db_password",
                      JWT_ACCESS_SECRET="synthetic_jwt_" + "a" * 40,
                      PUBLIC_TRACKING_HMAC_SECRET="synthetic_hmac_" + "b" * 40,
                      BACKUP_ROOT=str(self.root / "backups"), MIN_FREE_GB="1")
        Path(self.v["BACKUP_ROOT"]).mkdir()
        self.m = dict(release="qa-test", source_sha="a" * 40, alembic_head=N,
                      build_date="2026-11-11T00:00:00Z", status="validated", notes="synthetic")

    def context(self, current=("unknown_revision",)):
        c = SimpleNamespace(v=self.v, m=self.m)
        def sql(query):
            if "pg_stat_activity" in query:
                return "0"
            if "to_regclass" in query:
                return "t"
            if "version_num" in query:
                return "\n".join(current)
            return "1"
        c.sql = Mock(side_effect=sql)
        def dc(*args, output=None, **kwargs):
            if args[0] == "ps":
                return "pg"
            if output is not None and output != op.subprocess.DEVNULL:
                if any("tarfile.open" in x for x in args):
                    with tarfile.open(fileobj=output, mode="w:gz"):
                        pass
                else:
                    output.write(b"synthetic dump")
            return ""
        c.dc = Mock(side_effect=dc)
        c.run = Mock(return_value="")
        return c

    def make_backup(self, c):
        with patch.object(op.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            return op.backup(c)

    def test_shared_storage_python_module_is_versionable(self):
        paths = sorted((ROOT / "backend/app/core/storage").glob("*.py"))
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            result = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(),
                                     "-C", str(ROOT), "check-ignore", "--quiet",
                                     path.relative_to(ROOT).as_posix()], capture_output=True)
            self.assertEqual(result.returncode, 1, "Git excludes source module: " + str(path))

    def test_template_has_no_secrets(self):
        for key in ("POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET"):
            self.assertEqual(dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                                  if line and not line.startswith("#"))[key], "")

    def test_adopt_requires_explicit_historical_volume_names(self):
        v = {**self.v, "INSTALL_MODE": "adopt"}
        with self.assertRaises(op.Refusal):
            op.validate_env(v)
        v.update(POSTGRES_VOLUME="correspondencia_sipe_sipe_postgres_data",
                 STORAGE_VOLUME="correspondencia_sipe_sipe_backend_storage")
        op.validate_env(v)

    def test_demo_cannot_silently_adopt_history(self):
        v = {**self.v, "POSTGRES_VOLUME": "correspondencia_sipe_sipe_postgres_data",
             "STORAGE_VOLUME": "correspondencia_sipe_sipe_backend_storage"}
        with self.assertRaises(op.Refusal):
            op.validate_env(v)

    def context_mode(self, mode):
        root = self.root / "checkout"
        root.mkdir()
        (root / "release.json").write_text(json.dumps(self.m))
        v = {**self.v, "INSTALL_MODE": mode}
        if mode == "adopt":
            v.update(POSTGRES_VOLUME="correspondencia_sipe_sipe_postgres_data",
                     STORAGE_VOLUME="correspondencia_sipe_sipe_backend_storage")
        envfile = root / ".env"
        envfile.write_text("".join(k+"="+value+"\n" for k,value in v.items()))
        envfile.chmod(0o600)
        with patch.dict(op.os.environ, {"CORRESPONDENCIA_ENV_FILE": str(envfile)}):
            return op.Context(root)

    def test_adopt_context_includes_override(self):
        c = self.context_mode("adopt")
        self.assertIn(str(c.root / "docker-compose.adopt.yml"), c.base)

    def test_demo_context_uses_only_base_compose(self):
        c = self.context_mode("demo")
        self.assertNotIn(str(c.root / "docker-compose.adopt.yml"), c.base)

    def test_complete_env(self):
        op.validate_env(self.v, self.root / "checkout")

    def test_missing_secret(self):
        for key in ("POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET"):
            v = {**self.v, key: ""}
            with self.subTest(key=key), self.assertRaises(op.Refusal):
                op.validate_env(v)

    def test_short_signing_secrets(self):
        for key in ("JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET"):
            with self.subTest(key=key), self.assertRaises(op.Refusal):
                op.validate_env({**self.v, key: "short"})

    def test_external_database_rejected(self):
        for change in ({"DATABASE_URL": "postgresql://elsewhere/test"}, {"POSTGRES_HOST": "localhost"},
                       {"POSTGRES_PORT": "5433"}):
            with self.subTest(change=change), self.assertRaises(op.Refusal):
                op.validate_env({**self.v, **change})

    def test_cookie_misconfiguration_rejected(self):
        for change in ({"COOKIE_SECURE": "false"}, {"COOKIE_PATH": "/"}, {"COOKIE_SAMESITE": "none"}):
            with self.subTest(change=change), self.assertRaises(op.Refusal):
                op.validate_env({**self.v, **change})

    def test_backup_inside_checkout_rejected(self):
        with self.assertRaises(op.Refusal):
            op.validate_env({**self.v, "BACKUP_ROOT": str(self.root / "checkout/backups")}, self.root / "checkout")

    def test_duplicate_volumes_rejected(self):
        with self.assertRaises(op.Refusal):
            op.validate_env({**self.v, "STORAGE_VOLUME": self.v["POSTGRES_VOLUME"]})

    def test_env_literals_and_duplicate_guard(self):
        path = self.root / "private.env"
        path.write_text("A=value\nA=other\n")
        path.chmod(0o600)
        with self.assertRaises(op.Refusal):
            op.dotenv(path)
        path.write_text("A=$HOME\n")
        with self.assertRaises(op.Refusal):
            op.dotenv(path)

    def test_manifest_has_only_simple_metadata(self):
        (self.root / "release.json").write_text(json.dumps(self.m))
        self.assertEqual(op.manifest(self.root), self.m)

    def test_invalid_manifest_sha_rejected(self):
        (self.root / "release.json").write_text(json.dumps({**self.m, "source_sha": "short"}))
        with self.assertRaises(op.Refusal):
            op.manifest(self.root)

    def test_merge_graph_from_g2_preserves_shared_branch(self):
        pending = op.graph_plan(GRAPH, ["g2"], N)
        self.assertIn("h1", pending)
        self.assertIn("i1", pending)
        self.assertIn("m1", pending)

    def test_shared_branch_can_upgrade_to_merge(self):
        pending = op.graph_plan(GRAPH, ["h1"], N)
        self.assertNotIn("h1", pending)
        self.assertIn("i1", pending)

    def test_head_no_pending(self):
        self.assertEqual(op.graph_plan(GRAPH, [N], N), [])

    def test_fresh_base_uses_whole_graph(self):
        self.assertEqual(set(op.graph_plan(GRAPH, [], N)), set(GRAPH["parents"]))

    def test_unknown_revision_stops(self):
        with self.assertRaises(op.Refusal):
            op.graph_plan(GRAPH, ["a3b4c5d6e7f8"], N)

    def test_multiple_heads_stop(self):
        with self.assertRaises(op.Refusal):
            op.graph_plan({**GRAPH, "heads": [N, "other"]}, [], N)

    def test_backup_unknown_alembic_is_allowed(self):
        c = self.context()
        path = self.make_backup(c)
        data = json.loads((path / "backup.json").read_text())
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["current"], ["unknown_revision"])
        self.assertFalse(any("alembic" in call.args for call in c.dc.call_args_list))

    def test_backup_stops_application_before_dump(self):
        c = self.context()
        self.make_backup(c)
        commands = [call.args for call in c.dc.call_args_list]
        stop = commands.index(("stop", "web", "backend"))
        dump = next(i for i, command in enumerate(commands) if any("pg_dump" in arg for arg in command))
        self.assertLess(stop, dump)

    def test_backup_tampering_blocks_upgrade(self):
        c = self.context()
        path = self.make_backup(c)
        (path / "database.dump").write_bytes(b"tampered")
        with self.assertRaises(op.Refusal):
            op.verify_backup(c, path, ["unknown_revision"])

    def test_backup_wrong_database_rejected(self):
        c = self.context()
        path = self.make_backup(c)
        c.v = {**self.v, "POSTGRES_DB": "other"}
        with self.assertRaises(op.Refusal):
            op.verify_backup(c, path, ["unknown_revision"])

    def test_backup_failure_never_marks_complete(self):
        c = self.context()
        c.dc = Mock(side_effect=op.Refusal("dump failed"))
        with self.assertRaises(op.Refusal):
            self.make_backup(c)
        self.assertEqual(list(Path(self.v["BACKUP_ROOT"]).rglob("backup.json")), [])

    def test_external_writer_blocks_backup(self):
        c = self.context()
        c.sql.return_value = "1"
        c.sql.side_effect = None
        with self.assertRaises(op.Refusal):
            self.make_backup(c)

    def test_migrate_requires_backup(self):
        c = self.context()
        with patch.object(op, "plan", return_value=(["g2"], ["i1"])), self.assertRaises(op.Refusal):
            op.migrate(c, None)
        self.assertFalse(any("upgrade" in call.args for call in c.dc.call_args_list))

    def test_deploy_no_migration_without_flag(self):
        c = self.context()
        with patch.object(op, "preflight"), patch.object(op, "postgres_ready"), patch.object(op, "backup"), patch.object(op, "plan", return_value=(["g2"], ["i1"])), patch.object(op, "migrate") as migrate:
            with self.assertRaises(op.Refusal):
                op.deploy(c)
            migrate.assert_not_called()
        self.assertFalse(any(call.args[0] == "up" for call in c.dc.call_args_list))

    def test_deploy_order_and_no_postgres_recreate(self):
        c = self.context()
        calls = []
        c.dc.side_effect = lambda *args, **kwargs: calls.append(args[0])
        with patch.object(op, "preflight"), patch.object(op, "postgres_ready"), patch.object(op, "backup", side_effect=lambda c: calls.append("backup")), patch.object(op, "plan", return_value=(["g2"], ["i1"])), patch.object(op, "migrate", side_effect=lambda *a: calls.append("migrate")), patch.object(op, "health", side_effect=lambda c: calls.append("health")):
            op.deploy(c, True)
        self.assertEqual(calls, ["build", "backup", "migrate", "up", "health"])
        up = next(call.args for call in c.dc.call_args_list if call.args[0] == "up")
        self.assertIn("--no-deps", up)
        self.assertNotIn("postgres", up)

    def test_prepared_release_cannot_deploy(self):
        c = self.context()
        c.m = {**self.m, "status": "prepared"}
        with self.assertRaises(op.Refusal):
            op.deploy(c)

    def test_rollback_schema_mismatch_never_checks_out(self):
        c = self.context(("g2",))
        c.run.side_effect = ["", json.dumps(self.m)]
        with patch.object(op, "backup") as backup, self.assertRaises(op.Refusal):
            op.rollback(c, "b" * 40)
        backup.assert_not_called()
        self.assertFalse(any("checkout" in call.args[0] for call in c.run.call_args_list))

    def test_rollback_requires_clean_tree(self):
        c = self.context((N,))
        c.run.return_value = " M file"
        with self.assertRaises(op.Refusal):
            op.rollback(c, "b" * 40)

    def test_rollback_never_runs_database_downgrade(self):
        c = self.context((N,))
        c.run.side_effect = ["", json.dumps(self.m), ""]
        with patch.object(op, "backup"), patch.object(op, "health"):
            op.rollback(c, "b" * 40)
        commands = [str(call.args) for call in c.dc.call_args_list + c.run.call_args_list]
        self.assertFalse(any("downgrade" in call or "stamp" in call or "reset" in call for call in commands))

    def test_foreign_postgres_volume_owner_blocks_operation(self):
        c = self.context()
        c.dc.return_value = ""
        c.dc.side_effect = None
        c.run.side_effect = ["linux", c.v["POSTGRES_VOLUME"], c.v["STORAGE_VOLUME"], "foreign-pg"]
        with self.assertRaises(op.Refusal):
            op.preflight(c)
        self.assertFalse(any("up" in call.args for call in c.dc.call_args_list))

    def test_health_rejects_http_200_with_database_error(self):
        c = self.context()
        c.dc.side_effect = ["", '{"status":"error","database":"unreachable"}']
        with self.assertRaises(op.Refusal):
            op.health(c)

    def test_missing_volume_does_not_create_another(self):
        c = self.context()
        c.run.side_effect = ["linux", op.Refusal("volume missing")]
        with self.assertRaises(op.Refusal):
            op.preflight(c)
        self.assertFalse(any("create" in str(call.args) for call in c.run.call_args_list))


if __name__ == "__main__":
    unittest.main()
