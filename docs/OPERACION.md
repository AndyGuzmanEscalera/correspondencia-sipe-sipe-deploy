# Operación

1. PC: preparar desde el SHA develop esperado; validar runtime/build/smoke.
2. Revisar y autorizar commit/push del repositorio deploy.
3. Servidor, en ventana aprobada: git pull --ff-only.
4. Configurar .env privado o CORRESPONDENCIA_ENV_FILE; confirmar volúmenes,
   PostgreSQL 16, storage 10001:10001, disco y subnet.
5. preflight.sh valida Docker, Compose y volúmenes sin crearlos.
6. deploy.sh: build -> detener app -> backup -> current/heads/plan.
7. Si hay pendientes, solo --migrate autoriza upgrade head.
8. up -d --no-deps --wait backend web -> healthcheck.

El servidor construye backend Python y Apache con build Flutter precompilado.
No necesita Flutter, fuentes Dart, tests de desarrollo ni herramientas frontend.
Las imágenes base públicas pueden descargarse al construir; no existe acceso a
imágenes privadas, registro de releases ni plataforma de imagen publicada.

No ejecutar docker compose down -v ni eliminar volúmenes. No usar stamp,
downgrade o reconciliación automática. No recrear PostgreSQL para actualizar app.
Si Alembic current falla, conservar el diagnóstico sanitizado y respaldo, detener
y diseñar reconciliación. Base sin revisión pero con tablas también se rechaza.

La consulta pública comparte /api y nunca debe cachearse con datos autenticados.
COOKIE_SECURE=true requiere HTTPS externo; el smoke HTTP local comprueba headers,
y login/refresh/logout con un usuario sintético; no sustituye un login de navegador con cookie Secure. Validar HTTPS real en otra fase.
