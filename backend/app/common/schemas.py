import math
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int


def paginate(
    *,
    items: list[T],
    total: int,
    page: int,
    page_size: int,
) -> PaginatedResponse[T]:
    total_pages = max(1, math.ceil(total / page_size)) if total else 0
    return PaginatedResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


class ActiveToggleRequest(BaseModel):
    is_active: bool = Field(description="Estado activo/inactivo")
