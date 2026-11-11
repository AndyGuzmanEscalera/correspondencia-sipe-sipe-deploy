import uuid
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DateRange(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_date.year >= 9999:
            raise ValueError("Fecha fuera del rango admitido.")
        if self.start_date > self.end_date:
            raise ValueError("La fecha inicial debe ser anterior o igual a la final.")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("El rango máximo es 367 días inclusivos.")
        return self


class ParametersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    morning_entry_tolerance: int = Field(ge=0, le=86400, strict=True)
    morning_exit_tolerance: int = Field(ge=0, le=86400, strict=True)
    afternoon_entry_tolerance: int = Field(ge=0, le=86400, strict=True)
    afternoon_exit_tolerance: int = Field(ge=0, le=86400, strict=True)
    minimum_return_interval: int = Field(ge=0, le=86400, strict=True)
    duplicate_window: int = Field(ge=0, le=86400, strict=True)


class NormalizedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=100)
    timestamp: datetime
    status: int | None = Field(default=None, strict=True, ge=-2147483648, le=2147483647)
    punch: int | None = Field(default=None, strict=True, ge=-2147483648, le=2147483647)
    uid: int | None = Field(default=None, strict=True, ge=-2147483648, le=2147483647)

    @field_validator("timestamp")
    @classmethod
    def device_time(cls, value):
        if value.tzinfo is not None or value.microsecond:
            raise ValueError("Se requiere hora local sin zona y con precisión de segundos.")
        return value


class FileImport(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)
    records: list[NormalizedRecord] = Field(min_length=1, max_length=10000)


class LinkInput(BaseModel):
    funcionario_id: uuid.UUID


class GenerateInput(BaseModel):
    contract_type_id: uuid.UUID
    year: int = Field(ge=1901, le=9998)
    month: int = Field(ge=1, le=12)
