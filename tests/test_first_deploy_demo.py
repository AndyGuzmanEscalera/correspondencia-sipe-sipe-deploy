"""First cut safety tests. Docker, filesystem probes and HTTP are mocked."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import first_deploy_demo as first

HEAD = "n1b2c3d4e5f6"


def container(name="sipe_correspondencia_db", identity="a" * 64, *, project="correspondencia_sipe_sipe", service="postgres", running=False):
    return {"Id": identity, "Name": "/" + name, "Config": {
        "Image": "postgres:16-alpine" if service == "postgres" else "correspondencia-backend",
        "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service},
        "Env": ["POSTGRES_DB=correspondencia_demo"]},
        "State": {"Running": running, "StartedAt": "stable", "Health": {"Log": []}},
        "Mounts": [], "HostConfig": {"PortBindings": {}}, "NetworkSettings": {"Networks": {}}}


def volume(name, project="correspondencia_sipe_sipe", role="postgres_data"):
    return {"Name": name, "Driver": "local", "Options": None, "Mountpoint": "/var/lib/docker/volumes/" + name,
            "CreatedAt": "stable", "Labels": {"com.docker.compose.project": project, "com.docker.compose.volume": role}}


class FakeContext:
    def __init__(self, root):
        self.root, self.envfile = root, root / ".env"
        self.v = {"INSTALL_MODE": "demo", "POSTGRES_DB": "correspondencia_demo", "POSTGRES_USER": "correspondencia_app",
                  "POSTGRES_VOLUME": first.DEMO_DB, "STORAGE_VOLUME": first.DEMO_STORAGE, "MIN_FREE_GB": "5"}
        self.m = {"status": "validated", "alembic_head": HEAD}
        self.items, self.volumes, self.users, self.publishers, self.networks = {}, {}, {}, [], {}
        self.writes, self.commands = [], []
        self.remote, self.endpoint, self.tables, self.head = next(iter(first.REMOTES)), "unix:///var/run/docker.sock", 0, HEAD
        self.after_build = None
        self.config = {"name": first.op.PROJECT,
            "services": {"postgres": {"image": "postgres:16-alpine"},
                         "backend": {"expose": ["8000"], "build": {"context": str(root / "backend")}},
                         "web": {"build": {"context": str(root / "web")},
                                 "ports": [{"target": 8080, "published": "8080", "host_ip": "127.0.0.1"}]}},
            "networks": {"data": {"internal": True}, "edge": {"ipam": {"config": [{"subnet": "172.30.237.0/28"}]}}},
            "volumes": {"postgres_data": {"name": first.DEMO_DB}, "backend_storage": {"name": first.DEMO_STORAGE}}}

    def run(self, args, **kwargs):
        self.commands.append(args)
        if args[0] == "git":
            if "rev-parse" in args: return str(self.root)
            if "remote" in args: return self.remote
            return ""
        if args[1:3] == ["context", "inspect"]:
            return json.dumps([{"Endpoints": {"docker": {"Host": self.endpoint}}}])
        if args[1] == "info": return json.dumps({"OSType": "linux", "DockerRootDir": str(self.root)})
        if args[1:3] == ["compose", "version"]: return "2.30.0"
        if args[1:3] == ["network", "ls"]: return "\n".join(self.networks)
        if args[1:3] == ["network", "inspect"]: return json.dumps([self.networks[args[3]]])
        if args[1:3] == ["volume", "ls"]: return "\n".join(self.volumes)
        if args[1:3] == ["volume", "inspect"]: return json.dumps([self.volumes[args[3]]])
        if args[1] == "inspect": return json.dumps([next(v for k, v in self.items.items() if k.startswith(args[2]))])
        if args[1] == "ps":
            if "--filter" in args:
                flag = args[args.index("--filter") + 1]
                if flag.startswith("label="):
                    return "\n".join(k for k, v in self.items.items() if v["Config"]["Labels"].get("com.docker.compose.project") == first.op.PROJECT)
                if flag.startswith("volume="): return "\n".join(self.users.get(flag.split("=", 1)[1], []))
                if flag == "publish=8080": return "\n".join(self.publishers)
            return "\n".join(k + " " + v["Name"].lstrip("/") for k, v in self.items.items())
        if args[1] == "rm":
            identity = args[-1]
            self.writes.append(("rm", identity))
            self.items.pop(identity)
            self.users = {k: [v for v in values if v != identity] for k, values in self.users.items()}
            return ""
        if args[1:3] == ["volume", "rm"]:
            self.writes.append(("volume-rm", args[-1]))
            self.volumes.pop(args[-1])
            return ""
        raise AssertionError(args)

    def dc(self, *args, **kwargs):
        if args[0] == "config": return json.dumps(self.config)
        self.writes.append(tuple(args))
        if args[0] == "build" and self.after_build:
            self.after_build()
        if args[0] == "run" and "get_heads" in str(args): return self.head
        return ""

    def sql(self, query):
        return str(self.tables)


class FirstDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="corr-demo-unit-")
        self.addCleanup(self.temp.cleanup)
        self.c = FakeContext(Path(self.temp.name))
        for obj, name, value in (
            (first.platform, "system", lambda: "Linux"), (first, "port_free", lambda: True),
            (first.shutil, "disk_usage", lambda path: SimpleNamespace(free=10**12)),
            (first.op, "raw_current", lambda c: [HEAD]), (first.op, "health", lambda c: None),
        ):
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        response = MagicMock()
        response.__enter__.return_value = SimpleNamespace(status=200, read=lambda: b'<base href="/">')
        p = patch.object(first.urllib.request, "urlopen", return_value=response); p.start(); self.addCleanup(p.stop)
        p = patch.dict(first.os.environ, {"DOCKER_HOST": "", "DOCKER_TLS_VERIFY": "", "DOCKER_CERT_PATH": ""})
        p.start(); self.addCleanup(p.stop)

    def old(self, running=False):
        item = container(running=running)
        self.c.items[item["Id"]] = item
        return item

    def refusal(self, reset=True):
        with self.assertRaises(first.op.Refusal):
            first.FirstDemo(self.c, reset).execute()
        self.assertEqual(self.c.writes, [])

    def test_check_makes_no_docker_changes(self):
        first.FirstDemo(self.c).execute(check_only=True)
        self.assertEqual(self.c.writes, [])

    def test_fresh_cut_orders_build_postgres_migrate_app(self):
        first.FirstDemo(self.c).execute()
        writes = self.c.writes
        self.assertEqual(writes[0], ("build",))
        pg = next(i for i, a in enumerate(writes) if a[0] == "up" and a[-1] == "postgres")
        migration = next(i for i, a in enumerate(writes) if "upgrade" in a)
        app = next(i for i, a in enumerate(writes) if a[0] == "up" and a[-1] == "web")
        self.assertLess(pg, migration); self.assertLess(migration, app)
        self.assertFalse(any(a[0] in {"rm", "volume-rm"} for a in writes))

    def test_reset_uses_only_full_validated_ids_and_exact_volumes(self):
        item = self.old(True)
        name = "correspondencia_sipe_sipe_postgres_data"
        self.c.volumes[name] = volume(name)
        self.c.users[name] = [item["Id"]]
        first.FirstDemo(self.c, True).execute()
        self.assertIn(("rm", item["Id"]), self.c.writes)
        self.assertIn(("volume-rm", name), self.c.writes)
        self.assertEqual(self.c.writes[0], ("build",))

    def test_reset_method_without_flag_refuses(self):
        with self.assertRaises(first.op.Refusal):
            first.FirstDemo(self.c).reset_resources()
        self.assertEqual(self.c.writes, [])

    def test_running_legacy_without_flag_refuses(self):
        self.old(True); self.refusal(False)

    def test_stopped_history_preserved_without_flag(self):
        self.old(False)
        first.FirstDemo(self.c).execute(check_only=True)
        self.assertEqual(self.c.writes, [])

    def test_existing_demo_data_requires_flag(self):
        self.c.volumes[first.DEMO_DB] = volume(first.DEMO_DB, first.op.PROJECT)
        self.refusal(False)

    def test_gestion_database_rejected(self):
        self.c.v["POSTGRES_DB"] = "gestion_sipe"; self.refusal()

    def test_gestion_user_rejected(self):
        self.c.v["POSTGRES_USER"] = "sipe_sipe_gestion"; self.refusal()

    def test_known_container_pointing_to_gestion_db_rejected(self):
        item = self.old(); item["Config"]["Env"] = ["POSTGRES_DB=gestion_sipe"]; self.refusal()

    def test_protected_host_ports_rejected(self):
        for port in ("8001", "5433"):
            with self.subTest(port=port):
                self.c.items = {}
                self.old()["HostConfig"]["PortBindings"] = {"8000/tcp": [{"HostPort": port}]}
                self.refusal()

    def test_protected_port_in_container_command_rejected(self):
        self.old()["Config"]["Cmd"] = ["uvicorn", "--port", "8001"]
        self.refusal()

    def test_host_network_container_cannot_be_reset(self):
        self.old()["HostConfig"]["NetworkMode"] = "host"
        self.refusal()

    def test_gestion_volume_mount_rejected(self):
        self.old()["Mounts"] = [{"Type": "volume", "Name": "gestion_postgres_data", "Source": "/var/lib/docker/volumes/gestion_postgres_data"}]
        self.refusal()

    def test_gestion_bind_mount_rejected(self):
        self.old()["Mounts"] = [{"Type": "bind", "Source": "/srv/sipe_sipe_gestion/backend"}]
        self.refusal()

    def test_unknown_bind_root_rejected(self):
        self.old()["Mounts"] = [{"Type": "bind", "Source": "/"}]; self.refusal()

    def test_other_project_container_rejected_even_with_historical_name(self):
        self.old()["Config"]["Labels"]["com.docker.compose.project"] = "other"; self.refusal()

    def test_extra_service_in_project_rejected(self):
        item = container("unknown-service", project=first.op.PROJECT, service="other")
        self.c.items[item["Id"]] = item; self.refusal()

    def test_unlabelled_new_demo_volume_not_adopted(self):
        item = volume(first.DEMO_DB); item["Labels"] = {}
        self.c.volumes[first.DEMO_DB] = item; self.refusal()

    def test_volume_from_another_project_rejected(self):
        name = "correspondencia_sipe_sipe_postgres_data"
        self.c.volumes[name] = volume(name, project="other"); self.refusal()

    def test_volume_driver_options_rejected(self):
        name = "correspondencia_sipe_sipe_postgres_data"
        self.c.volumes[name] = volume(name); self.c.volumes[name]["Options"] = {"device": "/srv/gestion"}
        self.refusal()

    def test_foreign_volume_consumer_blocks_every_removal(self):
        name = "correspondencia_sipe_sipe_postgres_data"
        self.c.volumes[name] = volume(name); self.c.users[name] = ["foreign-id"]
        self.refusal()

    def test_unrelated_resources_are_not_candidates(self):
        item = container("gestion-backend", identity="b" * 64, project="sipe_sipe_gestion", service="backend", running=True)
        self.c.items[item["Id"]] = item
        self.c.volumes["gestion_sipe_data"] = volume("gestion_sipe_data", project="sipe_sipe_gestion")
        first.FirstDemo(self.c, True).execute(check_only=True)
        self.assertEqual(self.c.writes, [])

    def test_foreign_publisher_8080_blocks_cut(self):
        self.c.publishers = ["foreign-id"]; self.refusal()

    def test_unknown_listener_8080_blocks_cut(self):
        with patch.object(first, "port_free", return_value=False): self.refusal()

    def test_linux_required(self):
        with patch.object(first.platform, "system", return_value="Windows"): self.refusal()

    def test_wrong_remote_rejected(self):
        self.c.remote = "https://github.com/AndyGuzmanEscalera/sipe_sipe_gestion.git"; self.refusal()

    def test_remote_docker_daemon_rejected(self):
        self.c.endpoint = "ssh://another-server"; self.refusal()

    def test_networks_without_ipam_config_are_tolerated(self):
        cases = (
            ("missing_ipam", {}),
            ("null_ipam", {"IPAM": None}),
            ("missing_config", {"IPAM": {}}),
            ("null_config", {"IPAM": {"Config": None}}),
            ("empty_config", {"IPAM": {"Config": []}}),
            ("valid_subnet", {"IPAM": {"Config": [{"Subnet": "10.241.0.0/24"}]}}),
        )
        for name, network in cases:
            with self.subTest(name=name):
                self.c.networks = {"sample": network}
                first.FirstDemo(self.c).execute(check_only=True)
                self.assertEqual(self.c.writes, [])

    def test_foreign_network_overlap_blocks_before_cut(self):
        self.c.networks["foreign"] = {"IPAM": {"Config": [{"Subnet": "172.30.237.0/24"}]}, "Labels": {}, "Driver": "bridge"}
        self.refusal()

    def test_existing_owned_edge_can_be_reused(self):
        self.c.networks["owned"] = {"IPAM": {"Config": [{"Subnet": "172.30.237.0/28"}]},
            "Labels": {"com.docker.compose.project": first.op.PROJECT, "com.docker.compose.network": "edge"}, "Driver": "bridge"}
        first.FirstDemo(self.c).execute(check_only=True)
        self.assertEqual(self.c.writes, [])

    def test_low_disk_refuses_before_build(self):
        with patch.object(first.shutil, "disk_usage", return_value=SimpleNamespace(free=0)): self.refusal()

    def test_external_env_refused_for_first_demo(self):
        self.c.envfile = self.c.root / "other.env"; self.refusal()

    def test_adopt_mode_refused(self):
        self.c.v["INSTALL_MODE"] = "adopt"; self.refusal()

    def test_extra_service_in_compose_refused(self):
        self.c.config["services"]["gestion"] = {}; self.refusal()

    def test_postgres_host_port_refused(self):
        self.c.config["services"]["postgres"]["ports"] = [{"published": "5432", "target": 5432}]
        self.refusal()

    def test_web_public_binding_refused(self):
        self.c.config["services"]["web"]["ports"][0]["host_ip"] = "0.0.0.0"; self.refusal()

    def test_external_demo_volume_refused(self):
        self.c.config["volumes"]["postgres_data"]["external"] = True; self.refusal()

    def test_health_history_does_not_change_identity_fingerprint(self):
        item = self.old(True)
        f = first.FirstDemo(self.c, True)
        a = f.inspect_plan()
        item["State"]["Health"]["Log"].append({"Start": "later"})
        self.assertEqual(a, f.inspect_plan())

    def test_inventory_change_after_build_refuses_before_deletion(self):
        item = self.old(True)
        self.c.after_build = lambda: item["Config"]["Labels"].update({"unexpected": "changed"})
        with self.assertRaises(first.op.Refusal):
            first.FirstDemo(self.c, True).execute()
        self.assertEqual(self.c.writes, [("build",)])

    def test_nonempty_new_database_stops_before_upgrade(self):
        self.c.tables = 1
        with self.assertRaises(first.op.Refusal):
            first.FirstDemo(self.c).execute()
        self.assertFalse(any("upgrade" in a for a in self.c.writes))

    def test_wrong_image_head_stops_before_upgrade(self):
        self.c.head = "other"
        with self.assertRaises(first.op.Refusal):
            first.FirstDemo(self.c).execute()
        self.assertFalse(any("upgrade" in a for a in self.c.writes))


if __name__ == "__main__":
    unittest.main()
