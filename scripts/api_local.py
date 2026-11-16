"""Manual QA stages. Never connects remotely, builds, migrates, or restores DB."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

TABLES = ('users', 'employees', 'correspondences', 'organizational_units', 'roles', 'permissions', 'correspondence_attachments')
PORT = {'target': 8000, 'published': '8100', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def check_change(before, after):
    a, b = copy.deepcopy(before), copy.deepcopy(after)
    require(set(a['services']) == {'backend', 'postgres', 'web'}, 'Servicios inesperados; revisar antes de continuar')
    require(set(b['services']) == set(a['services']), 'Se cambiaron servicios')
    old = a['services']['backend'].pop('ports', [])
    new = b['services']['backend'].pop('ports', [])
    require(not old, 'El backend ya tenia puertos publicados; revisar manualmente')
    require(len(new) == 1 and all(new[0].get(k) == v for k, v in PORT.items()), 'Binding distinto de 127.0.0.1:8100:8000')
    require(a == b, 'Compose cambia algo adicional al puerto backend; ABORTADO')


def check_identity(before, after):
    for key in ('postgres', 'web'):
        require(before[key] == after[key], 'Cambio inesperado en ' + key + '; no restaurar DB automaticamente')
    for key in ('image', 'mounts'):
        require(before['backend'][key] == after['backend'][key], 'Cambio inesperado backend ' + key)
    require(before['database'] == after['database'], 'Cambio de base/usuario PostgreSQL')


def check_catalog(text):
    for table in ('users', 'employees', 'correspondences'):
        require(any(' TABLE public ' + table + ' ' in line for line in text.splitlines()), 'Dump sin tabla requerida: ' + table)


class QA:
    def __init__(self, root):
        require(platform.system() == 'Linux', 'Ejecutar manualmente en Debian; no ejecutar contra Docker de desarrollo')
        self.root = root.resolve()
        require(str(self.root) == '/srv/correspondencia-sipe-sipe-deploy', 'Ruta QA inesperada')
        require((self.root / '.env').is_file(), 'Falta .env; NO inicializar otro entorno')
        self.run(['docker', 'compose', 'version'])
        remote = self.run(['git', 'remote', 'get-url', 'origin']).strip()
        require(remote.rstrip('/').removesuffix('.git').endswith('AndyGuzmanEscalera/correspondencia-sipe-sipe-deploy'), 'Repo incorrecto')
        self.config = json.loads(self.dc('config', '--format', 'json'))
        require(set(self.config['services']) == {'postgres', 'backend', 'web'}, 'Servicios inesperados')
        require('correspondencia' in self.config['name'].lower() and 'gestion' not in self.config['name'].lower(), 'Proyecto ajeno')

    def run(self, args, *, output=None, input_file=None):
        if args[0] == 'git':
            args = ['git', '-c', 'safe.directory=' + str(self.root), *args[1:]]
        result = subprocess.run(args, cwd=self.root, stdin=input_file, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=900)
        require(result.returncode == 0, 'Fallo comando ' + ' '.join(args[:3]) + '; codigo=' + str(result.returncode) + ' (salida omitida para proteger secretos)')
        return result.stdout.decode() if output is None else ''

    def dc(self, *args, **kw):
        return self.run(['docker', 'compose', *args], **kw)

    def inspect(self, service, healthy=True):
        ids = self.dc('ps', '-a', '-q', service).split()
        require(len(ids) == 1, 'Debe existir un contenedor activo de ' + service)
        row = json.loads(self.run(['docker', 'inspect', ids[0]]))[0]
        labels = row['Config']['Labels']
        require(labels.get('com.docker.compose.project') == self.config['name'], 'Contenedor de otro proyecto')
        require(labels.get('com.docker.compose.service') == service, 'Servicio incorrecto')
        if healthy:
            require(row['State']['Running'], service + ' no esta funcionando')
            require(row['State'].get('Health', {}).get('Status') == 'healthy', service + ' no esta healthy')
        return row

    def snapshot(self, allow_unhealthy_backend=False):
        result = {}
        for service in ('postgres', 'backend', 'web'):
            row = self.inspect(service, healthy=not (service == 'backend' and allow_unhealthy_backend))
            if service == 'backend':
                spec = self.config['services'][service]
                require(not row['Config'].get('Entrypoint') and row['Config']['Cmd'] and row['Config']['Cmd'][0] == 'uvicorn', 'Startup inesperado; descartar migraciones antes de recrear')
                actual_env = dict(v.split('=', 1) for v in row['Config']['Env'] if '=' in v)
                require(all(actual_env.get(k) == (str(v) if v is not None else None) for k, v in spec.get('environment', {}).items()), 'Entorno backend difiere del Compose efectivo; revisar antes de recrear')
                require(spec.get('command', row['Config']['Cmd']) == row['Config']['Cmd'], 'Comando backend difiere del Compose efectivo')
                require(set(row['NetworkSettings']['Networks']) == {self.config['networks'][n]['name'] for n in spec.get('networks', {})}, 'Redes backend difieren del Compose efectivo')
            # JSON-native entries keep live snapshots equal to persisted checkpoints.
            mounts = sorted([m['Type'], m.get('Name') or m.get('Source', ''), m['Destination']] for m in row['Mounts'])
            require(not any('gestion' in str(m).lower() for m in mounts), 'Mount ajeno a Correspondencia')
            result[service] = {'id': row['Id'], 'started': row['State']['StartedAt'], 'image': row['Image'], 'mounts': mounts}
            if service == 'postgres':
                env = dict(v.split('=', 1) for v in row['Config']['Env'] if '=' in v)
                result['database'] = {k: env.get(k) for k in ('POSTGRES_DB', 'POSTGRES_USER')}
                require(all(result['database'].values()), 'No se identifico DB/usuario')
            for mount in self.config['services'][service].get('volumes', []):
                if mount['type'] == 'volume':
                    name = self.config['volumes'][mount['source']]['name']
                    require(any(m[0] == 'volume' and m[1] == name and m[2] == mount['target'] for m in mounts), 'Volumen efectivo no coincide con Compose; comprobar COMPOSE_FILE/adopt')
        return result

    def counts(self):
        sql = 'BEGIN READ ONLY; ' + ' UNION ALL '.join("SELECT '" + t + "', count(*) FROM public." + t for t in TABLES) + '; COMMIT;'
        return self.dc('exec', '-T', 'postgres', 'sh', '-ec', 'exec psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', 'sh', sql)

    def catalog(self, dump):
        with dump.open('rb') as stream:
            text = self.dc('exec', '-T', 'postgres', 'pg_restore', '--list', input_file=stream)
        check_catalog(text)

    def audit_backup(self):
        print('CORRESPONDENCIA QA - AUDITORIA Y RESPALDO; no se recrean servicios', flush=True)
        before = self.snapshot()
        require(not self.config['services']['backend'].get('ports'), 'Backend ya publicado: revisar estado real antes de este procedimiento')
        print(self.dc('ps'), end='')
        for kind in ('--services', '--volumes', '--networks'):
            print(kind + ':\n' + self.dc('config', kind), end='')
        print('Identidad y mounts (sin secretos):\n' + json.dumps(before, indent=2))
        counts = self.counts()
        require(shutil.disk_usage(self.root).free >= 5 * 1024**3, 'Menos de 5 GiB libres; revisar capacidad del respaldo')
        backups = self.root / 'backups'
        require(not backups.is_symlink(), 'Directorio backups no debe ser symlink')
        backups.mkdir(mode=0o700, exist_ok=True)
        os.chmod(backups, 0o700)
        folder = Path(tempfile.mkdtemp(prefix='pre-api-local-', dir=backups))
        os.chmod(folder, 0o700)
        infra = folder / 'infrastructure'
        infra.mkdir(mode=0o700)
        for pattern in ('*compose*.yml', '*compose*.yaml', '.env', 'runtime.env', 'release.json'):
            for source in self.root.glob(pattern):
                if source.is_file():
                    shutil.copyfile(source, infra / source.name)
                    os.chmod(infra / source.name, 0o600)
        for name in ('scripts', 'apache', 'web/apache'):
            source = self.root / name
            if source.is_dir():
                shutil.copytree(source, infra / name)
        (folder / 'compose.private.json').write_text(json.dumps(self.config))
        (folder / 'counts.before.txt').write_text(counts)
        (folder / 'git.before.txt').write_text(self.run(['git', 'status', '--short']) + '\nHEAD=' + self.run(['git', 'rev-parse', 'HEAD']))
        dump_dir = backups / 'postgres'
        require(not dump_dir.is_symlink(), 'backups/postgres no debe ser symlink')
        dump_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(dump_dir, 0o700)
        dump = folder / 'database.dump'
        with dump.open('xb') as stream:
            self.dc('exec', '-T', 'postgres', 'sh', '-ec', 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"', output=stream)
        require(dump.stat().st_size > 0, 'Dump vacio')
        self.catalog(dump)
        require(before == self.snapshot(), 'Contenedores cambiaron durante backup; detenerse')
        published_dump = dump_dir / (folder.name + '.dump')
        os.link(dump, published_dump)
        (folder / 'checkpoint.json').write_text(json.dumps({'before': before, 'sha256': digest(dump)}, indent=2))
        print('BACKUP=' + str(folder))
        print('DUMP=' + str(published_dump) + '\nBYTES=' + str(dump.stat().st_size) + '\nPG_RESTORE_CATALOG=OK')
        print('Conteos iniciales (sin datos personales):\n' + counts)
        print('DETENERSE: compartir esta salida antes de pull/apply. No hubo migraciones ni restauracion.')

    def checkpoint(self, folder):
        folder = folder.resolve()
        require(folder.parent == self.root / 'backups' and folder.name.startswith('pre-api-local-'), 'Backup fuera de la ruta permitida')
        require(folder.stat().st_mode & 0o077 == 0, 'Backup requiere permisos privados')
        data = json.loads((folder / 'checkpoint.json').read_text())
        require(digest(folder / 'database.dump') == data['sha256'], 'Hash del dump no coincide')
        self.catalog(folder / 'database.dump')
        return folder, data

    def recreate(self, before):
        expected = before['backend']['image']
        name = self.config['services']['backend'].get('image', self.config['name'] + '-backend')
        require(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name]).strip() == expected, 'Tag backend cambio de imagen; no recrear')
        self.dc('up', '-d', '--no-deps', '--force-recreate', '--no-build', '--pull', 'never', 'backend')
        for _ in range(45):
            try:
                check_identity(before, self.snapshot())
                return
            except RuntimeError:
                time.sleep(2)
        raise RuntimeError('No se alcanzo healthy; usar rollback de infraestructura, NO restaurar DB')

    def verify(self, folder, before):
        check_identity(before, self.snapshot())
        for path in ('health', 'health/database'):
            with urllib.request.urlopen('http://127.0.0.1:8100/' + path, timeout=10) as response:
                payload = json.load(response)
            require(payload.get('status') == 'ok', 'Health fallo: ' + path)
            if path.endswith('database'):
                require(payload.get('database') == 'connected', 'Health sin conexion DB')
            print(path + ': ' + json.dumps(payload))
        inspect = self.inspect('backend')
        require(inspect['HostConfig']['PortBindings'] == {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8100'}]}, 'Binding efectivo inesperado')
        listeners = self.run(['ss', '-lnt']).splitlines()
        matching = [line for line in listeners if len(line.split()) >= 4 and line.split()[3].endswith(':8100')]
        require(all(line.split()[3] == '127.0.0.1:8100' for line in matching), 'Listener no restringido a loopback')
        print('BINDING_DOCKER=127.0.0.1:8100:8000; ss=' + (' | '.join(matching) or 'sin socket visible (posible NAT sin docker-proxy)'))
        now = self.counts()
        (folder / 'counts.after.txt').write_text(now)
        print('Conteos despues:\n' + now)
        require(now == (folder / 'counts.before.txt').read_text(), 'Conteos distintos: puede haber actividad QA; revisar antes de confirmar. NO restaurar DB automaticamente')
        print('DATOS_CONTEOS=IGUALES; POSTGRES_MISMO_CONTENEDOR_Y_MOUNTS=OK; WEB_INTACTO=OK')
        print(self.dc('ps'), end='')
        print(self.run(['git', 'status', '--short']), end='')
        print(self.run(['git', 'diff', '--', 'docker-compose.yml']), end='')
        print('Login, /auth/me y lectura funcional: verificacion manual pendiente en navegador QA.')

    def apply(self, folder, rollback=False):
        folder, data = self.checkpoint(folder)
        before = data['before']
        check_identity(before, self.snapshot(allow_unhealthy_backend=rollback))
        original = json.loads((folder / 'compose.private.json').read_text())
        check_change(original, self.config)
        if not rollback:
            require(self.counts() == (folder / 'counts.before.txt').read_text(), 'Conteos cambiaron desde backup: revisar/refrescar backup antes de aplicar')
        if rollback:
            for source in (folder / 'infrastructure').glob('*compose*.y*ml'):
                shutil.copyfile(source, self.root / source.name)
            self.config = json.loads(self.dc('config', '--format', 'json'))
            require(self.config == original, 'Rollback Compose no coincide; no recrear')
        else:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 8100))
        self.dc('config', '--quiet')
        self.recreate(before)
        if rollback:
            check_identity(before, self.snapshot())
            print('ROLLBACK_COMPOSE=OK; solo backend recreado; DB NO restaurada')
        else:
            self.verify(folder, before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('audit-backup', 'apply', 'verify', 'rollback'))
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    qa = QA(args.root)
    if args.action == 'audit-backup':
        qa.audit_backup()
    else:
        require(args.backup is not None, 'Indicar --backup exacto')
        if args.action == 'verify':
            folder, data = qa.checkpoint(args.backup)
            qa.verify(folder, data['before'])
        else:
            qa.apply(args.backup, rollback=args.action == 'rollback')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Never print captured config/env/subprocess stderr or credentials.
        message = sys.exc_info()[1]
        print('DETENIDO: ' + (str(message) if isinstance(message, RuntimeError) else type(message).__name__ + '; revisar entorno sin mostrar secretos'), file=sys.stderr)
        sys.exit(1)
