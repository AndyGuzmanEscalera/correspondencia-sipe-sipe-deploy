"""Abstracción transversal de almacenamiento de archivos."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import BinaryIO, Protocol


class StorageService(Protocol):
    """Contrato mínimo para backends de almacenamiento (local, S3, MinIO)."""

    def put(self, key: str, data: bytes) -> None:
        """Persiste bytes bajo una clave relativa."""

    def read(self, key: str) -> bytes:
        """Lee el contenido completo de una clave."""

    def open(self, key: str) -> BinaryIO:
        """Abre un stream de lectura binaria."""

    def exists(self, key: str) -> bool:
        """Indica si la clave existe."""

    def delete(self, key: str) -> bool:
        """Elimina la clave. Retorna False si no existía."""


def validate_storage_key(key: str) -> str:
    """Normaliza y valida una clave relativa dentro del storage root."""
    normalized = key.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("La clave de almacenamiento no puede estar vacía")
    if normalized.startswith("/"):
        raise ValueError("La clave de almacenamiento no puede ser absoluta")
    if PurePosixPath(normalized).is_absolute():
        raise ValueError("La clave de almacenamiento no puede ser absoluta")

    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise ValueError("La clave de almacenamiento no puede contener '..'")

    return normalized
