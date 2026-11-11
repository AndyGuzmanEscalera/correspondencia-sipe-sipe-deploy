# Release y rollback

## Manifiesto versionado

format_version=1; status=draft hasta imágenes y QA completos. No inventar SHA,
digests, plataforma o fechas. Campos vacíos actuales son placeholders deliberados.
source_sha refiere al desarrollo; deploy_sha vacío se resuelve desde Git al operar
(evita autorreferencia imposible); EXPECTED_DEPLOY_SHA puede fijarlo externamente.
El estado activo registra siempre el commit real de deploy.

main/tags protegidos y revisión humana son la autoridad del publicador. Checksums
aseguran integridad de archivos, no son firma del release. GHCR y pipeline de
publicación todavía no están implementados en esta preparación.

## Plan DB aprobado por release

`database` contiene target_head, allowed_current_revisions,
approved_pending_revisions, migration_required y plan_approved.
allowed_current_revisions es el frontier simultáneo exacto, no una lista de
orígenes alternativos. Si hay varios estados de origen posibles, preparar planes
aprobados distintos; no interpretar una unión como alternativas.

El inspector de imagen resuelve padres y dependencies de todas las revisiones.
Calcula ascendencia completa del destino menos las ya aplicadas (incluyendo ramas
del merge), muestra plan y compara conjuntos de origen/pendientes y destino/flag.
No se decide por prefijos, módulos, nombre de archivo ni revisiones hardcodeadas.
No se comparan IDs lexicográficamente para inferir compatibilidad.

Ejemplos de la cadena conocida (no son aprobación de una DB real):
- k1 → n1: pendientes h1, l1, m1, n1.
- l1 → n1: pendientes h1, m1, n1.
- h1+l1 → n1: pendientes m1, n1.
- m1 → n1: pendiente n1.
Cada caso se permite solo si coincide exactamente con su plan aprobado. Revisión
actual desconocida bloquea migrar, pero no backup. No omitir ramas ni hacer stamp,
downgrade o reescritura de historia. Que tablas existan no demuestra una migración
registrada: discrepancias requieren inspección/reconciliación autorizada aparte.

`migrate.sh` sin --apply solo inspecciona. El upgrade explícito exige
--apply --writers-stopped --backup RUTA, plan exacto, respaldo completo y revisión
revalidada. Con migration_required=false no invoca upgrade. Después exige target_head.
Repetir un release migrado exige revisar el plan de origen; no se acepta un origen
nuevo por comodidad ni se asume que migrar dos veces es correcto.

## Rollback de aplicación

`rollback.sh --apply --writers-stopped --target /checkout-anterior`

Usar desde commit/release activo registrado. active.json conserva previous con
manifiesto y commit exactos. El target debe coincidir con ambos; no se adivina una
versión ni se hace checkout o cambio de rama. Imágenes target deben existir por
digest y ser compatibles. Conservar el mismo runtime.env, DB y storage.

Se exige rollback_compatible=true y rollback_to exacto. El esquema actual debe
ser exactamente target.database.target_head, o existir una matriz explícita:
rollback_schema_compatibility.approved=true, current_revisions exactas y
target_release coincidente. La matriz requiere ensayo previo del código anterior
con ese esquema/datos, no una ordenación de revisiones.

Hace backup, confirma PostgreSQL, levanta backend y después web con --no-deps,
verifica salud y registra estado/commit. No ejecuta Alembic, descarga imágenes,
reconcilia PostgreSQL ni restaura DB. Un fallo intenta detener solo aplicación.
Un estado anterior sin procedencia registrada exige intervención manual.
El primer corte desde el stack legado se definirá después y no está automatizado.
