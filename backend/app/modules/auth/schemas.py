from pydantic import BaseModel, ConfigDict, EmailStr, Field


class MeInstitutionalUnit(BaseModel):
    id: str
    name: str


class MeInstitutionalHierarchy(BaseModel):
    id: str
    code: str
    name: str
    rank: int
    is_active: bool


class MeInstitutionalPosition(BaseModel):
    hierarchy: MeInstitutionalHierarchy | None = None
    id: str
    name: str


class MeEmployeeContext(BaseModel):
    id: str
    full_name: str
    document_number: str | None = None
    position: MeInstitutionalPosition | None = None
    unit: MeInstitutionalUnit | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=255)


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    employee_id: str | None = None
    is_active: bool
    employee: MeEmployeeContext | None = None


class AuthResponse(BaseModel):
    access_token: str
    expires_in: int
    user: UserBrief | None = None


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class MeResponse(UserBrief):
    roles: list[str] = []
    permissions: list[str] = []
