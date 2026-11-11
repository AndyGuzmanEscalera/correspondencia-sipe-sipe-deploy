import uuid

from pydantic import BaseModel


class OrganizationalUnitResponse(BaseModel):
    id: uuid.UUID
    code: str | None
    name: str


class UnitUserResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
