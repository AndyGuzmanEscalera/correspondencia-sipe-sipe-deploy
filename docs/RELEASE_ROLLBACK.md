# Rollback por Git

Usar únicamente un SHA anterior del repo deploy que haya sido aprobado.
Con checkout limpio:
bash scripts/rollback.sh --ref SHA_COMPLETO

El script comprueba que la DB tenga la misma head que el release destino.
Si difiere, NO hace checkout ni downgrade: requiere revisar compatibilidad o
restauración manual aprobada. Una head igual tampoco demuestra compatibilidad
funcional universal; el operador debe aprobar el release concreto.

Con head igual: respaldo nuevo -> git checkout --detach del SHA -> compose build
-> up backend/web sin recrear postgres -> healthcheck.
No hay reset, force, merge ni modificación de DB.
Después de volver hacia delante, seleccionar main aprobado y repetir el flujo.
No correr git pull mientras se está detached.
Si un build falla tras checkout, la app puede quedar detenida: diagnosticar y
seleccionar explícitamente un commit compatible. Nunca restaurar DB por defecto.

Rebuild por Git recupera las fuentes, no garantiza bytes idénticos: las imágenes
base por tag y las dependencias transitivas pueden actualizarse. Probar el
commit de rollback en un entorno aislado antes de aprobarlo.
