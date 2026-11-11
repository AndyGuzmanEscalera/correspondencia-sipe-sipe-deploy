import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationalUnitAdminResponse(BaseModel):
    id: uuid.UUID
    code: str | None
    name: str
    description: str | None
    parent_id: uuid.UUID | None
    parent_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None


class CreateOrganizationalUnitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=300)
    parent_id: uuid.UUID | None = None


class UpdateOrganizationalUnitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=300)
    parent_id: uuid.UUID | None = None


class PositionAdminResponse(BaseModel):
    hierarchy_id: uuid.UUID | None = None
    hierarchy_code: str | None = None
    hierarchy_name: str | None = None
    id: uuid.UUID
    code: str | None
    name: str
    description: str | None
    can_view_unit_correspondence: bool = False
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None


class CreatePositionRequest(BaseModel):
    hierarchy_id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=255)
    can_view_unit_correspondence: bool = False


class UpdatePositionRequest(BaseModel):
    hierarchy_id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=255)
    can_view_unit_correspondence: bool = False


class EmployeeAdminResponse(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    document_number: str | None
    email: str | None
    phone: str | None
    unit_id: uuid.UUID | None
    unit_name: str | None
    position_id: uuid.UUID | None
    position_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None


class CreateEmployeeRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    document_number: str = Field(min_length=1, max_length=30)
    email: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    unit_id: uuid.UUID
    position_id: uuid.UUID


class UpdateEmployeeRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    document_number: str = Field(min_length=1, max_length=30)
    email: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    unit_id: uuid.UUID
    position_id: uuid.UUID
