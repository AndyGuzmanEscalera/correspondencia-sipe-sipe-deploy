# Publicar solo la API en loopback: QA existente

Este procedimiento se ejecuta MANUALMENTE en Debian por AnyDesk. La PC solo
prepara y prueba los archivos. No usar first_deploy_demo.sh, deploy.sh, down,
reset, migraciones, build ni restauracion DB para este cambio.

## Etapa 1: auditar y respaldar SIN pull

Coordinar una ventana sin altas/bajas de datos QA. El dump es consistente con
PostgreSQL activo; no detiene servicios. Los conteos deben permanecer iguales
hasta aplicar. Un incremento por actividad exige revision, nunca restauracion
DB automatica. El script copia configuracion privada, NO imprime secretos.
No hace backup del contenido del storage: lo identifica y exige conservar sus
mounts. Este cambio no escribe adjuntos ni reemplaza su volumen.

Desde el usuario habitual del repo, en Bash:

```bash
cd /srv/correspondencia-sipe-sipe-deploy
set -euo pipefail
umask 077
git status --short
# DETENERSE si hay cambios locales ajenos. No stash/reset ni sobrescribirlos.
git fetch origin main
QA_RELEASE="$(git rev-parse origin/main)"
QA_SCRIPT="$(mktemp /tmp/correspondencia-api-local.XXXXXX.py)"
git show "${QA_RELEASE}:scripts/api_local.py" > "$QA_SCRIPT"
sudo python3 -B "$QA_SCRIPT" audit-backup --root "$PWD"
```

NO hacer pull todavia. Compartir la salida y detenerse para revisarla.
La salida incluye servicios/redes/volumenes, IDs y mounts, DB/usuario (sin
passwords), conteos, ruta BACKUP, BYTES y PG_RESTORE_CATALOG=OK.
El script exige backend/postgres/web healthy, servicios y volumenes reales
coincidentes con Compose, configuracion backend coincidente y al menos 5 GiB
libres. Este minimo no garantiza espacio suficiente para una DB grande.
Si la instalacion usa COMPOSE_FILE/adopt, conservar exactamente ese entorno;
no cambiar al modo demo. Una discrepancia bloquea la operacion.

Respaldo en backups/pre-api-local-<identificador>/ (700), dump custom y archivos
privados (600 con umask 077). El dump tambien queda en backups/postgres/
mediante enlace duro al mismo archivo, sin duplicar espacio. Ambas rutas se
protegen con permisos privados. Incluye compose(s), scripts, Apache, .env/runtime.env
si existen, release.json, modelo Compose privado, identidad y conteos. No compartir
.env, compose.private.json ni el contenido del dump. Los backups estan ignorados
por Git. checkpoint.json solo aparece despues de pg_restore --list satisfactorio
con tablas users/employees/correspondences y hash SHA-256 del dump.

## Etapa 2: SOLO despues de revisar y aprobar la salida del respaldo

Reutilizar QA_RELEASE de la etapa anterior. Sustituir la ruta siguiente por
BACKUP exacto recibido; nunca inventar una ruta ni usar un respaldo incompleto.

```bash
cd /srv/correspondencia-sipe-sipe-deploy
set -euo pipefail
QA_BACKUP='/srv/correspondencia-sipe-sipe-deploy/backups/pre-api-local-RUTA_REAL'
: "${QA_RELEASE:?Reutilizar SHA de la etapa 1}"
test -z "$(git status --porcelain)"
git merge-base --is-ancestor HEAD "$QA_RELEASE"
git merge --ff-only "$QA_RELEASE"
sudo python3 -B scripts/api_local.py apply --backup "$QA_BACKUP"
```

El merge aplica exactamente el commit obtenido con fetch, equivalente al avance
fast-forward de un pull, sin incorporar cambios remotos aparecidos despues del
backup. No crea merge commit. Si incluye cambios extra de configuracion, aborta.

El script compara el modelo completo contra el privado del backup y admite
UNICAMENTE agregar backend.ports=127.0.0.1:8100:8000. Comprueba el hash/catalogo,
conteos, PostgreSQL original, web original y puerto libre. No cambia variables,
redes, aliases ni imagen. Ejecuta config --quiet sin volcar secretos a /tmp.
Solo despues ejecuta:

    docker compose up -d --no-deps --force-recreate --no-build --pull never backend

Espera healthy. Exige misma imagen y mounts backend, mismo ID/arranque/mounts
PostgreSQL y mismo contenedor web. Comprueba /health, /health/database, binding
Docker y ss, y compara conteos. No ejecuta login ni modifica usuarios.
Si hay error: conservar salida, no ejecutar restore DB. Evaluar rollback.

## Comprobaciones manuales posteriores

```bash
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/health/database
ss -lntp 'sport = :8100'
docker compose ps
git status --short
git diff -- docker-compose.yml
```

Health debe devolver status=ok (service segun configuracion actual);
health/database debe devolver database=connected. Binding Docker obligatorio
127.0.0.1, nunca 0.0.0.0/:: ni otro puerto. Docker con NAT y sin docker-proxy puede
no mostrar socket en ss: el script lo informa y valida PortBindings mas HTTP;
no interpreta la ausencia de socket como publicacion global.

Abrir la URL QA habitual, iniciar sesion con una cuenta existente y comprobar
/auth/me, funcionarios, unidades, roles/permisos y lectura de correspondencias.
No crear usuarios ni cambiar passwords. Compartir resultados sin tokens/cookies.
El login normal puede actualizar metadatos de sesion; no se automatiza ni modifica
informacion de negocio. La validacion funcional QA queda pendiente del operador.

## Rollback de infraestructura, independiente de DB

Si falla backend o la aplicacion tras aplicar:

```bash
cd /srv/correspondencia-sipe-sipe-deploy
sudo python3 -B scripts/api_local.py rollback --backup "$QA_BACKUP"
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health/database
git status --short
git diff -- docker-compose.yml
```

Restaura compose(s) originales y recrea exclusivamente backend sin build/pull.
No restaura .env, PostgreSQL ni storage. Admite backend detenido/unhealthy.
Si detecta imagen, mounts, DB o web distintos, aborta para revision manual.
El rollback deja el compose local distinto del nuevo commit; no hacer reset ni
pull para ocultarlo. Validar la aplicacion mediante su URL habitual.

## Recuperacion DB: SOLO desastre demostrado y autorizacion separada

NO ejecutar sobre una DB sana. Primero detener escritores, confirmar DB/usuario
y volumen contra checkpoint.json, guardar copia adicional del respaldo original,
verificar hash y catalogo. No eliminar la DB ni tablas para hacer sitio.

El siguiente comando restaura SOLO sobre la DB original ya existente y VACIA;
rechaza tablas publicas existentes. Una DB no vacia/corrupta necesita un plan
especifico antes de continuar. No hay --clean, drop, creacion DB ni downgrade.

```bash
cd /srv/correspondencia-sipe-sipe-deploy
set -euo pipefail
: "${QA_BACKUP:?Usar respaldo verificado}"
sudo docker compose stop backend
# Confirmar manualmente DB/volumen originales y ausencia de otros escritores.
sudo docker compose exec -T postgres sh -ec '
  test "$(psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM pg_tables WHERE schemaname = '\''public'\'';")" = 0
'
sudo cat "$QA_BACKUP/database.dump" | sudo docker compose exec -T postgres sh -ec '
  PGPASSWORD="$POSTGRES_PASSWORD" exec pg_restore --exit-on-error --single-transaction --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"
'
# Verificar catalogo/datos y Alembic contra backup ANTES de iniciar escritores.
sudo docker compose start backend
```

Si el test de DB vacia falla, DETENERSE: no continuar con pg_restore ni borrar
objetos. Si PostgreSQL/los datos estan correctos, este apartado NO se usa.
El storage sigue en su volumen original; perdida de ese volumen requiere otro
respaldo de storage y un plan de recuperacion separado.

## Futuro Tunnel (fuera de este cambio)

El endpoint directo usa /health, /auth, /correspondences, etc. El proxy web actual
quita /api; un futuro Tunnel directo no debe presuponer esa reescritura.
Revisar prefijo /api, trusted proxies, cookie path y HTTPS cuando se prepare ese
lote. No cambiar Tunnel, DNS, Pages, CORS, frontend ni RRHH en esta operacion.
