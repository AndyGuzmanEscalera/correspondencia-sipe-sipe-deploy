# Backup y recuperación

## Independencia del plan de migración

`backup.sh --apply --writers-stopped` requiere identidad Git/daemon/env válidos,
imagen backend local compatible y preflight exclusivo de respaldo. No requiere
que DB esté en un grafo conocido ni plan_approved. No aplica migraciones.
No depende de disponibilidad del puerto de la aplicación para preservar datos.

El operador debe mantener mantenimiento efectivo y bloquear escritores externos.
Se rechazan contenedores activos con storage RW y otras sesiones cliente de DB;
estas observaciones no son un bloqueo transaccional contra futuros clientes.

## Capacidad antes del respaldo

Se consulta pg_database_size de la DB y se suma tamaño lógico de archivos regulares
de storage sin seguir symlinks. Fórmula conservadora inicial:

`requerido = 2 * (bytes_DB + bytes_storage) + MIN_FREE_GB * 1024^3`

No se descuenta compresión. El factor 2 cubre overhead/variación aproximada; no es
una garantía matemática (datos, tar de muchos archivos pequeños y crecimiento
pueden superar la estimación). Se exige reserva básica también para STATE_DIR.

Si no se puede medir, se aborta salvo --allow-unestimated-backup explícito. Esa
excepción advierte y requiere al menos UNESTIMATED_BACKUP_MIN_GB (mínimo 100 GiB)
y la reserva configurada. No afirma que hay espacio suficiente. No permite
continuar si una estimación calculada ya dice que el respaldo no cabe.
Definir RPO/RTO, retención y volumen máximo antes de uso real.

## Unidad lógica

Fuera de Git, directorio UTC con sufijo aleatorio, modo 700 y umask 077:
- database.dump: pg_dump -Fc, acotado por timeout.
- storage.tar.gz: volumen montado read-only, symlinks no seguidos.
- manifest.json: complete solo después de ambas partes y validaciones; hashes,
  release, SHA deploy, estimación, DB/volúmenes y revisión(es) raw observadas.

pg_restore --list valida índice sin restaurar; lectura completa del archivo
storage detecta errores de lectura/compresión. No equivalen a ensayo de restore.
Fallo DB o storage/timeout deja parcial sin manifiesto complete; no se borra solo.
No versionar ni incluir runtime.env: respaldar secretos separadamente y cifrados.

Antes de migrar, verificar respaldo del mismo release, DB/volúmenes, esquema,
hashes y antigüedad <=24h. Comparar revisiones no detecta cambios de filas: mantener
escritores bloqueados desde el backup hasta terminar la migración.

## Restore manual y autorizado aparte

No hay script que restaure/borrre datos automáticamente. Nunca down -v, stamp o
downgrade para salir de un error de código.

1. Autorizar pérdida potencial de operaciones posteriores al respaldo.
2. Mantener mantenimiento y preservar evidencia del estado fallido.
3. Verificar manifiesto, hashes y versión PostgreSQL compatible.
4. Ensayar DB/storage en destinos nuevos descartables; validar tar/rutas/enlaces
   antes de extraer, nunca sobrescribir storage real a ciegas.
5. Comprobar relaciones DB/archivos, PDFs y revisión Alembic.
6. Aprobar explícitamente cualquier sustitución de persistencia; conservar originales.
7. Arrancar imágenes compatibles y probar antes de reabrir acceso.

Backups periódicos, cifrado, copia externa, alertas y restauración periódica siguen
pendientes. Esta preparación no acredita backup productivo ni restore exitoso.
