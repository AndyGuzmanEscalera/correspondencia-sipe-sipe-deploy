# Operación: fases y límites

## Configuración e identidad

Solo una configuración privada externa por entorno. Formato NOMBRE=valor literal,
sin comillas, duplicados ni expansión; los scripts no hacen source del archivo.
Nunca imprimir config interpolado, secretos ni usar set -x. Compose usa env_file
raw y las variables sensibles no se pasan en argumentos de comandos.

Completar EXPECTED_DOCKER_CONTEXT y EXPECTED_DOCKER_DAEMON_ID tras una inspección
autorizada del destino. No se inventan. El contexto activo debe coincidir y cada
comando usa --context explícito; DOCKER_HOST/TLS overrides se rechazan. Arquitectura
daemon y release deben coincidir; no se cambia contexto ni se usa emulación por defecto.

EXPECTED_DEPLOY_REF=main es una referencia esperada, no una instrucción de cambio
de rama. EXPECTED_DEPLOY_SHA permite fijar externamente un commit. HEAD se registra
siempre en el estado activo. No rellenar deploy_sha con un SHA autorreferencial.

Conservar secretos existentes; no rotarlos automáticamente. El contrato actual
exige contraseña DB URL-safe >=24 y JWT/HMAC distintos >=32; si las credenciales
actuales no cumplen, detener y proponer adaptación/hardening autorizado aparte.
DATABASE_URL vacío deja al backend construir POSTGRES_* internos. APP_VERSION se
inyecta desde el release, no requiere modificar secretos al cambiar versión.

## Preflight inicial (sin imágenes)

`preflight.sh --phase initial`: Git limpio (incluidos untracked no ignorados),
ningún privado versionado, manifiesto validado, checksum de todos los archivos
versionables, env/secretos, Docker/Compose, contexto/ID/arquitectura daemon,
volúmenes externos existentes y directorios operativos con reserva de disco.

Puerto 127.0.0.1:8080: libre OK; publicación loopback de web del proyecto
correspondencia-deploy OK para update; proceso o contenedor ajeno ABORTA. No mata
procesos. El reconocimiento Docker usa etiquetas de proyecto/servicio y binding.
No descubre automáticamente qué proceso externo sería aceptable.

## Pull e imágenes

`deploy.sh --pull-images` repite inicial y descarga solo backend/web por digest
con policy always. No descarga/reconcilia postgres ni construye.
`preflight.sh --phase images` comprueba imágenes/digests/plataforma, UID/GID,
archivos runtime/Alembic/assets, grafo y configuración web mediante probes efímeros
con rootfs read-only. httpd -t no inicia el servidor. No valida render de Flutter.
Los contenedores efímeros tienen nombres UUID corr-op-... y se eliminan al terminar,
incluso al vencer un timeout; no se eliminan contenedores ajenos.

## Backup y migración son independientes

`preflight.sh --phase backup --writers-stopped` comprueba DB/usuario/volumen,
storage/herramientas, ausencia de escritores y capacidad. No consulta compatibilidad
del grafo ni exige plan aprobado. Backup usa solo imagen backend como herramienta,
no requiere la imagen web local. Revisión desconocida o ausencia de alembic_version
se conserva en el manifiesto del respaldo, sin impedirlo.

`preflight.sh --phase migration` obtiene grafo de la imagen y revisiones DB, muestra
el plan y exige coincidencia exacta con database del release. Desconocida, origen
inesperado, pendientes extra o aprobación ausente bloquean upgrade, no backup.

## Aplicación

`deploy.sh --apply --writers-stopped` adquiere lock, revalida Git, descarga/verifica
imágenes, detiene solo web/backend de este proyecto, respalda ambos datos, valida
plan, migra y actualiza con up --no-deps --no-build --pull never --wait:

1. Confirmar PostgreSQL healthy, DB correcta e identidad del contenedor.
2. Levantar solo backend y esperar salud.
3. Confirmar la misma identidad PostgreSQL.
4. Levantar solo web y esperar salud.
5. Confirmar otra vez la misma identidad PostgreSQL.
6. HTTP/SPA, proxy/API/DB y tracking sintético; registrar active.json con SHA deploy.

Un fallo mantiene/intenta dejar aplicación detenida; si Docker no permite detenerla,
conservar bloqueo externo y actuar manualmente. No hay restauración automática.

## Primer corte: procedimiento posterior

Estos scripts presuponen PostgreSQL ya adoptado por el proyecto nuevo. NO diseñan
ni ejecutan la transición desde el deployment actual. Se deberá inspeccionar,
aprobar y ensayar ese primer corte aparte. No arrancar dos PostgreSQL sobre un
volumen. No crear sustitutos vacíos. Nombres previstos a confirmar:
correspondencia_sipe_sipe_postgres_data y correspondencia_sipe_sipe_backend_storage.

No alterar Website, Gestión ni RR.HH. La interpretación del plan es genérica; si
incluye cambios de otro módulo, su aprobación de alcance se realiza antes de
marcar el plan aprobado, sin borrar ni omitir esas revisiones.

## Proxy y aceptación posterior

Confirmar subnet 172.30.237.0/28, gateway y cadenas reales antes del uso. Apache
confía CF-Connecting-IP solo desde 172.30.237.1; Uvicorn solo en 172.30.237.10.
No confiar en '*'. Cloudflare debe preservar IP y no-store; no se configura aquí.
El listener loopback es destino del túnel HTTPS, no login público HTTP alternativo.

Healthcheck verifica HTML/JSON sin credenciales y sin cambiar filas; el tracking
sintético consume un intento del limitador en memoria. No acredita ejecución JS,
login, cookies, permisos, PDF o HTTPS real; esos E2E requieren QA posterior.

## Timeouts

Defaults: comandos 60s, pull 1800s, backup 7200s, migración 3600s, update 240s,
HTTP individual 8s. Alembic se ejecuta en contenedor efímero con timeout y cleanup.
En Debian se termina grupo de procesos (también plugin Compose), TERM y luego KILL.
pg_dump/pg_restore llevan además timeout dentro del contenedor DB. Nunca se mata
PostgreSQL. Ante fallos/timeout no hay éxito ni continuación automática.
