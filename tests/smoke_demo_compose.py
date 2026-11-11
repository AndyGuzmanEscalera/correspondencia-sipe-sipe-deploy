"""Real Compose demo bootstrap on PC, UUID-owned volumes and an ephemeral web port.

Does NOT execute first_deploy_demo/reset on the host. No municipal .env or data.
"""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DOCKER = os.environ.get("DOCKER", "docker")
PROJECT = "corr-demo-compose-" + uuid.uuid4().hex[:10]


def docker(*args, input_text=None, env=None):
    p = subprocess.run([DOCKER, *map(str, args)], input=input_text, text=True,
                       encoding="utf-8", errors="replace", capture_output=True, env=env)
    if p.returncode:
        raise RuntimeError("Test Docker failed: " + p.stderr[-4000:])
    return p.stdout


def main():
    with tempfile.TemporaryDirectory(prefix=PROJECT + "-") as folder:
        temp = Path(folder)
        probe = PROJECT + "-probe"
        probe_id = docker("network", "create", "--label", "municipal.first-demo-test=" + PROJECT, probe).strip()
        try:
            subnet = json.loads(docker("network", "inspect", probe_id))[0]["IPAM"]["Config"][0]["Subnet"]
            network = ipaddress.ip_network(subnet)
            web_ip, backend_ip = str(network.network_address + 10), str(network.network_address + 11)
            values = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
                          if line and not line.startswith("#"))
            values.update(POSTGRES_DB="bootstrap_test", POSTGRES_USER="bootstrap_user",
                          POSTGRES_PASSWORD=uuid.uuid4().hex, JWT_ACCESS_SECRET=uuid.uuid4().hex + uuid.uuid4().hex,
                          PUBLIC_TRACKING_HMAC_SECRET=uuid.uuid4().hex + uuid.uuid4().hex,
                          POSTGRES_VOLUME=PROJECT + "-db", STORAGE_VOLUME=PROJECT + "-storage")
            for name in (values["POSTGRES_VOLUME"], values["STORAGE_VOLUME"]):
                assert not docker("volume", "ls", "--format", "{{.Name}}", "--filter", "name=^" + name + "$").strip()
            envfile = temp / "synthetic.env"
            envfile.write_text("".join(key + "=" + value + "\n" for key, value in values.items()), encoding="utf-8")
            env = {**os.environ, "RUNTIME_ENV_FILE": str(envfile)}
            override = temp / "isolated.yml"
            override.write_text("services:\n  backend:\n    command: [uvicorn, app.main:app, --host, 0.0.0.0, --port, '8000', --proxy-headers, --forwarded-allow-ips, '{web_ip}']\n    networks:\n      edge:\n        ipv4_address: {backend_ip}\n  web:\n    ports: !override ['127.0.0.1::8080']\n    networks:\n      edge:\n        ipv4_address: {web_ip}\nnetworks:\n  edge: !override\n    external: true\n    name: {probe}\n".format(web_ip=web_ip, backend_ip=backend_ip, probe=probe), encoding="utf-8")
            base = ["compose", "--project-directory", str(ROOT), "--project-name", PROJECT,
                    "--env-file", str(envfile), "-f", str(ROOT / "docker-compose.yml"), "-f", str(override)]
            def dc(*args):
                return docker(*base, *args, env=env)
            try:
                dc("config", "--quiet")
                config = json.loads(dc("config", "--format", "json"))
                assert not config["volumes"]["postgres_data"].get("external")
                assert not config["volumes"]["backend_storage"].get("external")
                assert not config["services"]["postgres"].get("ports")
                assert not config["services"]["backend"].get("ports")
                print("Compose demo: managed volumes/internal DB/backend, ephemeral loopback web: OK", flush=True)
                dc("build")
                print("Build from deploy repo: OK", flush=True)
                dc("up", "-d", "--wait", "--wait-timeout", "180", "postgres")
                dc("run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head")
                current = dc("run", "--rm", "--no-deps", "backend", "alembic", "current")
                assert "n1b2c3d4e5f6" in current
                dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "backend", "web")
                backend, web = dc("ps", "-q", "backend").strip(), dc("ps", "-q", "web").strip()
                code = "import os,json,urllib.request\nfrom pathlib import Path\nassert os.getuid()==10001 and os.getgid()==10001\nassert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/database'))=={'status':'ok','database':'connected'}\nPath('/app/storage/synthetic-bootstrap.txt').write_text('synthetic')\nprint('Backend non-root, managed storage writable, database health: OK')\n"
                print(docker("exec", "-i", backend, "python", "-B", "-", input_text=code), flush=True)
                print(docker("exec", "-i", backend, "python", "-B", "-", input_text=(ROOT / "tests/pdf_probe.py").read_text(encoding="utf-8")), flush=True)
                for service in ("postgres", "backend", "web"):
                    item = json.loads(docker("inspect", dc("ps", "-q", service).strip()))[0]
                    published = item["HostConfig"].get("PortBindings") or {}
                    if service != "web":
                        assert not published
                    else:
                        assert all(p["HostIp"] == "127.0.0.1" for binding in published.values() for p in binding)
                endpoint = dc("port", "web", "8080").strip()
                assert endpoint.startswith("127.0.0.1:")
                for route in ("/", "/correspondencia/login", "/correspondencia/home", "/correspondencia/consulta", "/consulta-publica"):
                    with urllib.request.urlopen("http://" + endpoint + route, timeout=8) as response:
                        assert response.status == 200 and b'<base href="/">' in response.read()
                with urllib.request.urlopen("http://" + endpoint + "/api/health/database", timeout=8) as response:
                    assert json.load(response)=={"status":"ok","database":"connected"}
                    assert "no-store" in response.headers["Cache-Control"]
                public = urllib.request.Request("http://" + endpoint + "/api/public/correspondence/track",
                                                data=b"{}", headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(public, timeout=8) as response:
                    assert "detail" not in json.load(response)
                count = dc("exec", "-T", "postgres", "psql", "-X", "-At", "-U", values["POSTGRES_USER"],
                           "-d", values["POSTGRES_DB"], "-c", "SELECT count(*) FROM users;").strip()
                assert count == "0", "Startup unexpectedly created user accounts"
                for name in (values["POSTGRES_VOLUME"], values["STORAGE_VOLUME"]):
                    item = json.loads(docker("volume", "inspect", name))[0]
                    assert item["Labels"]["com.docker.compose.project"] == PROJECT
                print("Alembic fresh head, SPA/login/public/API/PDFs, zero auto-created users: OK", flush=True)
                print("PASS: clean managed-volume demo bootstrap", flush=True)
            finally:
                # UUID project + UUID volume names created by this test only.
                dc("down", "--volumes", "--timeout", "15")
                assert not dc("ps", "-a", "-q").strip()
                for name in (values["POSTGRES_VOLUME"], values["STORAGE_VOLUME"]):
                    assert not docker("volume", "ls", "--format", "{{.Name}}", "--filter", "name=^" + name + "$").strip()
                print("Only owned temporary Compose resources removed: " + PROJECT, flush=True)
        finally:
            docker("network", "rm", probe_id)



if __name__ == "__main__":
    main()
