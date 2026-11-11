import uuid
import re
from pydantic import BaseModel, Field, field_validator


class AuthorityEmployeeResponse(BaseModel):
    employee_id: uuid.UUID
    user_id: uuid.UUID
    unit_id: uuid.UUID
    employee_name: str
    position_name: str
    unit_name: str
    document_number: str | None = None


class InstitutionalSettingsResponse(BaseModel):
    institutional_email: str | None = None
    intake_unit_id: uuid.UUID | None = None
    intake_unit_name: str | None = None
    initial_recipient_employee_ids: list[uuid.UUID] = Field(default_factory=list)
    initial_recipients: list[AuthorityEmployeeResponse] = Field(default_factory=list)
    mayor_display_name: str | None = None
    mayor_employee_id: uuid.UUID | None = None
    mayor: AuthorityEmployeeResponse | None = None
    mayor_validation_error: str | None = None


class UpdateInstitutionalSettingsRequest(BaseModel):
    institutional_email: str | None = Field(default=None, max_length=254)

    @field_validator("institutional_email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("institutional_email")
    @classmethod
    def validate_email(cls, value):
        if value is None:
            return None
        local, separator, domain = value.rpartition("@")
        if (not separator or len(local) > 64 or not re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*", local
        ) or not re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", domain
        )):
            raise ValueError("Ingrese un correo institucional válido")
        return value

    intake_unit_id: uuid.UUID | None = None
    initial_recipient_employee_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    mayor_employee_id: uuid.UUID | None = Field(default=None, description="Referencia al funcionario autoridad institucional. Null elimina la selección; omitir conserva la selección.")
    mayor_display_name: str | None = Field(
        default=None,
        max_length=200,
        description="Texto de la máxima autoridad para documentos nuevos; no cambia el funcionario destinatario EDIE. Vacío elimina el texto personalizado.",
    )
