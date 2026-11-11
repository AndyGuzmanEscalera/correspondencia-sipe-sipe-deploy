from app.core.storage.local_filesystem_storage import LocalFilesystemStorage
from app.core.storage.storage_service import StorageService, validate_storage_key

__all__ = [
    "LocalFilesystemStorage",
    "StorageService",
    "get_storage_service",
    "validate_storage_key",
]


def get_storage_service() -> StorageService:
    from app.core.config import get_settings

    return LocalFilesystemStorage(get_settings().storage_root)
