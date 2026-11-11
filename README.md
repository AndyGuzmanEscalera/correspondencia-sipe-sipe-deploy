# Correspondencia GAM Sipe Sipe — deploy

**DRAFT: no desplegable.** No hay digests ni imágenes validadas, ni plan DB aprobado.
No se ejecutaron servicios, migraciones, respaldos o cambios de servidor/Cloudflare.
El primer commit requiere revisión; no se ha realizado.

## Separación y arquitectura

Desarrollo: `C:\dev\correspondencia_sipe_sipe` (fuentes/tests/build).
Deploy: este repositorio (Compose, Apache, manifiesto y operación).
Persistencia: volúmenes externos; secretos, backups y estado fuera del checkout.

Cloudflare Tunnel del host → 127.0.0.1:8080 → Apache web no-root
→ Flutter con base `/` y rutas `/correspondencia/login`, `/correspondencia/home`,
`/correspondencia/consulta`, `/consulta-publica`
→ `/api/` a `backend:8000` → `postgres:5432` en red Docker interna.

Solo web publica puerto. No se han cambiado los bindings actuales de ningún sistema.
No se configura DNS, Cloudflare, Website, Gestión ni RR.HH. en esta tarea.

## Flujo real

Desarrollo SHA limpio → QA → imágenes web/backend probadas → GHCR por digest
→ repo deploy revisado → tag/release → servidor pull de código operativo
→ **preflight inicial sin imágenes** → pull exacto de imágenes
→ **preflight imágenes** → mantenimiento → **backup**
→ **preflight migración** → migración explícita
→ update backend y después web **sin dependencias** → healthcheck → activar release.

El servidor no ejecuta flutter build, docker build, pip install ni npm build.
PostgreSQL se gestiona separadamente: el update de aplicación no lo incluye ni
reconcilia dependencias. Verificación de identidad/health antes, entre y después
confirma que se sigue usando el mismo contenedor PostgreSQL.

## Uso futuro autorizado en Debian

Requiere Linux, Bash, Python >=3.10, Git, Docker y Compose >=2.30.
Preparar runtime.env externo (ej. /etc/correspondencia/runtime.env), permisos 600:

```sh
export CORRESPONDENCIA_ENV_FILE=/etc/correspondencia/runtime.env
bash scripts/preflight.sh --phase initial
bash scripts/deploy.sh --pull-images
bash scripts/preflight.sh --phase images
# Solo con ventana aprobada y escritores externos bloqueados:
bash scripts/deploy.sh --apply --writers-stopped
bash scripts/healthcheck.sh
```

El deploy aplicado repite inicial, descarga y verifica imágenes bajo el lock antes
de detener servicios. La descarga usa `pull --policy always backend web`, de modo
que `pull_policy: never` del arranque no omita la descarga explícita por digest.
No descarga/recrea PostgreSQL. Si falla tras detener aplicación, intenta dejar
web/backend detenidos; no ejecuta downgrade, restore ni una reversión por su cuenta.

También se puede inspeccionar `migrate.sh` sin --apply (muestra plan y lo valida).
Para aplicar por separado: --apply --writers-stopped --backup RUTA.
Para backup de preservación: backup.sh --apply --writers-stopped. Una revisión
Alembic desconocida NO impide respaldar: sí impide migrar. No se exige aprobar
un plan de migración para obtener un backup.

## Contrato del release

release.json format_version=1 conserva digests vacíos/status=draft hasta validar.
`database.allowed_current_revisions` representa UN frontier exacto (lista de
revisiones simultáneas), no alternativas. Se aprueban origen, destino, conjunto
completo de pendientes y migration_required mediante plan_approved=true.
No hay excepciones por nombre de módulo ni revisión concreta.

`deploy_sha` vacío es placeholder: un commit no puede contener su propio SHA.
Al operar se obtiene HEAD real, se exige Git limpio y se guarda en active.json.
Puede fijarse externamente EXPECTED_DEPLOY_SHA. EXPECTED_DEPLOY_REF comprueba
que esa referencia apunte a HEAD; no se cambia de rama ni se hace checkout.

## Imágenes pendientes (propuesta; NO se modificó desarrollo)

Backend: /app/app, /app/alembic, /app/alembic.ini, Alembic/Uvicorn/Psycopg,
recursos oficiales de PDF; UID/GID 10001; raíz read-only; escritura solo en
/app/storage y /tmp. No migrar en entrypoint. El Dockerfile actual de desarrollo
necesita revisión para ser autónomo; no se ha cambiado.

Web: build Flutter completo, main.dart.js, assets y Apache 2.4 no-root; comando
httpd-foreground, módulos rewrite/proxy/proxy_http/headers/remoteip; include de
conf/extra/correspondencia.conf; reemplazar Listen de la configuración principal,
no duplicarlo; directorio htdocs correcto, runtime/PID en /tmp, curl y herramientas
sh/id disponibles. `httpd -t` se comprobará con esa imagen exacta, sin arrancar web.

GHCR previsto:
- ghcr.io/andyguzmanescalera/correspondencia-sipe-sipe-backend
- ghcr.io/andyguzmanescalera/correspondencia-sipe-sipe-web

## Archivos y pruebas

Seis wrappers Bash usan scripts/operations.py (stdlib). Checksums cubren archivos
versionables, no .env/runtime.env, dumps, logs, backups o storage. Archivos
operativos inesperados se rechazan separadamente; privados nunca pueden estar
versionados según la comprobación Git.

```sh
python3 -B -m unittest discover -s tests -v
bash -n scripts/preflight.sh  # repetir para los seis wrappers
bash scripts/preflight.sh --static
```

Static no contacta Docker, DB o red. Tests usan datos sintéticos/mocks y Python
local (incluido -O), sin contenedores. Validación de imágenes/Apache y E2E Debian
quedan pendientes. Véanse docs/OPERACION.md, BACKUP_RESTORE.md y RELEASE_ROLLBACK.md.

No usar git add indiscriminado, down -v, borrado de volúmenes, stamp ni downgrade.
No SMTP, semillas automáticas, creación de usuarios o credenciales temporales.
