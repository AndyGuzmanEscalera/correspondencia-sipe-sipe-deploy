# Primer deploy limpio DEMO/QA en Debian

Preparado para ejecutarse SIN Codex. No instala herramientas del sistema,
no cambia Cloudflare y no modifica Gestión, Website ni sus puertos/volúmenes.

## Requisitos y publicación previa

Requiere Linux, Git, Python >=3.10, Docker Linux local y Docker Compose >=2.30.
El repo deploy debe estar revisado, committeado y disponible en origin.
Esta preparación local no ejecuta commit/push ni comandos en servidor.

Desde una cuenta autorizada con acceso a Docker y al checkout:

```sh
# Solo si aún no existe este checkout independiente:
git clone https://github.com/AndyGuzmanEscalera/correspondencia-sipe-sipe-deploy.git /srv/correspondencia-sipe-sipe-deploy
cd /srv/correspondencia-sipe-sipe-deploy
git pull --ff-only
# Solo si NO existe .env. Nunca sobrescribe un archivo existente:
python3 scripts/init_demo_env.py
```

El generador crea .env privado con permisos 600 y tres secretos independientes.
No imprime secretos ni crea usuarios. Si .env ya existe, se detiene: revisarlo,
no borrarlo ni reemplazarlo automáticamente. Primer demo requiere .env regular
en la raíz, INSTALL_MODE=demo y los nombres de volúmenes nuevos indicados abajo.
No reutilizar sin revisión un .env antiguo que apunte a otra aplicación.

## Inspección y ejecución

Para el primer corte que retira los servicios históricos de Correspondencia:

```sh
bash scripts/first_deploy_demo.sh --reset-demo-data --check
# Revisar el plan mostrado; solo si es correcto:
bash scripts/first_deploy_demo.sh --reset-demo-data
```

Usar bash no requiere cambiar permisos ni ensuciar el checkout.
Si el commit aprobado registra el script como ejecutable, también se admite:
./scripts/first_deploy_demo.sh --reset-demo-data
No cambiar el modo con chmod en servidor: el preflight exige Git limpio.

Si no hay servicios históricos activos ni recursos demo previos, puede usarse:
bash scripts/first_deploy_demo.sh --check
bash scripts/first_deploy_demo.sh

--check no construye, borra, inicia ni migra Docker/DB. Mantiene solamente un lock
local ignorado para evitar dos ejecuciones simultáneas.
Sin --reset-demo-data NO elimina contenedores ni volúmenes. Si ya existe el demo
o hay históricos activos, se detiene; para actualizar un demo en uso corresponde
deploy.sh, no repetir el primer corte.

## Recursos y protecciones

Proyecto Compose: correspondencia-deploy.
Modo actual: demo, volúmenes administrados NUEVOS:
- correspondencia_demo_postgres_data
- correspondencia_demo_backend_storage

Únicamente con --reset-demo-data se permite eliminar:
- sipe_correspondencia_backend
- sipe_correspondencia_db
- contenedores de servicios postgres/backend/web que estén etiquetados como
  propiedad del proyecto correspondencia-deploy;
- correspondencia_sipe_sipe_postgres_data
- correspondencia_sipe_sipe_backend_storage
- los dos volúmenes demo nuevos, si pertenecen inequívocamente a este proyecto.

No se usan coincidencias por prefijo para borrar volúmenes históricos. El nombre
completo autorizado es correspondencia_sipe_sipe_backend_storage.
El reset descarta esos datos DEMO SIN BACKUP AUTOMÁTICO; está diseñado para el
primer corte autorizado de QA. Los scripts normales conservan su flujo de backup.

Antes de cualquier borrado se comprueban nombres, IDs completos, etiquetas,
montajes, consumidores de volúmenes, puertos y propiedad. Ante referencias a
Gestión, gestion_sipe, sipe_sipe_gestion, Website, 8001 o 5433, se aborta.
Un volumen compartido con otro contenedor bloquea TODO el reset.
No hay prune ni borrados globales, no se eliminan bind mounts del host y no se
eliminan redes existentes. Cambios de inventario después del build abortan.
El puerto 8080 ocupado por un tercero y un subnet solapado bloquean el corte.

El Docker daemon debe ser local (socket Unix), no un contexto SSH/TCP ni un
DOCKER_HOST externo. Se comprueba espacio en checkout y disco de Docker.
El mínimo MIN_FREE_GB es una condición de inicio, no una garantía de capacidad.

## Secuencia real

Validar repo oficial limpio/.env/Docker/Compose/puerto/disco/recursos/subnet
-> mostrar CORRESPONDENCIA DEMO y plan
-> build backend y web
-> revalidar inventario
-> reset SOLO si se pasó el flag
-> verificar 8080 libre
-> up postgres con volúmenes demo nuevos
-> exigir base pública sin tablas
-> comprobar única head de la imagen
-> alembic upgrade head
-> exigir n1b2c3d4e5f6
-> up backend y web, sin recrear postgres
-> health DB/API/Apache y rutas SPA.

El build termina ANTES de borrar datos históricos. Ante fallo posterior al corte,
el script se detiene; puede quedar un demo parcial. No hay rollback de datos,
downgrade, stamp ni recuperación automática.

Único puerto publicado: 127.0.0.1:8080:8080.
Backend: expose 8000; PostgreSQL: 5432 solo en red interna.
Cloudflare se configurará por separado hacia http://127.0.0.1:8080.
El script no cambia DNS, túneles ni Apache del host.

## Usuarios y autenticación

La instalación limpia no conserva usuarios previos. Las migraciones incluyen
catálogos/roles estructurales, pero no crean un administrador ni una contraseña.
El alta inicial de administrador requiere una acción explícita posterior.
Abrir la pantalla de login no implica que ya exista una cuenta utilizable.

COOKIE_SECURE=true se conserva para HTTPS externo. La prueba HTTP local valida
servicio/SPA, no sustituye validar cookies de navegador detrás de Cloudflare.

## Adopción futura (NO usar para este primer corte)

INSTALL_MODE=adopt y nombres históricos confirmados en .env.
Los scripts normales agregan docker-compose.adopt.yml automáticamente.
Con Compose manual se deben pasar ambos archivos:

docker compose -f docker-compose.yml -f docker-compose.adopt.yml --env-file .env config --quiet

Ese override exige volúmenes externos existentes; no crea reemplazos silenciosos.
Inspeccionar propiedad, schema y permisos antes de adoptar. first_deploy_demo.sh
rechaza INSTALL_MODE=adopt. No mezclar reset demo con adopción de datos existentes.
