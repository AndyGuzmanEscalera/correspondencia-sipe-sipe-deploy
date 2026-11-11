"""Idempotent seed for institutional base roles (ADMIN, MANAGER, OPERATOR).

Used by Alembic data migration and safe to re-run: upserts roles by code and
adds missing master_data role_permissions without deleting custom/future grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

MASTER_DATA_PERMISSIONS: tuple[str, ...] = (
    "master_data.organizational_units.read",
    "master_data.organizational_units.manage",
    "master_data.positions.read",
    "master_data.positions.manage",
    "master_data.employees.read",
    "master_data.employees.manage",
    "master_data.users.read",
    "master_data.users.manage",
    "master_data.document_types.read",
    "master_data.document_types.manage",
)

BASE_ROLES: Mapping[str, tuple[str, str, Sequence[str]]] = {
    "ADMIN": (
        "Administrador",
        "Administración general del sistema",
        MASTER_DATA_PERMISSIONS,
    ),
    "MANAGER": (
        "Gestor",
        "Gestión funcional institucional",
        (
            "master_data.organizational_units.read",
            "master_data.organizational_units.manage",
            "master_data.positions.read",
            "master_data.positions.manage",
            "master_data.employees.read",
            "master_data.employees.manage",
            "master_data.users.read",
            "master_data.document_types.read",
            "master_data.document_types.manage",
        ),
    ),
    "OPERATOR": (
        "Operador",
        "Operación institucional de correspondencia",
        (),
    ),
}


def ensure_institutional_base_roles(connection: Connection) -> None:
    permission_ids = _load_master_data_permission_ids(connection)
    for code, (name, description, permission_codes) in BASE_ROLES.items():
        role_id = _upsert_role(connection, code=code, name=name, description=description)
        _ensure_role_permissions(
            connection,
            role_id=role_id,
            permission_ids=[permission_ids[c] for c in permission_codes],
        )


def _load_master_data_permission_ids(connection: Connection) -> dict[str, uuid.UUID]:
    rows = connection.execute(
        text(
            """
            SELECT code, id
            FROM permissions
            WHERE code = ANY(:codes)
            """
        ),
        {"codes": list(MASTER_DATA_PERMISSIONS)},
    ).all()
    by_code = {row[0]: row[1] for row in rows}
    missing = [code for code in MASTER_DATA_PERMISSIONS if code not in by_code]
    if missing:
        raise RuntimeError(
            "Faltan permisos master_data requeridos: " + ", ".join(missing)
        )
    return by_code


def _upsert_role(
    connection: Connection,
    *,
    code: str,
    name: str,
    description: str,
) -> uuid.UUID:
    new_id = uuid.uuid4()
    row = connection.execute(
        text(
            """
            INSERT INTO roles (id, code, name, description, is_active, created_at, updated_at)
            VALUES (:id, :code, :name, :description, TRUE, NOW(), NOW())
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_active = TRUE,
                updated_at = NOW()
            RETURNING id
            """
        ),
        {
            "id": new_id,
            "code": code,
            "name": name,
            "description": description,
        },
    ).one()
    return row[0]


def _ensure_role_permissions(
    connection: Connection,
    *,
    role_id: uuid.UUID,
    permission_ids: Iterable[uuid.UUID],
) -> None:
    for permission_id in permission_ids:
        connection.execute(
            text(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (:role_id, :permission_id)
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """
            ),
            {"role_id": role_id, "permission_id": permission_id},
        )


def master_data_permission_codes_for_role(role_code: str) -> frozenset[str]:
    """Expected master_data grants managed by this seed (add-only sync)."""
    entry = BASE_ROLES.get(role_code)
    if entry is None:
        return frozenset()
    return frozenset(entry[2])
