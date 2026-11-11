#!/usr/bin/env python3
"""Primer corte DEMO. Allowlist fija; sin borrados globales, sin acceso a otros servicios."""
import argparse
from contextlib import contextmanager
import json
import ipaddress
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import sys
import urllib.request

import operations as op

LEGACY_CONTAINERS = {"sipe_correspondencia_backend", "sipe_correspondencia_db"}
LEGACY_VOLUMES = {"correspondencia_sipe_sipe_postgres_data", "correspondencia_sipe_sipe_backend_storage"}
DEMO_DB = "correspondencia_demo_postgres_data"
DEMO_STORAGE = "correspondencia_demo_backend_storage"
DEMO_VOLUMES = {DEMO_DB, DEMO_STORAGE}
ALL_VOLUMES = LEGACY_VOLUMES | DEMO_VOLUMES
SERVICES = {"postgres", "backend", "web"}
PROJECTS = {op.PROJECT, "correspondencia_sipe_sipe"}
REMOTES = {"https://github.com/AndyGuzmanEscalera/correspondencia-sipe-sipe-deploy.git",
           "git@github.com:AndyGuzmanEscalera/correspondencia-sipe-sipe-deploy.git"}
DENIED = re.compile(r"gestion|gestión|website|(?:^|\D)(?:8001|5433)(?:\D|$)", re.I)


def safe_identifier(value, field):
    op.require(not DENIED.search(str(value)), "Protección Gestión/Website: recurso prohibido en " + field)


def port_free():
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 8080))
        return True
    except OSError:
        return False


def container_safe(item):
    name = item["Name"].lstrip("/")
    labels = item["Config"].get("Labels") or {}
    project = labels.get("com.docker.compose.project")
    service = labels.get("com.docker.compose.service")
    legacy = name in LEGACY_CONTAINERS
    op.require(legacy or (project == op.PROJECT and service in SERVICES), "Contenedor fuera de Correspondencia")
    op.require(not project or project in PROJECTS, "El contenedor pertenece a otro proyecto")
    for field, value in (("nombre", name), ("imagen", item["Config"].get("Image", "")),
                         ("labels", json.dumps(labels)), ("red", list(item.get("NetworkSettings", {}).get("Networks", {})))):
        safe_identifier(value, field)
    for value in item["Config"].get("Env", []):
        if value.split("=", 1)[0] in {"POSTGRES_DB", "POSTGRES_USER", "DATABASE_URL", "PGDATABASE", "PGHOST"}:
            safe_identifier(value, "entorno de base del contenedor")
    safe_identifier(" ".join(item["Config"].get("Cmd") or []), "comando del contenedor")
    host = item.get("HostConfig", {})
    op.require(not host.get("Privileged") and host.get("NetworkMode") != "host", "Contenedor privilegiado/red host: no se puede aislar con seguridad")
    ports = host.get("PortBindings") or {}
    for binding in ports.values():
        for published in binding or []:
            safe_identifier(published.get("HostPort", ""), "puerto host")
    for mount in item.get("Mounts", []):
        safe_identifier(mount.get("Name", "") + mount.get("Source", ""), "montaje")
        if mount["Type"] == "volume":
            allowed = LEGACY_VOLUMES if legacy else ({DEMO_DB} if service == "postgres" else {DEMO_STORAGE} if service == "backend" else set())
            op.require(mount.get("Name") in allowed, "Contenedor candidato tiene un volumen no autorizado")
        elif mount["Type"] == "bind":
            parts = Path(mount["Source"]).parts
            op.require(legacy and any(p in {"correspondencia_sipe_sipe", "correspondencia-sipe-sipe-deploy"} for p in parts),
                       "Bind mount fuera de Correspondencia")
        else:
            op.require(mount["Type"] == "tmpfs", "Montaje no reconocido")
    return name


def volume_safe(name, item):
    op.require(name in ALL_VOLUMES and item["Name"] == name, "Volumen fuera de allowlist")
    labels = item.get("Labels") or {}
    safe_identifier(name, "volumen")
    safe_identifier(json.dumps(labels), "labels del volumen")
    op.require(item.get("Driver") == "local" and not item.get("Options"), "Volumen con driver/opciones externos: detener")
    project = labels.get("com.docker.compose.project")
    if name in DEMO_VOLUMES:
        role = "postgres_data" if name == DEMO_DB else "backend_storage"
        op.require(project == op.PROJECT and labels.get("com.docker.compose.volume") == role,
                   "El volumen demo no pertenece inequívocamente a este proyecto")
    else:
        op.require(not project or project in PROJECTS, "Volumen histórico pertenece a otro proyecto")


def compose_safe(config, c):
    op.require(config["name"] == op.PROJECT and set(config["services"]) == SERVICES, "Proyecto/servicios inesperados")
    services = config["services"]
    for name in ("postgres", "backend"):
        op.require(not services[name].get("ports"), "Solo web puede publicar un puerto")
    web_ports = services["web"].get("ports", [])
    op.require(len(web_ports) == 1 and str(web_ports[0]["published"]) == "8080"
               and str(web_ports[0]["target"]) == "8080" and web_ports[0].get("host_ip") == "127.0.0.1", "Web debe publicar únicamente 127.0.0.1:8080")
    op.require(set(map(str, services["backend"].get("expose", []))) == {"8000"}, "Backend expose incompatible")
    op.require(config["networks"]["data"].get("internal") is True, "La red de DB debe ser interna")
    for key, name in (("postgres_data", DEMO_DB), ("backend_storage", DEMO_STORAGE)):
        volume = config["volumes"][key]
        op.require(volume.get("name") == name and not volume.get("external", False), "Modo demo requiere volúmenes nuevos administrados")
    for name in SERVICES:
        service = services[name]
        op.require(not service.get("privileged") and service.get("network_mode") != "host", "Servicio privilegiado/red host no permitido")
        for mount in service.get("volumes", []):
            op.require(mount["type"] == "volume" and mount["source"] in {"postgres_data", "backend_storage"}, "Solo se permiten los volúmenes propios")
        if name in {"backend", "web"}:
            context = Path(service["build"]["context"]).resolve()
            op.require(context == c.root / name, "Build context fuera del repo")
    op.require(services["postgres"]["image"] == "postgres:16-alpine", "Se requiere PostgreSQL 16 Alpine")


class FirstDemo:
    def __init__(self, c, reset=False):
        self.c, self.reset = c, reset
        self.containers, self.volumes = [], {}
        self.fingerprint = None

    def inspect_plan(self):
        c = self.c
        names = {}
        for line in c.run(["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}}"]).splitlines():
            identity, name = line.split(maxsplit=1)
            if name in LEGACY_CONTAINERS:
                names[identity] = name
        own = c.run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + op.PROJECT, "--format", "{{.ID}}"]).split()
        for identity in own:
            names.setdefault(identity, "")
        self.containers = []
        for identity in sorted(names):
            item = json.loads(c.run(["docker", "inspect", identity]))[0]
            container_safe(item)
            if all(existing["Id"] != item["Id"] for existing in self.containers):
                self.containers.append(item)
        existing_volumes = set(c.run(["docker", "volume", "ls", "--format", "{{.Name}}"]).split())
        self.volumes = {}
        for name in sorted(ALL_VOLUMES & existing_volumes):
            item = json.loads(c.run(["docker", "volume", "inspect", name]))[0]
            volume_safe(name, item)
            self.volumes[name] = item
            users = c.run(["docker", "ps", "-a", "--filter", "volume=" + name, "--format", "{{.ID}}"]).split()
            op.require(all(any(v["Id"].startswith(user) or user.startswith(v["Id"]) for v in self.containers) for user in users),
                       "Un contenedor ajeno utiliza un volumen candidato; no se borrará nada")
        if not self.reset:
            op.require(not (DEMO_VOLUMES & set(self.volumes)), "Ya existen datos demo. Usar actualización normal o --reset-demo-data explícito")
            op.require(not any(v["State"]["Running"] or v["Name"].lstrip("/") not in LEGACY_CONTAINERS for v in self.containers),
                       "Hay servicios demo/históricos activos. El corte limpio requiere --reset-demo-data")
        publishers = c.run(["docker", "ps", "--filter", "publish=8080", "--format", "{{.ID}}"]).split()
        planned = {v["Id"] for v in self.containers} if self.reset else set()
        op.require(all(any(i.startswith(p) or p.startswith(i) for i in planned) for p in publishers), "8080 pertenece a otro contenedor; no se tocará")
        if not port_free():
            op.require(self.reset and bool(publishers), "Puerto 8080 ocupado por un servicio no autorizado")
        stable = [{key: item.get(key) for key in ("Id", "Name", "Config", "Mounts", "HostConfig")} |
                  {"running": item["State"]["Running"], "started": item["State"].get("StartedAt")}
                  for item in self.containers]
        return json.dumps({"containers": stable, "volumes": self.volumes}, sort_keys=True)

    def preflight(self):
        c = self.c
        op.require(platform.system() == "Linux", "Solo ejecutar en Linux/Debian")
        op.require(c.envfile == c.root / ".env", "Primer demo requiere .env regular en la raíz del repo")
        op.require(c.v.get("INSTALL_MODE", "demo") == "demo", "Primer corte solo admite INSTALL_MODE=demo")
        op.require(c.v["POSTGRES_VOLUME"] == DEMO_DB and c.v["STORAGE_VOLUME"] == DEMO_STORAGE, "No adoptar volúmenes históricos en primer demo")
        for field in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_VOLUME", "STORAGE_VOLUME"):
            safe_identifier(c.v[field], field)
        safe_identifier(str(c.root), "ruta del repo")
        op.require(c.run(["git", "-c", "safe.directory=" + str(c.root), "rev-parse", "--show-toplevel"]).strip() == str(c.root), "Raíz Git incorrecta")
        op.require(c.run(["git", "-c", "safe.directory=" + str(c.root), "remote", "get-url", "origin"]).strip() in REMOTES, "Remote distinto al repo deploy oficial")
        op.require(not c.run(["git", "-c", "safe.directory=" + str(c.root), "status", "--porcelain"]).strip(), "Se requiere un checkout limpio y commit aprobado")
        op.require(c.m["status"] == "validated", "Release sin validación local")
        op.require(not any(os.environ.get(k) for k in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")), "Eliminar overrides de daemon remoto")
        context = json.loads(c.run(["docker", "context", "inspect"]))[0]
        op.require(context["Endpoints"]["docker"]["Host"].startswith("unix://"), "Usar únicamente Docker local en Debian")
        info = json.loads(c.run(["docker", "info", "--format", "{{json .}}"]))
        op.require(info["OSType"] == "linux", "Se requiere motor Docker Linux")
        version = c.run(["docker", "compose", "version", "--short"]).strip().lstrip("v")
        match = re.match(r"(\d+)\.(\d+)", version)
        op.require(match and tuple(map(int, match.groups())) >= (2, 30), "Se requiere Docker Compose >=2.30")
        config = json.loads(c.dc("config", "--format", "json"))
        compose_safe(config, c)
        subnet = ipaddress.ip_network(config["networks"]["edge"]["ipam"]["config"][0]["subnet"])
        op.require(str(subnet) == "172.30.237.0/28", "Subnet inesperado para el proxy confiable")
        for identity in c.run(["docker", "network", "ls", "-q"]).split():
            network = json.loads(c.run(["docker", "network", "inspect", identity]))[0]
            for role in ("edge", "data"):
                if network.get("Name") == op.PROJECT + "_" + role:
                    labels = network.get("Labels") or {}
                    op.require(labels.get("com.docker.compose.project") == op.PROJECT
                               and labels.get("com.docker.compose.network") == role
                               and network.get("Driver") == "bridge"
                               and bool(network.get("Internal")) == (role == "data"),
                               "La red esperada pertenece a otro proyecto o tiene configuración incompatible")
            for entry in network.get("IPAM", {}).get("Config", []):
                other = ipaddress.ip_network(entry["Subnet"])
                if subnet.version == other.version and subnet.overlaps(other):
                    labels = network.get("Labels") or {}
                    op.require(labels.get("com.docker.compose.project") == op.PROJECT
                               and labels.get("com.docker.compose.network") == "edge"
                               and network.get("Driver") == "bridge" and subnet == other,
                               "El subnet 172.30.237.0/28 se solapa con otra red. No se tocará ni eliminará esa red")
        for disk in {str(c.root), info["DockerRootDir"]}:
            op.require(Path(disk).exists(), "No se puede comprobar el disco local de Docker")
            op.require(shutil.disk_usage(disk).free >= int(c.v.get("MIN_FREE_GB", "5")) * 1024**3, "Espacio libre insuficiente")
        self.fingerprint = self.inspect_plan()
        print("\nPLAN DEL PRIMER CORTE", flush=True)
        print("Proyecto: " + op.PROJECT + "\nVolúmenes nuevos: " + ", ".join(sorted(DEMO_VOLUMES)), flush=True)
        print("Servicios a crear: postgres, backend, web (solo este proyecto).", flush=True)
        print("Puerto único: 127.0.0.1:8080; PostgreSQL/backend internos.", flush=True)
        print("RESET DEMO AUTORIZADO" if self.reset else "SIN BORRAR DATOS EXISTENTES", flush=True)
        if self.reset:
            print("Estos datos DEMO se descartan SIN backup automático.", flush=True)
            print("Contenedores a eliminar: " + (", ".join(v["Name"].lstrip("/") for v in self.containers) or "ninguno"), flush=True)
            print("Volúmenes a eliminar: " + (", ".join(sorted(self.volumes)) or "ninguno"), flush=True)
        print("Gestión, 8001, 5433, Website y contenedores ajenos: EXCLUIDOS.", flush=True)

    def reset_resources(self):
        op.require(self.reset, "Reset sin flag explícito")
        op.require(self.inspect_plan() == self.fingerprint, "Cambió el inventario tras preflight; volver a inspeccionar")
        # Full container IDs, never names reused after validation. No -v and no prune.
        for item in self.containers:
            self.c.run(["docker", "rm", "-f", item["Id"]], timeout=180, label="Reset contenedor Correspondencia")
        for name, before in sorted(self.volumes.items()):
            after = json.loads(self.c.run(["docker", "volume", "inspect", name]))[0]
            op.require(after == before, "Cambió el volumen candidato; detener")
            users = self.c.run(["docker", "ps", "-a", "--filter", "volume=" + name, "--format", "{{.ID}}"]).strip()
            op.require(not users, "Volumen sigue en uso; no se eliminará")
            self.c.run(["docker", "volume", "rm", name], label="Reset volumen Correspondencia")

    def execute(self, check_only=False):
        self.preflight()
        if check_only:
            print("CHECK OK: no se construyó, borró, migró ni inició ningún recurso.", flush=True)
            return
        c = self.c
        # Finish both builds before removing any historical service/data.
        c.dc("build", timeout=3600, label="Build antes del corte")
        if self.reset:
            self.reset_resources()
        else:
            op.require(self.inspect_plan() == self.fingerprint, "Cambió el inventario; detener antes de crear recursos")
        op.require(port_free(), "8080 sigue ocupado; detener antes de iniciar")
        c.dc("up", "-d", "--wait", "--wait-timeout", "180", "postgres", timeout=240)
        tables = int(c.sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"))
        op.require(tables == 0, "La base demo no está vacía: no se aplicarán migraciones")
        code = "from alembic.config import Config; from alembic.script import ScriptDirectory; print(','.join(ScriptDirectory.from_config(Config('alembic.ini')).get_heads()))"
        op.require(c.dc("run", "--rm", "--no-deps", "backend", "python", "-B", "-c", code).strip() == c.m["alembic_head"], "La imagen tiene otra head")
        c.dc("run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head", timeout=3600, label="Migración de base demo nueva")
        op.require(op.raw_current(c) == [c.m["alembic_head"]], "La DB no alcanzó la head esperada")
        c.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "backend", "web", timeout=240)
        op.health(c)
        for route in ("/", "/correspondencia/login", "/correspondencia/consulta", "/consulta-publica"):
            with urllib.request.urlopen("http://127.0.0.1:8080" + route, timeout=8) as response:
                op.require(response.status == 200 and b'<base href="/">' in response.read(), "Frontend/SPA incorrecto")
        print("\nCORRESPONDENCIA DEMO LISTA: http://127.0.0.1:8080", flush=True)
        print("No se creó ningún administrador automáticamente. Alta inicial por acción separada.", flush=True)


@contextmanager
def demo_lock(c):
    import fcntl
    # Lock local ignored, no need to create backup directories for a clean demo.
    fd = os.open(c.root / ".first-deploy-demo.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-demo-data", action="store_true", help="Borra SOLO recursos Correspondencia validados, sin backup automático")
    parser.add_argument("--check", action="store_true", help="Valida y muestra plan sin modificar Docker/DB")
    args = parser.parse_args(argv)
    op.require(platform.system() == "Linux", "Solo ejecutar en Linux/Debian")
    c = op.Context()
    os.umask(0o077)
    with demo_lock(c):
        FirstDemo(c, args.reset_demo_data).execute(args.check)


if __name__ == "__main__":
    try:
        main()
    except (op.Refusal, OSError, ValueError) as exc:
        print("DETENIDO: " + str(exc), file=sys.stderr)
        sys.exit(1)
