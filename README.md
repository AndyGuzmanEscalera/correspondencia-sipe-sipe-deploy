# Correspondencia GAM Sipe Sipe — deploy simple

Este repositorio contiene el runtime necesario para construir y ejecutar el sistema.
Flutter se compila en la PC; el servidor no necesita Flutter ni ejecuta pruebas.
No hay publicación/descarga de imágenes privadas ni transportes de imágenes.

## Estructura

```text
.env.example
docker-compose.yml
docker-compose.adopt.yml  # solo adopción futura de volúmenes externos
release.json
backend/                 # Dockerfile, app, Alembic, ini, requirements runtime
web/
  Dockerfile             # Apache, copia build precompilado
  apache/correspondencia.conf
  build/                 # Flutter release generado en PC, versionable
scripts/                 # backup, migrate, deploy, healthcheck, rollback, preflight
tests/                   # pruebas de operación y smoke local descartable
docs/
```

Cloudflare del host -> 127.0.0.1:8080 -> Apache no-root -> Flutter y /api/
-> backend:8000 -> postgres:5432 en red interna. PostgreSQL y backend no publican
puertos. Apache conserva cookie /auth -> /api/auth, fallback SPA y no-store.
Se mantienen las IPs del contrato validado: Apache 172.30.237.10, backend .11,
gateway confiable 172.30.237.1. Confirmar ausencia de solapamiento y el salto real.

## Primer corte demo limpio (modo actual)

Ver [FIRST_DEPLOY_DEMO.md](docs/FIRST_DEPLOY_DEMO.md) para los comandos completos
preparados para Debian sin Codex. init_demo_env.py genera .env privado sin
sobrescribirlo; first_deploy_demo.sh valida todo antes del corte y admite
--check y --reset-demo-data. Solo ese flag permite descartar datos demo.
No se crea administrador automáticamente.

## Preparar en PC

Desde C:\dev\correspondencia_sipe_sipe:

```powershell
./scripts/prepare_deploy.ps1 -ExpectedSourceSha (git rev-parse HEAD)
```

Exige develop/SHA exacto y fuentes funcionales committeadas. Ejecuta pruebas,
pub get y flutter build web --release --dart-define=APP_ENV=production con base /.
-SkipTests es opt-in explícito. -ValidateOnly no cambia archivos.
Copia únicamente runtime backend versionado, branding, Alembic y build/web.
Reemplaza solamente backend/app, backend/alembic y web/build; no toca .git, .env,
storage, backups ni Website. Genera Dockerfiles y release.json simple.
No hace staging/commit/push. Tras cada preparación repetir el smoke local y
cambiar status a validated solo si pasó. Revisar diff antes de autorizar commit.

## Configuración privada y persistencia

Copiar .env.example a .env ignorado con permisos 600, o usar un archivo externo:

```sh
export CORRESPONDENCIA_ENV_FILE=/etc/correspondencia/runtime.env
```

Los scripts pasan ese archivo a Compose explícitamente. Para usar Compose manual
con archivo externo: export RUNTIME_ENV_FILE="$CORRESPONDENCIA_ENV_FILE" y
docker compose --env-file "$CORRESPONDENCIA_ENV_FILE" ... .
No imprimir docker compose config completo con secretos; usar config --quiet.

Dos modos explícitos:
- INSTALL_MODE=demo (actual): correspondencia_demo_postgres_data y
  correspondencia_demo_backend_storage, nuevos y administrados por este proyecto.
- INSTALL_MODE=adopt (futuro): volúmenes históricos externos confirmados,
  docker-compose.adopt.yml y nombres exactos en .env.

El primer deploy limpio no necesita adoptar los históricos. No arrancar un
segundo PostgreSQL sobre el mismo volumen. No se hace chown automático de datos
existentes. Los scripts de actualización mantienen backup y verifican propiedad.
No versionar datos, entorno real, credenciales, dumps ni backups.

## Actualizaciones posteriores al primer corte

```sh
git pull --ff-only
bash scripts/preflight.sh
bash scripts/deploy.sh
# Si hay revisiones pendientes y se autoriza esa migración:
bash scripts/deploy.sh --migrate
```

deploy.sh valida entorno/Docker/volúmenes y PostgreSQL ya iniciado; construye con
docker compose build; detiene web/backend para respaldo consistente; respalda
PostgreSQL + storage; lee Alembic current/heads; migra solo con --migrate;
levanta backend/web con --no-deps --wait; verifica health.
No recrea PostgreSQL como efecto de actualizar la aplicación. Si PostgreSQL aún no
está iniciado, detenerse: confirmar nombres/datos/versiones y levantar postgres
explícitamente antes del despliegue autorizado.
Si la base tiene revisión desconocida, se respalda pero NO se migra.
Ante fallo después de detener la aplicación, puede quedar detenida: revisar el
error; no hay downgrade, stamp, restore ni reversión automática.

Operación separada: migrate.sh muestra plan sin escribir. Aplicar requiere
migrate.sh --apply --backup /ruta/backup_completo, en ventana autorizada.
backup.sh funciona aunque Alembic sea desconocido y deja web/backend detenidos.
Bloquear escritores externos durante respaldo/migración.
BACKUP_ROOT debe quedar fuera del checkout; MIN_FREE_GB es un mínimo, no una
estimación del tamaño final de DB + storage. Verificar capacidad antes de operar.

## Rollback

bash scripts/rollback.sh --ref SHA_DEPLOY_ANTERIOR_APROBADO

Exige SHA completo, Git limpio y misma head de DB que el release anterior. Crea
respaldo, hace checkout detached de ese commit, build, up y healthcheck.
No cambia DB. Con schema diferente se detiene ANTES de checkout: revisar
compatibilidad/restauración manual aprobada. Ver docs/RELEASE_ROLLBACK.md.

## Pruebas locales seguras

python -B -m unittest discover -s tests -p test_*.py -v

Para build/config usar entorno sintético externo y nombre de proyecto temporal;
nunca iniciar el Compose de producción contra volúmenes habituales durante QA.
Ejemplo después de docker compose build:
python tests/smoke_runtime.py --backend-image correspondencia-deploy-backend:latest --web-image correspondencia-deploy-web:latest

tests/smoke_runtime.py recibe los dos tags locales construidos desde este repo.
Crea PostgreSQL descartable, redes y storage propios, sin puerto DB publicado;
prueba migraciones, backend, web, SPA, /api, cookies y PDFs. Limpia solo sus
recursos. No lee .env habitual ni monta fuentes del host.

La head actual es n1b2c3d4e5f6. Se conserva el runtime/grafo compartido de RRHH
sin cambios y sin iniciar su worker. Ver docs/RUNTIME_COMPARTIDO.md.
Se requiere Docker Linux, Compose >=2.30, Git, Bash y Python >=3.10 para operar.
Esta preparación no modifica servidor, DNS, Cloudflare ni bases habituales.
