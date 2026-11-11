"""Implementación local del storage root configurable."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from app.core.storage.storage_service import validate_storage_key


class LocalFilesystemStorage:
    """Persistencia en disco bajo un directorio raíz."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, key: str) -> Path:
        safe_key = validate_storage_key(key)
        candidate = (self._root / safe_key).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise ValueError("La clave de almacenamiento escapa del storage root")
        return candidate

    def put(self, key: str, data: bytes) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read(self, key: str) -> bytes:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(key)
        return target.read_bytes()

    def open(self, key: str) -> BinaryIO:
        return BytesIO(self.read(key))

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if not target.is_file():
            return False
        target.unlink()
        return True
