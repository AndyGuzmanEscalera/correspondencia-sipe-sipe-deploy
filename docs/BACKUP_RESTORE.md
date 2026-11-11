# Respaldo y restauración

backup.sh valida entorno, Docker y volúmenes; exige PostgreSQL iniciado.
Detiene exclusivamente web/backend y rechaza otras sesiones cliente de la base.
No ejecuta Alembic ni exige que la revisión actual sea conocida.

Cada carpeta bajo BACKUP_ROOT (fuera de Git, permisos privados) contiene:
- database.dump, pg_dump -Fc; índice comprobado con pg_restore --list.
- storage.tar.gz, tar del almacenamiento; lectura completa comprobada.
- backup.json, nombres de DB/volúmenes, revisión cruda, fecha y hashes de archivos.

Solo status=complete permite migrar. Una operación incompleta conserva archivos
para diagnóstico y NO autoriza upgrade. El lock impide dos operaciones locales
simultáneas. No protege contra escritores de otros equipos: bloquearlos durante
la ventana. El mínimo de espacio libre no garantiza capacidad del dump completo.

Antes de migrar, se comprueban hashes, DB, volúmenes y revisión del respaldo.
Un respaldo completo no equivale a haber ensayado una restauración.
Conservar copia fuera del host y probar restore en un entorno aislado.

Restauración: procedimiento MANUAL aprobado, con todos los escritores detenidos,
base y storage coordinados desde el mismo respaldo, secretos disponibles por vía
separada y verificación de permisos UID/GID 10001. No extraer tar ni restaurar
sobre volúmenes habituales sin autorización y respaldo adicional.
No hay script de restore ni restauración automática durante rollback.
