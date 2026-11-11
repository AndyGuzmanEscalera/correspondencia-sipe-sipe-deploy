"""Local-only image validation. Creates/deletes exclusively UUID-labelled resources.

Requires prebuilt correspondencia-{backend,web}:test. Never loads host .env,
never publishes DB ports, never mounts host source or persistent municipal data.
Run after building both local images FROM THIS DEPLOY repository.
No development sources or test suites are installed in runtime images.
"""
import argparse
import ipaddress
import json
import urllib.request
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DOCKER = os.environ.get("DOCKER", "docker")
RUN = "corr-images-" + uuid.uuid4().hex[:12]
LABEL = "municipal.image-validation=" + RUN
NETWORK, DATA_NETWORK, VOLUME = RUN + "-edge", RUN + "-data", RUN + "-storage"
CONTAINERS = []
RESOURCES = []
PASSWORD = uuid.uuid4().hex
JWT = uuid.uuid4().hex + uuid.uuid4().hex
ENV = {
    "DATABASE_URL": "",
    "POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "image_validation", "POSTGRES_USER": "image_validation",
    "POSTGRES_PASSWORD": PASSWORD, "JWT_ACCESS_SECRET": JWT,
    "PUBLIC_TRACKING_HMAC_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
    "APP_ENV": "production", "COOKIE_SECURE": "true",
    "STORAGE_ROOT": "/app/storage", "PYTHONDONTWRITEBYTECODE": "1",
}


def docker(*args, stdin=None):
    result = subprocess.run(
        [DOCKER, *map(str, args)], input=stdin, text=True,
        encoding="utf-8", errors="replace", capture_output=True,
    )
    output = result.stdout + result.stderr
    for secret in (PASSWORD, JWT, ENV["PUBLIC_TRACKING_HMAC_SECRET"]):
        output = output.replace(secret, "<synthetic-secret>")
    if result.returncode:
        raise RuntimeError("Docker operation failed: " + output)
    return result.stdout


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def env_args(values):
    return [arg for key, value in values.items() for arg in ("-e", key + "=" + value)]


def start(name, *args, network=NETWORK):
    # Record only successful creations; cleanup never targets unrelated resources.
    docker("run", "-d", "--name", name, "--label", LABEL, "--network", network, *args)
    CONTAINERS.append(name)


def wait_for(action, description, seconds=90):
    deadline = time.monotonic() + seconds
    error = ""
    while time.monotonic() < deadline:
        try:
            return action()
        except (RuntimeError, ValueError) as exc:
            error = str(exc)
            time.sleep(1)
    raise RuntimeError(description + ": " + error)


def execute_python(container, code):
    return docker("exec", "-i", container, "python", "-B", "-", stdin=code)


def request(web, path, *, data=None):
    args = ["exec", web, "curl", "-sS", "--fail", "--max-time", "8"]
    if data is not None:
        args += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    return docker(*args, "http://127.0.0.1:8080" + path)


def validate(args):
    docker("version", "--format", "{{.Server.Version}}")
    require(shutil.which(DOCKER) or Path(DOCKER).is_file(), "Docker executable missing")
    docker("network", "create", "--label", LABEL, NETWORK)
    RESOURCES.append(("network", NETWORK))
    docker("network", "create", "--internal", "--label", LABEL, DATA_NETWORK)
    RESOURCES.append(("network", DATA_NETWORK))
    subnet = json.loads(docker("network", "inspect", NETWORK))[0]["IPAM"]["Config"][0]["Subnet"]
    network = ipaddress.ip_network(subnet)
    web_ip, backend_ip = str(network.network_address + 10), str(network.network_address + 11)
    docker("volume", "create", "--label", LABEL, VOLUME)
    RESOURCES.append(("volume", VOLUME))
    pg, backend, web, runner = (RUN + suffix for suffix in ("-pg", "-backend", "-web", "-tests"))
    start(pg, "--network-alias", "postgres",
          "-e", "POSTGRES_DB=image_validation", "-e", "POSTGRES_USER=image_validation",
          "-e", "POSTGRES_PASSWORD=" + PASSWORD, args.postgres_image, network=DATA_NETWORK)
    wait_for(lambda: docker("exec", pg, "pg_isready", "-U", "image_validation"), "Postgres startup")
    # No host ports or sources; the image supplies all migration files.
    print(docker("run", "--rm", "--read-only", "--network", DATA_NETWORK,
                 "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", *env_args(ENV),
                 args.backend_image, "alembic", "heads"), flush=True)
    print(docker("run", "--rm", "--read-only", "--network", DATA_NETWORK,
                 "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", *env_args(ENV),
                 args.backend_image, "alembic", "upgrade", "head"), flush=True)
    current = docker("run", "--rm", "--read-only", "--network", DATA_NETWORK,
                     "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", *env_args(ENV),
                     args.backend_image, "alembic", "current")
    require("n1b2c3d4e5f6" in current, "Unexpected Alembic revision")
    print("Alembic current: " + current.strip(), flush=True)
    start(backend, "--ip", backend_ip, "--network-alias", "backend", "--read-only",
          "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
          "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", "-v", VOLUME + ":/app/storage",
          *env_args(ENV), args.backend_image, "uvicorn", "app.main:app",
          "--host", "0.0.0.0", "--port", "8000", "--proxy-headers",
          "--forwarded-allow-ips", web_ip)
    docker("network", "connect", DATA_NETWORK, backend)
    def healthy():
        result = json.loads(execute_python(backend, "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/database', timeout=4))))"))
        require(result == {"status": "ok", "database": "connected"}, "Database health failed: " + str(result))
    wait_for(healthy, "Backend startup")
    print(execute_python(backend, """import os, errno
from pathlib import Path
from app.main import app
assert os.getuid() == 10001 and os.getgid() == 10001
assert app.openapi()['paths']['/health']
assert app.openapi()['paths']['/public/correspondence/track']
Path('/app/storage/synthetic.txt').write_text('synthetic image smoke')
try:
    Path('/app/forbidden.txt').write_text('must fail')
    raise AssertionError('Root filesystem was writable')
except OSError as e:
    assert e.errno in (errno.EROFS, errno.EACCES)
for p in Path('/app').rglob('*'):
    assert p.name not in ('.git', '.env', '__pycache__', 'website', 'node_modules')
    assert p.suffix not in ('.pyc', '.pyo', '.sql', '.dump')
print('Import/OpenAPI/non-root/read-only/storage/image contents: OK')
"""), flush=True)
    print(execute_python(backend, (ROOT / "tests/pdf_probe.py").read_text(encoding="utf-8")), flush=True)
    if args.backend_only:
        return
    start(web, "--ip", web_ip, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
          "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "-p", "127.0.0.1::8080", args.web_image)
    print(docker("exec", web, "httpd", "-t"), flush=True)
    modules = docker("exec", web, "httpd", "-M")
    for module in ("proxy_module", "proxy_http_module", "rewrite_module", "headers_module", "remoteip_module"):
        require(module in modules, "Missing Apache module " + module)
    includes = docker("exec", web, "httpd", "-t", "-D", "DUMP_INCLUDES")
    require("/conf/extra/correspondencia.conf" in includes, "Apache did not include deploy config")
    require(docker("exec", web, "id", "-u").strip() == "10001", "Web is root")
    wait_for(lambda: request(web, "/index.html"), "Apache startup")
    html = request(web, "/")
    require('<base href="/">' in html, "Wrong Flutter base href")
    for path in ("/correspondencia", "/correspondencia/login", "/correspondencia/home",
                 "/correspondencia/consulta", "/consulta-publica", "/synthetic-spa-route"):
        require(request(web, path) == html, "SPA fallback failed for " + path)
    for path in ("/main.dart.js", "/flutter_bootstrap.js", "/assets/AssetManifest.json"):
        require(len(request(web, path)) > 20, "Missing Flutter asset " + path)
    for path in ("/index.html", "/main.dart.js", "/api/health"):
        headers = docker("exec", web, "curl", "-sSI", "http://127.0.0.1:8080" + path).lower()
        require("cache-control: no-store, max-age=0" in headers, "Unsafe cache: " + path)
        require("x-content-type-options: nosniff" in headers, "Missing nosniff")
    for path in ("/api/health", "/api/health/database"):
        require(json.loads(request(web, path))["status"] == "ok", "Proxy health failed")
    require("/public/correspondence/track" in json.loads(request(web, "/api/openapi.json"))["paths"], "OpenAPI proxy failed")
    public = json.loads(request(web, "/api/public/correspondence/track", data={}))
    require("detail" not in public, "Public validation disclosed internal error")
    # Real authentication against a generated user in this disposable database.
    username, password = RUN.replace("-", "_"), uuid.uuid4().hex
    execute_python(backend, (
        "from app.main import app; from app.core.database import SessionLocal; from app.core.security import hash_password; "
        "from app.modules.identity.user import User; "
        "s=SessionLocal(); s.add(User(username=" + repr(username) +
        ", password_hash=hash_password(" + repr(password) +
        "), is_active=True)); s.commit(); s.close()"
    ))
    login_headers = docker("exec", web, "curl", "-sS", "--fail", "-D", "-", "-o", "/tmp/synthetic-login.json",
                           "-H", "Content-Type: application/json", "-d",
                           json.dumps({"username": username, "password": password}),
                           "http://127.0.0.1:8080/api/auth/login")
    login = json.loads(docker("exec", web, "cat", "/tmp/synthetic-login.json"))
    require(bool(login.get("access_token")), "Synthetic login failed")
    cookie = next(line.split(":", 1)[1].strip().split(";", 1)[0] for line in login_headers.splitlines()
                  if line.lower().startswith("set-cookie: refresh_token="))
    require("path=/api/auth" in login_headers.lower() and "secure" in login_headers.lower(), "Login cookie path/security failed")
    refreshed_headers = docker("exec", web, "curl", "-sS", "--fail", "-D", "-", "-o", "/tmp/synthetic-refresh.json",
                               "-X", "POST", "--cookie", cookie,
                               "http://127.0.0.1:8080/api/auth/refresh")
    refreshed = json.loads(docker("exec", web, "cat", "/tmp/synthetic-refresh.json"))
    require(bool(refreshed.get("access_token")), "Synthetic refresh failed")
    refreshed_cookie = next(line.split(":", 1)[1].strip().split(";", 1)[0] for line in refreshed_headers.splitlines()
                            if line.lower().startswith("set-cookie: refresh_token="))
    require(refreshed_cookie != cookie, "Refresh cookie did not rotate")
    logout = docker("exec", web, "curl", "-sS", "-D", "-", "-o", "/dev/null", "-X", "POST",
                    "--cookie", refreshed_cookie, "http://127.0.0.1:8080/api/auth/logout")
    print("Synthetic login -> refresh rotation -> logout: OK", flush=True)
    require("204" in logout.splitlines()[0], "Logout route failed")
    cookie = logout.lower()
    require("path=/api/auth" in cookie and "httponly" in cookie and "secure" in cookie, "Cookie proxy contract failed")
    docker("exec", web, "sh", "-ec", "test ! -e /opt/flutter; test ! -e /src; test ! -e /usr/local/apache2/htdocs/.env; test ! -e /usr/local/apache2/htdocs/.git; test ! -e /usr/local/apache2/htdocs/website")
    print("Apache modules/non-root/SPA/assets/cache/proxy/public validation/cookie path: OK", flush=True)
    endpoint = docker("port", web, "8080/tcp").strip()
    require(endpoint.startswith("127.0.0.1:"), "Test port must bind loopback only")
    with urllib.request.urlopen("http://" + endpoint + "/api/health", timeout=8) as response:
        require(json.load(response)["status"] == "ok", "Host loopback proxy failed")
    print("Host loopback -> web -> backend -> isolated DB: OK (" + endpoint + ")", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", default="correspondencia-backend:test")
    parser.add_argument("--web-image", default="correspondencia-web:test")
    parser.add_argument("--postgres-image", default="postgres:16-alpine")
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    try:
        validate(args)
        print("PASS: isolated image validation", flush=True)
    finally:
        failures = []
        for container in reversed(CONTAINERS):
            try:
                docker("rm", "-f", "-v", container)
            except RuntimeError as exc:
                failures.append(str(exc))
        for kind, name in reversed(RESOURCES):
            try:
                docker(kind, "rm", name)
            except RuntimeError as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("Temporary cleanup incomplete: " + "; ".join(failures))
        print("Only resources created by " + RUN + " were removed.", flush=True)


if __name__ == "__main__":
    main()
