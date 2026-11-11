#!/usr/bin/env python3
"""Build local y operación explícita. No descarga de imágenes privadas ni migración de startup."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "correspondencia-deploy"


class Refusal(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Refusal(message)


def manifest(root=ROOT):
    data = json.loads((root / "release.json").read_text(encoding="utf-8"))
    require(set(data) == {"release", "source_sha", "alembic_head", "build_date", "status", "notes"}, "release.json inválido")
    require(re.fullmatch(r"[0-9a-f]{40}", data["source_sha"]), "SHA fuente inválido")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", data["release"]), "Release inválido")
    require(re.fullmatch(r"[A-Za-z0-9_]+", data["alembic_head"]), "Head inválida")
    require(data["status"] in {"prepared", "validated"}, "Release no preparado")
    return data


def dotenv(path):
    require(path.is_file() and not path.is_symlink(), "Falta archivo de entorno regular")
    if os.name == "posix":
        require(path.stat().st_mode & 0o077 == 0, "El entorno debe tener permisos 600/400")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key), "Formato de entorno inválido")
        require(key not in values and value == value.strip() and not any(c in value for c in "\x00$`\"'"), "Usar valores literales sin comillas ni expansión")
        values[key] = value
    return values


def validate_env(v, root=ROOT):
    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET", "BACKUP_ROOT"):
        require(v.get(key), "Variable vacía: " + key)
    require(v.get("APP_ENV") == "production", "Se requiere production")
    require(v.get("COOKIE_SECURE") == "true" and v.get("COOKIE_PATH") == "/auth", "Cookie segura/path incompatible")
    require(v.get("COOKIE_SAMESITE") == "lax" and v.get("COOKIE_NAME") == "refresh_token", "Contrato cookie incompatible")
    require(v.get("POSTGRES_HOST") == "postgres" and v.get("POSTGRES_PORT") == "5432" and not v.get("DATABASE_URL"), "Usar exclusivamente PostgreSQL interno")
    require(v.get("STORAGE_ROOT") == "/app/storage", "Storage incompatible")
    require(not v.get("CORS_ALLOW_ORIGIN_REGEX"), "Usar orígenes HTTPS explícitos, no regex")
    require(v.get("CORS_ALLOW_CREDENTIALS") == "true", "Pages requiere CORS credentials")
    for origin in filter(None, (x.strip() for x in v.get("CORS_ALLOW_ORIGINS", "").split(","))):
        parsed = urlsplit(origin)
        require(parsed.scheme == "https" and parsed.hostname and "*" not in origin
                and not parsed.username and not parsed.password and parsed.path in {"", "/"}
                and not parsed.query and not parsed.fragment and origin == origin.rstrip("/"),
                "CORS requiere orígenes HTTPS exactos sin comodines")
    require(len(v["JWT_ACCESS_SECRET"]) >= 32 and len(v["PUBLIC_TRACKING_HMAC_SECRET"]) >= 32, "Secretos cortos")
    require(len({v["POSTGRES_PASSWORD"], v["JWT_ACCESS_SECRET"], v["PUBLIC_TRACKING_HMAC_SECRET"]}) == 3, "Secretos deben ser independientes")
    require(re.fullmatch(r"[A-Za-z0-9_-]+", v["POSTGRES_PASSWORD"]), "La contraseña DB debe ser URL-safe; no rotar la existente automáticamente")
    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", v[key]), "Identificador DB inválido")
    mode = v.setdefault("INSTALL_MODE", "demo")
    require(mode in {"demo", "adopt"}, "INSTALL_MODE debe ser demo o adopt")
    expected = ("correspondencia_demo_postgres_data", "correspondencia_demo_backend_storage") if mode == "demo" else ("correspondencia_sipe_sipe_postgres_data", "correspondencia_sipe_sipe_backend_storage")
    defaults = dict(zip(("POSTGRES_VOLUME", "STORAGE_VOLUME"), expected))
    for key, default in defaults.items():
        v.setdefault(key, default)
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", v[key]), "Volumen inválido")
    require((v["POSTGRES_VOLUME"], v["STORAGE_VOLUME"]) == expected, "Nombres de volúmenes incompatibles con INSTALL_MODE")
    backup = Path(v["BACKUP_ROOT"])
    require(backup.is_absolute() and backup.resolve() != root.resolve() and root.resolve() not in backup.resolve().parents, "Backups fuera de Git")
    require(v.get("MIN_FREE_GB", "5").isdigit() and int(v.get("MIN_FREE_GB", "5")) >= 1, "MIN_FREE_GB inválido")


class Context:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.m = manifest(self.root)
        self.envfile = Path(os.environ.get("CORRESPONDENCIA_ENV_FILE", self.root / ".env")).resolve()
        self.v = dotenv(self.envfile)
        validate_env(self.v, self.root)
        self.env = {**os.environ, **self.v, "RUNTIME_ENV_FILE": str(self.envfile)}
        # Never allow COMPOSE_FILE/PROJECT_NAME inherited from another application.
        self.base = ["docker", "compose", "--project-directory", str(self.root),
                     "--project-name", PROJECT, "--env-file", str(self.envfile),
                     "-f", str(self.root / "docker-compose.yml")]
        if self.v["INSTALL_MODE"] == "adopt":
            self.base += ["-f", str(self.root / "docker-compose.adopt.yml")]

    def run(self, args, *, output=None, input_stream=None, timeout=120, label="Comando"):
        try:
            p = subprocess.run(args, env=self.env, cwd=self.root,
                               stdout=subprocess.PIPE if output is None else output,
                               stderr=subprocess.PIPE, stdin=input_stream, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal(label + ": herramienta no disponible o timeout; revisar estado antes de reintentar") from None
        if p.returncode:
            error = p.stderr.decode("utf-8", errors="replace")
            for key in ("POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET"):
                error = error.replace(self.v[key], "<redacted>")
            raise Refusal(label + ": " + error[-2000:])
        return p.stdout.decode("utf-8") if output is None else ""

    def dc(self, *args, **kwargs):
        return self.run([*self.base, *args], **kwargs)

    def sql(self, query):
        return self.dc("exec", "-T", "postgres", "sh", "-ec",
                       'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
                       "sh", query).strip()


def preflight(c):
    require(c.run(["docker", "info", "--format", "{{.OSType}}"], label="Docker").strip() == "linux", "Se requiere Docker Linux")
    c.dc("config", "--quiet")
    for key in ("POSTGRES_VOLUME", "STORAGE_VOLUME"):
        actual = c.run(["docker", "volume", "inspect", "--format", "{{.Name}}", c.v[key]]).strip()
        require(actual == c.v[key], "No existe el volumen externo esperado")
    # Resolve the existing postgres service; do not create/recreate it silently.
    pg = c.dc("ps", "-q", "postgres").strip()
    users = c.run(["docker", "ps", "--filter", "volume=" + c.v["POSTGRES_VOLUME"], "--format", "{{.ID}}"]).split()
    require(all(pg and (pg.startswith(item) or item.startswith(pg)) for item in users),
            "Otro contenedor usa el volumen PostgreSQL. Inspeccionar/adoptar el proyecto existente; nunca iniciar un segundo PostgreSQL sobre esos datos")
    if pg:
        inspect = json.loads(c.run(["docker", "inspect", pg]))[0]
        require(inspect["State"].get("Health", {}).get("Status") == "healthy", "PostgreSQL existente no saludable")
        mounts = {item.get("Destination"): item.get("Name") for item in inspect["Mounts"]}
        require(mounts.get("/var/lib/postgresql/data") == c.v["POSTGRES_VOLUME"], "PostgreSQL usa otro volumen")
    return bool(pg)


def postgres_ready(c):
    require(c.dc("ps", "-q", "postgres").strip(), "PostgreSQL no está iniciado. Tras confirmar volúmenes: docker compose up -d postgres")
    require(c.sql("SELECT 1;") == "1", "PostgreSQL no disponible")


@contextmanager
def operation_lock(c):
    path = Path(c.v["BACKUP_ROOT"])
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path / ".operation.lock").open("a+b") as stream:
        if os.name == "posix":
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            stream.write(b"0"); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(stream, fcntl.LOCK_UN)
            else:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def raw_current(c):
    if c.sql("SELECT to_regclass('public.alembic_version') IS NOT NULL;") != "t":
        return []
    return c.sql("SELECT version_num FROM alembic_version ORDER BY version_num;").split()


def graph_plan(graph, current, target):
    require(graph["heads"] == [target], "La imagen no tiene la única head esperada")
    parents = graph["parents"]
    def ancestors(revision):
        require(revision in parents, "Revisión desconocida: " + revision + ". Necesita reconciliación; no se migró.")
        found = {revision}
        for parent in parents[revision]:
            found.update(ancestors(parent))
        return found
    all_revisions = ancestors(target)
    applied = set()
    for revision in current:
        require(revision in all_revisions, "Base fuera de la cadena aprobada")
        applied.update(ancestors(revision))
    return sorted(all_revisions - applied)


def plan(c):
    postgres_ready(c)
    current = raw_current(c)
    if not current:
        tables = int(c.sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name <> 'alembic_version';"))
        require(tables == 0, "Hay tablas sin revisión Alembic; requiere reconciliación")
    # Read-only Alembic current/heads; never stamp/downgrade.
    print(c.dc("run", "--rm", "--no-deps", "backend", "alembic", "current"))
    print(c.dc("run", "--rm", "--no-deps", "backend", "alembic", "heads"))
    code = "import json; from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print(json.dumps({'heads':s.get_heads(),'parents':{r.revision:list(r._normalized_down_revisions) for r in s.walk_revisions()}}))"
    graph = json.loads(c.dc("run", "--rm", "--no-deps", "backend", "python", "-B", "-c", code))
    pending = graph_plan(graph, current, c.m["alembic_head"])
    print(json.dumps({"current": current, "target": c.m["alembic_head"], "pending": pending}))
    return current, pending


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def quiesce(c):
    c.dc("stop", "web", "backend", timeout=180)
    sessions = c.sql("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend';")
    require(sessions == "0", "Existen escritores externos. Dejarlos detenidos antes del respaldo/migración")


def backup(c):
    # Deliberately no Alembic validation here: unknown revisions must still back up.
    postgres_ready(c)
    quiesce(c)
    root = Path(c.v["BACKUP_ROOT"])
    require(shutil.disk_usage(root).free >= int(c.v.get("MIN_FREE_GB", "5")) * 1024**3, "Espacio libre insuficiente para iniciar backup")
    dest = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=root))
    os.chmod(dest, 0o700)
    try:
        current = raw_current(c)
        with (dest / "database.dump").open("wb") as f:
            c.dc("exec", "-T", "postgres", "sh", "-ec",
                 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                 output=f, timeout=7200, label="Backup PostgreSQL")
        with (dest / "database.dump").open("rb") as f:
            c.dc("exec", "-T", "postgres", "pg_restore", "--list", input_stream=f, output=subprocess.DEVNULL, timeout=300)
        code = "import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz',dereference=False); t.add('/app/storage',arcname='.'); t.close()"
        with (dest / "storage.tar.gz").open("wb") as f:
            c.dc("run", "--rm", "--no-deps", "backend", "python", "-B", "-c", code,
                 output=f, timeout=7200, label="Backup storage")
        with tarfile.open(dest / "storage.tar.gz") as archive:
            for item in archive:
                if item.isfile():
                    with archive.extractfile(item) as member:
                        while member.read(1024 * 1024):
                            pass
        data = {"status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                "release": c.m["release"], "current": current,
                "database": c.v["POSTGRES_DB"], "postgres_volume": c.v["POSTGRES_VOLUME"],
                "storage_volume": c.v["STORAGE_VOLUME"],
                "files": {name: hash_file(dest / name) for name in ("database.dump", "storage.tar.gz")}}
        (dest / "backup.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("Backup completo: " + str(dest))
        return dest
    except Exception:
        print("Backup INCOMPLETO: " + str(dest) + ". No autoriza migración.", file=sys.stderr)
        raise


def verify_backup(c, path, current):
    path = Path(path).resolve()
    require(Path(c.v["BACKUP_ROOT"]).resolve() in path.parents, "Backup fuera de BACKUP_ROOT")
    data = json.loads((path / "backup.json").read_text(encoding="utf-8"))
    require(data["status"] == "complete" and data["current"] == current, "Backup incompleto o de otra revisión")
    require(data["database"] == c.v["POSTGRES_DB"] and data["postgres_volume"] == c.v["POSTGRES_VOLUME"] and data["storage_volume"] == c.v["STORAGE_VOLUME"], "Backup de otra base/storage")
    for name in ("database.dump", "storage.tar.gz"):
        require(hash_file(path / name) == data["files"][name], "Backup alterado: " + name)


def migrate(c, backup_path):
    current, pending = plan(c)
    if pending:
        require(backup_path, "Migración requiere un backup completo")
        verify_backup(c, backup_path, current)
        quiesce(c)
        c.dc("run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head", timeout=3600, label="Migración")
        require(raw_current(c) == [c.m["alembic_head"]], "La migración no alcanzó la head esperada")
    return pending


def health(c):
    code = "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/database',timeout=5)); raise SystemExit(0 if d.get('status')=='ok' and d.get('database')=='connected' else 1)"
    c.dc("exec", "-T", "backend", "python", "-B", "-c", code)
    proxy = json.loads(c.dc("exec", "-T", "web", "curl", "--fail", "--silent", "--max-time", "5", "http://127.0.0.1:8080/api/health/database"))
    require(proxy.get("status") == "ok" and proxy.get("database") == "connected", "Health por proxy no conectado a DB")
    print("Health backend/web/PostgreSQL: OK")


def deploy(c, allow_migration=False):
    require(c.m["status"] == "validated", "Falta validación local del runtime")
    require(not c.run(["git", "status", "--porcelain"]).strip(), "Deploy requiere checkout limpio")
    preflight(c)
    postgres_ready(c)
    c.dc("build", timeout=3600, label="Build Docker local")
    saved = backup(c)
    _, pending = plan(c)
    require(not pending or allow_migration, "Se requiere autorización explícita --migrate. Backup conservado; aplicación detenida")
    if pending:
        migrate(c, saved)
    c.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "backend", "web", timeout=240)
    health(c)


def rollback(c, ref):
    require(re.fullmatch(r"[0-9a-f]{40}", ref or ""), "Indicar SHA deploy anterior aprobado, completo")
    require(not c.run(["git", "status", "--porcelain"]).strip(), "Rollback requiere checkout limpio")
    target = json.loads(c.run(["git", "show", ref + ":release.json"]))
    postgres_ready(c)
    require(raw_current(c) == [target["alembic_head"]], "Schema diferente al release anterior: restauración/compatibilidad manual aprobada; no se cambia Git ni DB")
    backup(c)
    c.run(["git", "checkout", "--detach", ref], label="Checkout rollback aprobado")
    c.dc("build", timeout=3600)
    c.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "backend", "web", timeout=240)
    health(c)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "backup", "migrate", "deploy", "healthcheck", "rollback"))
    parser.add_argument("--migrate", action="store_true", help="Autoriza upgrade head durante deploy")
    parser.add_argument("--apply", action="store_true", help="Autoriza migrate por separado")
    parser.add_argument("--backup", type=Path, help="Backup completo para migrate --apply")
    parser.add_argument("--ref", help="SHA completo del deploy anterior aprobado")
    args = parser.parse_args(argv)
    c = Context()
    os.umask(0o077)
    if args.command == "preflight":
        preflight(c)
        print("Preflight Docker/env/volúmenes: OK")
    elif args.command == "healthcheck":
        health(c)
    elif args.command == "migrate" and not args.apply:
        plan(c)
    else:
        with operation_lock(c):
            if args.command == "backup":
                preflight(c); backup(c)
            elif args.command == "deploy":
                deploy(c, args.migrate)
            elif args.command == "migrate":
                preflight(c); migrate(c, args.backup)
            else:
                preflight(c); rollback(c, args.ref)


if __name__ == "__main__":
    try:
        main()
    except (Refusal, OSError, ValueError) as exc:
        print("DETENIDO: " + str(exc), file=sys.stderr)
        sys.exit(1)
