#!/usr/bin/env python3
"""Operación de releases en Debian. Sin builds ni restauraciones automáticas.
Los subprocess usan argumentos explícitos, errores sanitizados y tiempo máximo.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "correspondencia-deploy"
BACKEND = "ghcr.io/andyguzmanescalera/correspondencia-sipe-sipe-backend"
WEB = "ghcr.io/andyguzmanescalera/correspondencia-sipe-sipe-web"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REVISION = re.compile(r"[a-zA-Z0-9_]{1,64}")
SHA = re.compile(r"[0-9a-f]{40}")
GIB = 1024**3

class Refusal(Exception):
    pass

def require(ok, message):
    if not ok:
        raise Refusal(message)

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refusal("JSON ausente o inválido: " + Path(path).name) from None

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(date.tzinfo is not None, "Fecha sin zona horaria")
        return date
    except (ValueError, AttributeError, TypeError):
        raise Refusal("Fecha inválida") from None

def revision_list(value):
    require(isinstance(value, list) and all(isinstance(x, str) and REVISION.fullmatch(x) for x in value), "Lista de revisiones inválida")
    require(len(value) == len(set(value)), "Lista de revisiones duplicadas")
    return value

def manifest(root=ROOT, ready=True):
    m = read_json(root / "release.json")
    require(isinstance(m, dict) and m.get("format_version") == 1, "Formato de manifiesto no soportado")
    for key in ("release", "source_sha", "deploy_sha", "backend_image", "backend_digest", "web_image", "web_digest", "platform", "validated_at"):
        require(isinstance(m.get(key), str), "Campo textual de manifiesto inválido: " + key)
    require(m["backend_image"] == BACKEND and m["web_image"] == WEB, "Registro de imágenes no aprobado")
    require(m.get("status") in {"draft", "validated"}, "Estado de release inválido")
    for key in ("backend_digest", "web_digest"):
        require(not m[key] or DIGEST.fullmatch(m[key]), "Digest inválido")
    require(not m["deploy_sha"] or SHA.fullmatch(m["deploy_sha"]), "deploy_sha inválido")
    require(m.get("rollback_compatible") is None or type(m["rollback_compatible"]) is bool, "Compatibilidad rollback inválida")
    require(m.get("rollback_to") is None or isinstance(m["rollback_to"], str), "rollback_to inválido")
    db = m.get("database")
    require(isinstance(db, dict) and isinstance(db.get("target_head"), str) and REVISION.fullmatch(db["target_head"]), "Destino Alembic inválido")
    revision_list(db.get("allowed_current_revisions"))
    revision_list(db.get("approved_pending_revisions"))
    require(type(db.get("plan_approved")) is bool, "Aprobación del plan inválida")
    require(db.get("migration_required") is None or type(db["migration_required"]) is bool, "migration_required inválido")
    compatibility = m.get("rollback_schema_compatibility")
    require(isinstance(compatibility, dict) and type(compatibility.get("approved")) is bool, "Matriz rollback inválida")
    revision_list(compatibility.get("current_revisions"))
    require(isinstance(compatibility.get("target_release"), str), "Destino de compatibilidad inválido")
    if ready:
        require(m["status"] == "validated", "Release draft: operación bloqueada")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", m["release"]), "Identificador pendiente")
        require(SHA.fullmatch(m["source_sha"]), "SHA desarrollo pendiente")
        require(all(DIGEST.fullmatch(m[k]) for k in ("backend_digest", "web_digest")), "Digests reales pendientes")
        require(m["platform"] in {"linux/amd64", "linux/arm64"}, "Plataforma no soportada")
        require(timestamp(m["validated_at"]) <= datetime.now(timezone.utc), "Validación fechada en el futuro")
    return m

PRIVATE_DIRS = {"storage", "staging", "data", "postgres_data", "backups", "backup", "logs", "secrets", "__pycache__", ".pytest_cache"}
PRIVATE_PATTERNS = (".env", ".env.*", "runtime.env", ".envrc", "*.dump", "*.sql", "*.tar", "*.tar.gz", "*.tgz", "*.zip", "*.log", "*.pid", "*.pyc", "*.pyo", "*.pem", "*.key", "credentials*.json", "cloudflared*")
FORBIDDEN_DIRS = {"website", "rrhh", "node_modules", ".venv", "build", "dist"}

def private_path(rel):
    if rel.as_posix() == ".env.example":
        return False
    return bool(set(rel.parts) & PRIVATE_DIRS) or any(fnmatch.fnmatch(rel.name, pat) for pat in PRIVATE_PATTERNS)

def versionable_files(root):
    found = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        base = Path(base)
        for name in list(dirs):
            rel = (base/name).relative_to(root)
            require(name not in FORBIDDEN_DIRS, "Directorio ajeno al runtime dentro del checkout")
            if name == ".git" or private_path(rel):
                dirs.remove(name)
            else:
                require(not (base/name).is_symlink(), "Directorio versionable no puede ser symlink")
        for name in files:
            rel = (base/name).relative_to(root)
            if rel.as_posix() not in {"checksums.sha256", ".git"} and not private_path(rel):
                found.add(rel.as_posix())
    return found

def verify_checksums(root=ROOT):
    try:
        rows = (root/"checksums.sha256").read_text(encoding="utf-8").splitlines()
    except OSError:
        raise Refusal("Falta checksums.sha256") from None
    expected = versionable_files(root)
    found = set()
    for row in rows:
        require(re.fullmatch(r"[0-9a-f]{64}  [A-Za-z0-9_./-]+", row), "Entrada de checksum inválida")
        digest, name = row.split("  ", 1)
        rel = Path(name)
        require(not rel.is_absolute() and ".." not in rel.parts and not private_path(rel) and name not in found, "Ruta no versionable en checksums")
        target = root/rel
        require(target.is_file() and not target.is_symlink() and sha(target) == digest, "Archivo operativo ausente o alterado: " + name)
        found.add(name)
    require(found == expected, "Archivo versionable inesperado o inventario incompleto")
    return found

def dotenv(path):
    require(path.is_file() and not path.is_symlink(), "Falta runtime.env regular")
    require(os.name != "posix" or path.stat().st_mode & 0o077 == 0, "runtime.env requiere 600/400")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key), "Formato runtime.env inválido")
        require(key not in result and value == value.strip() and not any(c in value for c in "\x00$`\"'"), "Valores literales sin comillas, duplicados ni expansión")
        result[key] = value
    return result

def validate_env(v, root=ROOT):
    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "JWT_ACCESS_SECRET", "PUBLIC_TRACKING_HMAC_SECRET",
                "POSTGRES_IMAGE", "POSTGRES_VOLUME", "STORAGE_VOLUME", "BACKUP_ROOT", "STATE_DIR", "JWT_ISSUER", "JWT_AUDIENCE",
                "EXPECTED_DOCKER_CONTEXT", "EXPECTED_DOCKER_DAEMON_ID"):
        require(bool(v.get(key)), "Variable obligatoria vacía: " + key)
    require(v.get("APP_ENV") == "production", "Compose requiere production")
    require(v.get("COOKIE_SECURE") == "true" and v.get("COOKIE_PATH") == "/auth", "Cookie segura/path incompatible")
    require(v.get("COOKIE_NAME") == "refresh_token" and v.get("COOKIE_SAMESITE") == "lax", "Contrato cookie incompatible")
    require(v.get("POSTGRES_HOST") == "postgres" and v.get("POSTGRES_PORT") == "5432" and not v.get("DATABASE_URL"), "DB debe usar POSTGRES_* internos")
    require(v.get("STORAGE_ROOT") == "/app/storage", "Storage incompatible")
    require(not v.get("CORS_ALLOW_ORIGINS") and not v.get("CORS_ALLOW_ORIGIN_REGEX") and v.get("CORS_ALLOW_CREDENTIALS") == "true", "Contrato same-origin incompatible")
    require(len(v["JWT_ACCESS_SECRET"]) >= 32 and len(v["PUBLIC_TRACKING_HMAC_SECRET"]) >= 32, "Secretos cortos")
    require(len({v["POSTGRES_PASSWORD"],v["JWT_ACCESS_SECRET"],v["PUBLIC_TRACKING_HMAC_SECRET"]}) == 3, "Secretos deben ser independientes")
    require(re.fullmatch(r"[A-Za-z0-9_-]{24,}", v["POSTGRES_PASSWORD"]), "DB password URL-safe de mínimo 24 caracteres requerido; no rotar sin autorización")
    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", v[key]), "Identificador DB inválido")
    require(re.fullmatch(r"(?:docker.io/library/)?postgres:16(?:[.-][a-z0-9.-]+)?@sha256:[0-9a-f]{64}", v["POSTGRES_IMAGE"]), "PostgreSQL 16 fijado por digest requerido")
    require(v["POSTGRES_VOLUME"] != v["STORAGE_VOLUME"], "Volúmenes deben ser distintos")
    for key in ("POSTGRES_VOLUME", "STORAGE_VOLUME"):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", v[key]), "Nombre de volumen inválido")
    bounds={"ACCESS_TOKEN_TTL_MINUTES":(1,1440),"REFRESH_TOKEN_TTL_DAYS":(1,365),"MAX_ATTACHMENT_SIZE_MB":(1,1024),"MIN_FREE_GB":(1,100000),
            "COMMAND_TIMEOUT_SECONDS":(30,600),"PULL_TIMEOUT_SECONDS":(300,14400),"BACKUP_TIMEOUT_SECONDS":(300,86400),"MIGRATION_TIMEOUT_SECONDS":(300,86400),
            "APP_UPDATE_TIMEOUT_SECONDS":(180,1800),"UNESTIMATED_BACKUP_MIN_GB":(100,100000)}
    for key,(lo,hi) in bounds.items():
        require(v.get(key,"").isdigit() and lo <= int(v[key]) <= hi, "Presupuesto inválido: " + key)
    require(bool(v.get("ATTACHMENT_ALLOWED_MIME_TYPES")), "Allowlist MIME vacía")
    for key in ("BACKUP_ROOT","STATE_DIR"):
        p=Path(v[key]).resolve()
        require(Path(v[key]).is_absolute() and p != root and root not in p.parents and p not in root.parents, "Directorio operativo debe estar fuera del checkout")
    require(not v.get("EXPECTED_DEPLOY_SHA") or SHA.fullmatch(v["EXPECTED_DEPLOY_SHA"]), "EXPECTED_DEPLOY_SHA inválido")

def execute(args, env, output=None, input_stream=None, timeout=60, label="Comando"):
    try:
        process=subprocess.Popen(args, env=env, stdout=subprocess.PIPE if output is None else output,
                                 stdin=input_stream, stderr=subprocess.PIPE, start_new_session=os.name=="posix")
    except OSError:
        raise Refusal(label+": herramienta no disponible") from None
    try:
        stdout,_=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Debian: terminar el grupo incluye el plugin Compose, no solo docker CLI.
        if os.name=="posix":
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        else:process.terminate()
        try:process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name=="posix":
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            else:process.kill()
            process.communicate(timeout=10)
        raise Refusal(label+": timeout; proceso terminado, operación no completada") from None
    require(process.returncode==0,label+": salida no cero; revisar diagnóstico local sin divulgar secretos")
    return stdout.decode("utf-8") if output is None else ""

class Context:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve();self.m=manifest(self.root);verify_checksums(self.root)
        self.envfile=Path(os.environ.get("CORRESPONDENCIA_ENV_FILE", self.root/".env")).absolute()
        self.v=dotenv(self.envfile);validate_env(self.v,self.root)
        self.env=dict(os.environ);self.env.update(self.v)
        # Nunca aceptar una selección de daemon oculta que eluda el contexto aprobado.
        require(not any(os.environ.get(k) for k in ("DOCKER_HOST","DOCKER_TLS_VERIFY","DOCKER_CERT_PATH")), "Eliminar overrides Docker y seleccionar explícitamente contexto autorizado")
        self.env.update(RUNTIME_ENV_FILE=str(self.envfile),BACKEND_IMAGE=self.m["backend_image"]+"@"+self.m["backend_digest"],
                        WEB_IMAGE=self.m["web_image"]+"@"+self.m["web_digest"],RELEASE_PLATFORM=self.m["platform"],RELEASE_ID=self.m["release"])
        self.docker_base=["docker","--context",self.v["EXPECTED_DOCKER_CONTEXT"]]
        self.base=self.docker_base+["compose","--project-name",PROJECT,"--env-file",str(self.envfile),"--project-directory",str(self.root),"-f",str(self.root/"docker-compose.prod.yml")]
        self.deploy_sha="";self.allow_unestimated=False;self.approved_sha=None
    def timeout(self,key="COMMAND_TIMEOUT_SECONDS"):
        return int(self.v[key])
    def run(self,args,output=None,input_stream=None,timeout=None,label="Comando operativo"):
        return execute(args,self.env,output,input_stream,self.timeout() if timeout is None else timeout,label)
    def docker(self,*args,**kwargs):
        return self.run(self.docker_base+list(args),label="Docker "+args[0],**kwargs)
    def dc(self,*args,**kwargs):
        return self.run(self.base+list(args),label="Compose "+args[0],**kwargs)
    def db(self,sql):
        return self.dc("exec","-T","postgres","sh","-c",
            'PGOPTIONS="-c statement_timeout=30000 -c lock_timeout=5000" PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"',"sh",sql).strip()
    def ephemeral(self,image,command,network="none",mounts=(),output=None,timeout=None,environment=False):
        name="corr-op-"+uuid.uuid4().hex
        args=["run","--rm","--pull=never","--name",name,"--network",network,"--read-only","--user","10001:10001",
              "--cap-drop=ALL","--security-opt","no-new-privileges:true","--tmpfs","/tmp:rw,nosuid,nodev,size=128m"]
        if environment:
            args += ["--env-file",str(self.envfile),"-e","APP_VERSION="+self.m["release"],"-e","POSTGRES_HOST=postgres","-e","POSTGRES_PORT=5432","-e","STORAGE_ROOT=/app/storage"]
        for mount in mounts:args += ["--mount",mount]
        args += ["--entrypoint",command[0],image]+command[1:]
        try:
            return self.docker(*args,output=output,timeout=timeout)
        finally:
            # Solo nuestro nombre UUID: nunca limpiar contenedores de aplicación.
            existing=self.docker("ps","-aq","--filter","name=^/"+name+"$",timeout=30).strip()
            if existing:
                self.docker("rm","-f",name,timeout=60)

def git_state(c):
    def git(*args):return c.run(["git","-C",str(c.root)]+list(args),label="Git").strip()
    require(Path(git("rev-parse","--show-toplevel")).resolve() == c.root,"Checkout Git incorrecto")
    require(not git("status","--porcelain=v1","--untracked-files=all"),"Checkout Git sucio; no desplegar")
    tracked=git("ls-files","-z").split("\0")
    require(not any(private_path(Path(x)) for x in tracked if x),"Hay archivos privados versionados")
    head=git("rev-parse","HEAD");require(SHA.fullmatch(head),"HEAD no válido")
    expected=c.approved_sha or c.m["deploy_sha"] or c.v.get("EXPECTED_DEPLOY_SHA")
    require(not expected or head == expected,"HEAD distinto del aprobado")
    ref="" if c.approved_sha else c.v.get("EXPECTED_DEPLOY_REF","")
    require(not ref or git("rev-parse",ref)==head,"La referencia esperada no apunta al checkout")
    c.deploy_sha=head
    return head

def platform_name(os_name,arch):
    return os_name+"/"+{"x86_64":"amd64","aarch64":"arm64"}.get(arch,arch)

def validate_daemon(info,context,expected_context,expected_id,platform):
    require(context==expected_context,"Contexto Docker no coincide; no se cambia automáticamente")
    require(info.get("ID")==expected_id,"Daemon Docker distinto del autorizado")
    require(platform_name(info.get("OSType",""),info.get("Architecture",""))==platform,"Arquitectura daemon incompatible")

def port_free():
    import errno
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        try:sock.bind(("127.0.0.1",8080));return True
        except OSError as e:
            if e.errno==errno.EADDRINUSE:return False
            raise Refusal("No se pudo comprobar puerto 8080") from None

def validate_port(containers,free):
    matches=[]
    for info in containers:
        for port,bindings in info.get("NetworkSettings",{}).get("Ports",{}).items():
            for binding in bindings or []:
                if binding.get("HostPort")=="8080":
                    labels=info.get("Config",{}).get("Labels",{}) or {}
                    require(port=="8080/tcp" and binding.get("HostIp")=="127.0.0.1" and labels.get("com.docker.compose.project")==PROJECT and labels.get("com.docker.compose.service")=="web","Puerto 8080 publicado por contenedor ajeno")
                    matches.append(info["Id"])
    require(len(set(matches)) <= 1,"Múltiples propietarios del puerto")
    require(bool(matches) or free,"Puerto 8080 ocupado por proceso ajeno")

def preflight_initial(c,application=True):
    require(sys.platform.startswith("linux"),"Operaciones reales requieren Linux; --static no accede a servicios")
    git_state(c)
    context=c.run(["docker","context","show"],label="Contexto Docker").strip()
    info=json.loads(c.docker("info","--format","{{json .}}"))
    validate_daemon(info,context,c.v["EXPECTED_DOCKER_CONTEXT"],c.v["EXPECTED_DOCKER_DAEMON_ID"],c.m["platform"])
    version=c.docker("compose","version","--short").strip().lstrip("v")
    require(tuple(int(x) for x in version.split('-')[0].split('.')[:2]) >= (2,30),"Compose >=2.30 requerido")
    c.dc("config","--quiet")
    for key in ("POSTGRES_VOLUME","STORAGE_VOLUME"):c.docker("volume","inspect",c.v[key])
    for key in ("BACKUP_ROOT","STATE_DIR"):
        p=Path(c.v[key]);require(p.is_dir() and os.access(p,os.W_OK|os.X_OK),"Preparar directorios operativos restringidos")
        require(shutil.disk_usage(p).free >= int(c.v["MIN_FREE_GB"])*GIB,"Reserva básica de espacio insuficiente")
    if application:
        ids=c.docker("ps","-q","--filter","publish=8080").split()
        containers=[json.loads(c.docker("inspect",cid))[0] for cid in ids]
        validate_port(containers,port_free())
    print("Preflight inicial OK: Git, daemon, arquitectura, puerto y persistencia; sin exigir imágenes locales")

GRAPH = r"""
import json,sys
try:
 from alembic.config import Config
 from alembic.script import ScriptDirectory
 script=ScriptDirectory.from_config(Config('/app/alembic.ini'))
 parents={}
 for node in script.walk_revisions():
  values=[]
  for entry in (node.down_revision,node.dependencies):
   for parent in ((entry,) if isinstance(entry,str) else (entry or ())):
    values.append(script.get_revision(parent).revision)
  parents[node.revision]=sorted(set(values))
 print(json.dumps(dict(heads=script.get_heads(),parents=parents)))
except Exception:
 print('Inspección de grafo falló',file=sys.stderr);sys.exit(1)
"""
BACKEND_CHECK = r"""
import os,sys
from pathlib import Path
try:
 if os.getuid()!=10001 or os.getgid()!=10001:raise ValueError()
 for p in ['/app/alembic.ini','/app/alembic/env.py','/app/app/main.py','/app/app/assets/branding/sipe_sipe_crest.png']:
  if not Path(p).is_file() or not os.access(p,os.R_OK):raise ValueError()
 if not os.access('/tmp',os.W_OK):raise ValueError()
 import alembic,uvicorn,sqlalchemy,psycopg
 print('Backend runtime OK')
except Exception:
 print('Imagen backend incompatible',file=sys.stderr);sys.exit(1)
"""
STORAGE_CHECK = r"""
import json,os,stat,sys
try:
 if not os.access('/data',os.R_OK|os.X_OK):raise ValueError()
 total=0
 for root,dirs,files in os.walk('/data',followlinks=False,onerror=lambda e:(_ for _ in ()).throw(e)):
  for name in files:
   p=os.path.join(root,name);s=os.lstat(p)
   if stat.S_ISREG(s.st_mode):
    if not os.access(p,os.R_OK):raise ValueError()
    total+=s.st_size
 print(json.dumps({'bytes':total}))
except Exception:
 print('No se pudo medir/leer storage completo',file=sys.stderr);sys.exit(1)
"""

WEB_CHECK = r"""
set -eu
test "$(id -u)" = 10001
test "$(id -g)" = 10001
test -r /usr/local/apache2/htdocs/index.html
test -r /usr/local/apache2/htdocs/main.dart.js
test -r /usr/local/apache2/htdocs/flutter_bootstrap.js || test -r /usr/local/apache2/htdocs/flutter.js
test -w /tmp
httpd -t
includes="$(httpd -t -D DUMP_INCLUDES 2>&1)"
case "$includes" in *'/conf/extra/correspondencia.conf'*) ;; *) exit 1 ;; esac
modules="$(httpd -M 2>/dev/null)"
for module in rewrite proxy proxy_http headers remoteip; do
    case "$modules" in *"${module}_module"*) ;; *) exit 1 ;; esac
done
"""

STORAGE_PERMISSIONS = "import os,sys; sys.exit(0 if os.access('/app/storage',os.R_OK|os.W_OK|os.X_OK) else 1)"

def inspect_image(c,ref):
    info=json.loads(c.docker("image","inspect",ref))[0]
    require(platform_name(info.get("Os",""),info.get("Architecture",""))==c.m["platform"],"Plataforma de imagen incompatible")
    require(any(x.rsplit('@',1)[-1]==ref.rsplit('@',1)[-1] for x in info.get("RepoDigests",[])),"Digest no acreditado por imagen local")
    return info

def image_graph(c):
    return json.loads(c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",GRAPH],environment=True))

def preflight_images(c,backend_only=False):
    inspect_image(c,c.env["BACKEND_IMAGE"])
    c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",BACKEND_CHECK])
    if not backend_only:
        # Se comprueban permisos del UID real; no se crea ni modifica ningún archivo.
        c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",STORAGE_PERMISSIONS],
                    mounts=["type=volume,source="+c.v["STORAGE_VOLUME"]+",target=/app/storage"])
        graph=image_graph(c)
        require(graph.get("heads")==[c.m["database"]["target_head"]],"Head de imagen distinta del manifiesto")
        inspect_image(c,c.env["WEB_IMAGE"])
        mount="type=bind,source="+str(c.root/"apache/correspondencia.conf")+",target=/usr/local/apache2/conf/extra/correspondencia.conf,readonly"
        c.ephemeral(c.env["WEB_IMAGE"],["sh","-c",WEB_CHECK],mounts=[mount])
    print("Preflight imágenes OK (rootfs read-only, UID/GID, runtime y configuración)")

def raw_current(c):
    if c.db("SELECT to_regclass('alembic_version') IS NOT NULL;") != "t":return []
    return sorted(c.db("SELECT version_num FROM alembic_version ORDER BY version_num;").splitlines())

def actual_plan(graph,current,target):
    heads=graph.get("heads");parents=graph.get("parents")
    require(heads==[target] and isinstance(parents,dict) and target in parents,"Grafo no tiene la única head aprobada")
    require(current and len(current)==len(set(current)),"Base sin revisión o revisiones duplicadas: inspección inicial aparte")
    visiting=set()
    def ancestors(nodes):
        result=set()
        def visit(node):
            require(node in parents,"Revisión DB/padre desconocido")
            require(node not in visiting,"Grafo con ciclo")
            if node in result:return
            visiting.add(node)
            for parent in parents[node]:visit(parent)
            visiting.remove(node);result.add(node)
        for node in nodes:visit(node)
        return result
    desired=ancestors([target]);applied=ancestors(current)
    require(applied <= desired,"Revisión fuera de la ascendencia destino")
    require(all(not (ancestors([a])-{a}) & set(current) for a in current),"Frontier DB inconsistente (ancestro y descendiente simultáneos)")
    pending=sorted(desired-applied)
    return dict(target_head=target,allowed_current_revisions=sorted(current),approved_pending_revisions=pending,migration_required=bool(pending))

def approve_plan(m,plan):
    print("Plan Alembic observado: "+json.dumps(plan,sort_keys=True))
    approved=m["database"]
    require(approved["plan_approved"] is True,"Falta aprobación explícita del plan de migración")
    for field in ("target_head","allowed_current_revisions","approved_pending_revisions","migration_required"):
        expected=approved[field]
        if isinstance(expected,list):expected=sorted(expected)
        require(expected==plan[field],"Plan difiere del release: "+field)
    return plan

def preflight_migration(c):
    graph=image_graph(c);current=raw_current(c)
    revision_list(current)
    print("Alembic current: "+json.dumps(current)+"; heads imagen: "+json.dumps(graph.get("heads")))
    plan=actual_plan(graph,current,c.m["database"]["target_head"])
    return approve_plan(c.m,plan)

def postgres_ready(c):
    cid=c.dc("ps","-q","postgres").strip();require(cid and '\n' not in cid,"PostgreSQL del proyecto no está activo")
    info=json.loads(c.docker("inspect",cid))[0]
    labels=info.get("Config",{}).get("Labels",{}) or {}
    require(labels.get("com.docker.compose.project")==PROJECT and labels.get("com.docker.compose.service")=="postgres","PostgreSQL pertenece a otro proyecto")
    require(info.get("State",{}).get("Health",{}).get("Status")=="healthy","PostgreSQL no está healthy")
    require(any(x.get("Name")==c.v["POSTGRES_VOLUME"] and x.get("Destination")=="/var/lib/postgresql/data" for x in info["Mounts"]),"Volumen DB incorrecto")
    require(c.db("SELECT 1;")=="1" and c.db("SELECT current_database();")==c.v["POSTGRES_DB"] and c.db("SELECT current_user;")==c.v["POSTGRES_USER"],"DB/usuario efectivos incorrectos")
    version=c.db("SHOW server_version_num;")
    require(version.isdigit() and int(version)//10000==16,"PostgreSQL major distinto al contrato")
    return cid

def capacity_required(db_bytes,storage_bytes,reserve):
    require(db_bytes>=0 and storage_bytes>=0,"Estimación negativa")
    return 2*(db_bytes+storage_bytes)+reserve

def preflight_backup(c):
    postgres_ready(c);no_writers(c)
    c.dc("exec","-T","postgres","sh","-c","command -v pg_dump >/dev/null && command -v pg_restore >/dev/null && command -v timeout >/dev/null")
    available=shutil.disk_usage(c.v["BACKUP_ROOT"]).free;reserve=int(c.v["MIN_FREE_GB"])*GIB
    try:
        db=int(c.db("SELECT pg_database_size(current_database());"))
        output=c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",STORAGE_CHECK],mounts=["type=volume,source="+c.v["STORAGE_VOLUME"]+",target=/data,readonly"],timeout=300)
        storage=int(json.loads(output)["bytes"])
        required=capacity_required(db,storage,reserve)
        require(available>=required,"Espacio insuficiente para estimación DB+storage, overhead y reserva")
        result=dict(estimated=True,database_bytes=db,storage_bytes=storage,required_bytes=required,free_bytes=available)
    except (Refusal,ValueError,KeyError,TypeError) as error:
        if str(error).startswith("Espacio insuficiente"):raise
        require(c.allow_unestimated,"No se pudo estimar capacidad; requiere revisión o --allow-unestimated-backup")
        floor=max(int(c.v["UNESTIMATED_BACKUP_MIN_GB"])*GIB,reserve)
        require(available>=floor,"Reserva conservadora insuficiente para backup sin estimación")
        print("ADVERTENCIA: capacidad no estimada; el mínimo conservador NO garantiza que quepa el respaldo",file=sys.stderr)
        result=dict(estimated=False,required_bytes=floor,free_bytes=available)
    print("Capacidad backup: "+json.dumps(result,sort_keys=True))
    return result

@contextmanager
def lock(c):
    import fcntl
    path = Path(c.v["STATE_DIR"]) / "operation.lock"
    with path.open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal("Otra operación de release está en curso") from None
        yield

def no_writers(c):
    ids = c.docker("ps", "-q", "--filter", "volume=" + c.v["STORAGE_VOLUME"]).split()
    for cid in ids:
        info = json.loads(c.docker("inspect", cid))[0]
        require(not any(m.get("Name") == c.v["STORAGE_VOLUME"] and m.get("RW") for m in info["Mounts"]),
                "Hay un contenedor activo con storage RW; detener escritores, incluido deployment anterior")
    sessions = c.db("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend';")
    require(sessions == "0", "Hay otras sesiones de DB: no se garantiza backup/migración sin escritores")

def backup(c):
    estimate = preflight_backup(c)
    path = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") , dir=c.v["BACKUP_ROOT"]))
    os.chmod(path, 0o700)
    try:
        current = raw_current(c)
        with (path / "database.dump").open("wb") as stream:
            c.dc("exec", "-T", "postgres", "sh", "-c",
                 'PGPASSWORD="$POSTGRES_PASSWORD" exec timeout -s TERM -k 30 "$1" pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                 "sh", str(c.timeout("BACKUP_TIMEOUT_SECONDS") - 60), output=stream, timeout=c.timeout("BACKUP_TIMEOUT_SECONDS"))
        c.dc("exec", "-T", "postgres", "pg_restore", "--version")
        # Validar el índice leyendo el dump por stdin, sin restaurar.
        with (path / "database.dump").open("rb") as stream:
            c.dc("exec", "-T", "postgres", "timeout", "-s", "TERM", "-k", "10", "300", "pg_restore", "--list",
                 input_stream=stream, output=subprocess.DEVNULL, timeout=330)
        with (path / "storage.tar.gz").open("wb") as stream:
            c.ephemeral(c.env["BACKEND_IMAGE"], ["python", "-B", "-c",
                "import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz',dereference=False); t.add('/data',arcname='.'); t.close()"],
                mounts=["type=volume,source=" + c.v["STORAGE_VOLUME"] + ",target=/data,readonly"],
                output=stream, timeout=c.timeout("BACKUP_TIMEOUT_SECONDS"))
        import tarfile
        with tarfile.open(path / "storage.tar.gz") as archive:
            for item in archive:
                # Leer contenido completo detecta truncado/errores de gzip.
                if item.isfile():
                    with archive.extractfile(item) as member:
                        while member.read(1024 * 1024): pass
        no_writers(c)
        data = dict(status="complete", release=c.m["release"], source_sha=c.m["source_sha"],
                    release_manifest_sha256=sha(c.root / "release.json"), created_at=datetime.now(timezone.utc).isoformat(),
                    database=c.v["POSTGRES_DB"], postgres_volume=c.v["POSTGRES_VOLUME"], storage_volume=c.v["STORAGE_VOLUME"],
                    alembic_current=current, capacity_estimate=estimate, deploy_sha=c.deploy_sha, files={n:sha(path/n) for n in ("database.dump", "storage.tar.gz")})
        (path / "manifest.json").write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
        print("Backup completo: " + str(path))
        return path
    except Exception:
        print("Backup INCOMPLETO, no habilita migraciones: " + str(path), file=sys.stderr)
        raise

def verify_backup(c, path, current):
    require(path is not None, "Indique --backup con respaldo completo")
    path = Path(path).resolve()
    require(Path(c.v["BACKUP_ROOT"]).resolve() in path.parents, "Backup fuera del directorio aprobado")
    data = read_json(path / "manifest.json")
    require(data.get("status") == "complete" and data.get("release_manifest_sha256") == sha(c.root/"release.json"), "Backup no corresponde al release")
    age = (datetime.now(timezone.utc) - timestamp(data.get("created_at"))).total_seconds()
    require(0 <= age <= 86400, "Backup fuera de la ventana de 24 horas")
    require(sorted(data.get("alembic_current", [])) == sorted(current), "La revisión de esquema cambió desde el backup")
    require(data.get("database") == c.v["POSTGRES_DB"] and data.get("postgres_volume") == c.v["POSTGRES_VOLUME"] and data.get("storage_volume") == c.v["STORAGE_VOLUME"], "Backup de otra persistencia")
    for name in ("database.dump", "storage.tar.gz"):
        require((path/name).is_file() and not (path/name).is_symlink() and data.get("files",{}).get(name)==sha(path/name), "Backup incompleto/alterado")


def migrate(c,path):
    no_writers(c)
    plan=preflight_migration(c)
    verify_backup(c,path,plan["allowed_current_revisions"])
    if plan["migration_required"]:
        code="from alembic.config import Config; from alembic import command; command.upgrade(Config('/app/alembic.ini'),'head')"
        c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",code],network=PROJECT+"_data",environment=True,
                    mounts=["type=volume,source="+c.v["STORAGE_VOLUME"]+",target=/app/storage"],timeout=c.timeout("MIGRATION_TIMEOUT_SECONDS"))
    current=raw_current(c)
    require(current==[plan["target_head"]],"Migración no terminó en la head destino")
    print("Esquema en head aprobada")

HEALTH = r"""
import json,sys,urllib.request
try:
 base='http://web:8080'
 for path in ['/','/index.html','/correspondencia/login','/correspondencia/home','/correspondencia/consulta','/consulta-publica']:
  with urllib.request.urlopen(base+path,timeout=8) as r:
   body=r.read()
   if r.status!=200 or b'<html' not in body.lower() or 'no-store' not in r.headers.get('Cache-Control',''):raise ValueError()
 for path in ['/api/health','/api/health/database']:
  with urllib.request.urlopen(base+path,timeout=8) as r:
   body=json.load(r)
   if body.get('status')!='ok':raise ValueError()
   if path.endswith('/database') and body.get('database')!='connected':raise ValueError()
 req=urllib.request.Request(base+'/api/public/correspondence/track',data=b'{"route_sheet_number":"INVALID-SYNTHETIC-HEALTHCHECK"}',headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=8) as r:
  body=json.load(r)
  if body.get('found') is not False or 'no-store' not in r.headers.get('Cache-Control',''):raise ValueError()
 print('HTTP/SPA, proxy API, DB y tracking sintético OK; no acredita login/render de navegador')
except Exception:
 print('Healthcheck falló',file=sys.stderr);sys.exit(1)
"""

def health(c):
    print(c.ephemeral(c.env["BACKEND_IMAGE"],["python","-B","-c",HEALTH],network=PROJECT+"_edge",timeout=120).strip())

def update_app(c):
    cid=postgres_ready(c)
    for service in ("backend","web"):
        # Orden explícito; --no-deps también evita reconciliar postgres.
        c.dc("up","-d","--no-deps","--no-build","--pull","never","--wait","--wait-timeout","120",service,
             timeout=c.timeout("APP_UPDATE_TIMEOUT_SECONDS"))
        require(postgres_ready(c)==cid,"Identidad PostgreSQL cambió durante update: intervenir")

def save_active(c):
    path=Path(c.v["STATE_DIR"])/"active.json"
    previous=None
    if path.exists():
        old=read_json(path)
        previous={k:old.get(k) for k in ("release","deploy_sha","deploy_root","deployed_at")}
    data=dict(release=c.m,deploy_sha=c.deploy_sha,deploy_root=str(c.root),previous=previous,
              docker_context=c.v["EXPECTED_DOCKER_CONTEXT"],docker_daemon_id=c.v["EXPECTED_DOCKER_DAEMON_ID"],
              deployed_at=datetime.now(timezone.utc).isoformat())
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8');os.replace(temp,path)

def validate_rollback(c,target,current):
    require(c.m["rollback_compatible"] is True and c.m["rollback_to"]==target.m["release"],"Rollback hacia ese release no aprobado")
    equal=current==[target.m["database"]["target_head"]]
    matrix=c.m["rollback_schema_compatibility"]
    approved=matrix["approved"] is True and sorted(matrix["current_revisions"])==sorted(current) and matrix["target_release"]==target.m["release"]
    require(equal or approved,"Esquema incompatible con rollback; restore requiere autorización aparte")

def stop_app(c):
    c.dc("stop","web","backend",timeout=120)

def run_deploy(c):
    c.dc("pull","--policy","always","backend","web",timeout=c.timeout("PULL_TIMEOUT_SECONDS"))
    preflight_images(c)
    stop_app(c)
    try:
        path=backup(c)
        migrate(c,path)
        update_app(c)
        health(c);save_active(c)
        print("Release activo: "+c.m["release"])
    except Exception:
        try:stop_app(c)
        except Exception:raise Refusal("Fallo y no se pudo detener aplicación; mantener bloqueo externo e intervenir") from None
        raise

def run_rollback(c,path):
    require(path is not None,"Rollback requiere --target CHECKOUT_ANTERIOR")
    active=read_json(Path(c.v["STATE_DIR"])/"active.json")
    require(active.get("release")==c.m and active.get("deploy_sha")==c.deploy_sha,"Ejecutar desde el release/commit activo registrado")
    previous=active.get("previous")
    require(isinstance(previous,dict) and SHA.fullmatch(previous.get("deploy_sha", "")),"Release anterior sin procedencia registrada: intervención manual")
    target=Context(path)
    require(target.envfile.resolve()==c.envfile.resolve(),"Rollback debe preservar runtime.env")
    require(target.m==previous.get("release"),"Target no corresponde al release anterior registrado")
    target.approved_sha=previous["deploy_sha"]
    preflight_initial(target);preflight_images(target)
    validate_rollback(c,target,raw_current(c))
    stop_app(c)
    try:
        backup(c)
        update_app(target)
        health(target);save_active(target)
        print("Rollback de aplicación completado; sin upgrade/downgrade/restore")
    except Exception:
        try:stop_app(target)
        except Exception:raise Refusal("Rollback falló y no se pudo detener aplicación; mantener bloqueo externo") from None
        raise

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['preflight','backup','migrate','deploy','healthcheck','rollback'])
    parser.add_argument('--static',action='store_true',help='Solo plantilla/checksums, sin Docker')
    parser.add_argument('--phase',choices=['initial','images','backup','migration'],default='initial')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--pull-images',action='store_true')
    parser.add_argument('--writers-stopped',action='store_true',help='Confirmación de mantenimiento y bloqueo de escritores externos')
    parser.add_argument('--allow-unestimated-backup',action='store_true')
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--target',type=Path)
    args=parser.parse_args(argv);os.umask(0o077)
    if args.static:
        require(args.operation=='preflight' and not args.apply and not args.pull_images,'--static solo para preflight')
        manifest(ready=False);verify_checksums();print('Plantilla/checksums OK; sin validar imágenes ni infraestructura');return
    c=Context();c.allow_unestimated=args.allow_unestimated_backup
    if args.operation in {'backup','deploy','rollback'} and not args.pull_images:
        require(args.apply and args.writers_stopped,'Requiere --apply --writers-stopped')
    if args.operation=='migrate' and args.apply:
        require(args.writers_stopped and args.backup is not None,'Migrar requiere mantenimiento y backup explícitos')
    preflight_initial(c,application=args.operation!='backup' and not (args.operation=='preflight' and args.phase=='backup'))
    if args.pull_images:
        require(args.operation=='deploy' and not args.apply,'Descarga separada: deploy --pull-images')
        c.dc('pull','--policy','always','backend','web',timeout=c.timeout('PULL_TIMEOUT_SECONDS'));print('Imágenes descargadas; ningún servicio arrancado');return
    if args.operation=='preflight':
        if args.phase=='images':preflight_images(c)
        elif args.phase=='backup':
            require(args.writers_stopped,'Comprobar backup requiere mantenimiento')
            preflight_images(c,backend_only=True);preflight_backup(c)
        elif args.phase=='migration':preflight_images(c);preflight_migration(c)
        return
    if args.operation=='healthcheck':
        preflight_images(c);health(c);return
    if args.operation=='migrate' and not args.apply:
        preflight_images(c);preflight_migration(c);print('Solo inspección; upgrade requiere --apply --writers-stopped --backup RUTA');return
    with lock(c):
        # Verificar de nuevo procedencia bajo lock, antes de modificar servicios.
        git_state(c)
        if args.operation=='backup':
            preflight_images(c,backend_only=True);backup(c)
        elif args.operation=='migrate':
            preflight_images(c);migrate(c,args.backup)
        elif args.operation=='deploy':
            require(not args.backup,'Deploy genera su backup propio');run_deploy(c)
        elif args.operation=='rollback':run_rollback(c,args.target)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('BLOQUEADO: '+(str(error) if isinstance(error,Refusal) else 'Error operativo; revisar diagnóstico local sin divulgar secretos'),file=sys.stderr)
        sys.exit(1)
