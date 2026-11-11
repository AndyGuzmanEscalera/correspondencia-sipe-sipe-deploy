from __future__ import annotations

import uuid
import json
from dataclasses import asdict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.identity.user import User
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.correspondence.creation_policy import (
    configured_creation_ids, INTAKE_UNIT_KEY, INITIAL_RECIPIENTS_KEY,
)
from app.modules.system.constants import INSTITUTIONAL_MAYOR_DISPLAY_NAME, INSTITUTIONAL_MAYOR_EMPLOYEE_ID, INSTITUTIONAL_EMAIL
from app.modules.correspondence.institutional_mayor import resolve_employee_authority, resolve_institutional_mayor, list_active_authorities
from app.modules.system.schemas import (
    InstitutionalSettingsResponse,
    UpdateInstitutionalSettingsRequest,
    AuthorityEmployeeResponse,
)
from app.modules.system.system_setting import SystemSetting


class SystemSettingsService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_institutional_settings(self) -> InstitutionalSettingsResponse:
        row = self._db.get(SystemSetting, INSTITUTIONAL_MAYOR_EMPLOYEE_ID)
        employee_id = None
        mayor = None
        error = None
        try:
            employee_id = uuid.UUID(row.value) if row and row.value else None
            resolved = resolve_institutional_mayor(self._db)
            if resolved:
                mayor = AuthorityEmployeeResponse(**asdict(resolved))
        except HTTPException as exc:
            error = exc.detail
        except ValueError:
            error = "La referencia al Alcalde/Máxima Autoridad Ejecutiva no es válida. Seleccione un funcionario en Configuración."
        try:
            intake_id, recipient_ids = configured_creation_ids(self._db)
        except HTTPException as exc:
            intake_id,recipient_ids=None,[]
            error=error or exc.detail
        intake = self._db.get(OrganizationalUnit, intake_id) if intake_id else None
        recipients = [AuthorityEmployeeResponse(**asdict(item))
                      for item in list_active_authorities(self._db)
                      if item.employee_id in recipient_ids]
        email_row = self._db.get(SystemSetting, INSTITUTIONAL_EMAIL)
        return InstitutionalSettingsResponse(
            institutional_email=email_row.value if email_row else None,
            intake_unit_id=intake_id,
            intake_unit_name=intake.name if intake else None,
            initial_recipient_employee_ids=recipient_ids,
            initial_recipients=recipients,
            mayor_display_name=self.get_mayor_display_name(),
            mayor_employee_id=employee_id,
            mayor=mayor,
            mayor_validation_error=error,
        )

    def list_authority_employees(self) -> list[AuthorityEmployeeResponse]:
        result = []
        for mayor in list_active_authorities(self._db):
            result.append(AuthorityEmployeeResponse(**asdict(mayor)))
        return sorted(result, key=lambda item: item.employee_name.casefold())

    def get_mayor_display_name(self) -> str | None:
        row = self._db.get(SystemSetting, INSTITUTIONAL_MAYOR_DISPLAY_NAME)
        if row is None or row.value is None:
            return None
        trimmed = row.value.strip()
        return trimmed or None

    def update_institutional_settings(
        self,
        user: User,
        body: UpdateInstitutionalSettingsRequest,
    ) -> InstitutionalSettingsResponse:
        # Validate every supplied reference before mutating the configuration.
        if body.intake_unit_id is not None:
            unit = self._db.get(OrganizationalUnit, body.intake_unit_id)
            if unit is None or not unit.is_active:
                raise HTTPException(status_code=422, detail="La Unidad de Ventanilla debe existir y estar activa")
        if body.initial_recipient_employee_ids is not None:
            for employee_id in body.initial_recipient_employee_ids:
                resolve_employee_authority(self._db, employee_id)
        if body.mayor_employee_id is not None:
            resolve_employee_authority(self._db, body.mayor_employee_id)
        for field, key in [("intake_unit_id", INTAKE_UNIT_KEY),
                           ("initial_recipient_employee_ids", INITIAL_RECIPIENTS_KEY)]:
            if field not in body.model_fields_set:
                continue
            value = getattr(body, field)
            serialized = (json.dumps([str(item) for item in dict.fromkeys(value or [])])
                          if field == "initial_recipient_employee_ids"
                          else str(value) if value else None)
            row = self._db.get(SystemSetting, key)
            if row is None:
                self._db.add(SystemSetting(key=key, value=serialized, updated_by_user_id=user.id))
            else:
                row.value, row.updated_by_user_id = serialized, user.id
        if "mayor_employee_id" in body.model_fields_set:
            if body.mayor_employee_id is not None:
                resolve_employee_authority(self._db, body.mayor_employee_id)
            selection = self._db.get(SystemSetting, INSTITUTIONAL_MAYOR_EMPLOYEE_ID)
            if body.mayor_employee_id is None:
                if selection is not None:
                    self._db.delete(selection)
            elif selection is None:
                self._db.add(SystemSetting(key=INSTITUTIONAL_MAYOR_EMPLOYEE_ID,
                    value=str(body.mayor_employee_id), updated_by_user_id=user.id))
            else:
                selection.value = str(body.mayor_employee_id)
                selection.updated_by_user_id = user.id
        if "institutional_email" in body.model_fields_set:
            row = self._db.get(SystemSetting, INSTITUTIONAL_EMAIL)
            if body.institutional_email is None:
                if row is not None:
                    self._db.delete(row)
            elif row is None:
                self._db.add(SystemSetting(key=INSTITUTIONAL_EMAIL,
                    value=body.institutional_email, updated_by_user_id=user.id))
            else:
                row.value = body.institutional_email
                row.updated_by_user_id = user.id
        if "mayor_display_name" not in body.model_fields_set:
            self._db.commit()
            return self.get_institutional_settings()
        normalized = self._normalize_mayor_name(body.mayor_display_name)
        row = self._db.get(SystemSetting, INSTITUTIONAL_MAYOR_DISPLAY_NAME)
        if normalized is None:
            if row is not None:
                self._db.delete(row)
        elif row is None:
            self._db.add(
                SystemSetting(
                    key=INSTITUTIONAL_MAYOR_DISPLAY_NAME,
                    value=normalized,
                    updated_by_user_id=user.id,
                )
            )
        else:
            row.value = normalized
            row.updated_by_user_id = user.id
        self._db.commit()
        return self.get_institutional_settings()

    @staticmethod
    def _normalize_mayor_name(value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None
