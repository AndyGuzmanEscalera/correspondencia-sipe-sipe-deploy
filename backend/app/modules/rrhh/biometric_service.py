import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from sqlalchemy import text
from app.modules.rrhh.reader.config import load_config
from app.modules.rrhh.errors import device_problem, problem


def Reader(config):
    # Import only when the connector is requested. Auth/health must still boot
    # if the running image has not installed the biometric requirements yet.
    from app.modules.rrhh.reader.connection import Reader as DeviceReader
    return DeviceReader(config)


class BiometricService:
    def __init__(self, reader=None):
        if reader is not None:
            self.reader = reader
            self.config = reader.config
            return
        try:
            # Use the backend's .env, never the ZIP's .env or a fallback network IP.
            from dotenv import load_dotenv
            load_dotenv(Path(".env"), override=False)
            if not os.getenv("BIOMETRIC_IP", "").strip() or not os.getenv("BIOMETRIC_DEVICE_ID", "").strip():
                raise ValueError("Missing configuration")
            self.config = load_config(Path(".env"))
            if len(self.config.device_id) > 100:
                raise ValueError("Device ID too long")
            self.reader = Reader(self.config)
        except (ValueError, LookupError, OSError):
            raise problem("biometric_invalid_configuration", "Revise la configuración BIOMETRIC del backend.") from None
        except ImportError:
            raise problem("biometric_dependency_missing", "El conector biométrico no está instalado. Actualice las dependencias del backend.") from None

    @contextmanager
    def device_lock(self, engine):
        # Session advisory lock across API/worker processes, released on disconnect/crash.
        # Physical endpoint, so aliases of device_id cannot bypass it.
        key = int.from_bytes(sha256(f"{self.config.ip}:{self.config.port}".encode()).digest()[:8], "big", signed=True)
        with engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            connection.commit()
            if not acquired:
                raise problem("biometric_busy", "Hay otra operación activa para este dispositivo.", 409)
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()

    def call(self, operation):
        from app.modules.rrhh.reader.connection import DeviceError
        try:
            return getattr(self.reader, operation)()
        except DeviceError as exc:
            # Reader wraps low-level errors; traverse only to classify, never serialize.
            cause = exc
            while cause.__cause__ is not None:
                cause = cause.__cause__
            raise device_problem(cause if "incomplet" in str(cause).lower() else exc) from None

    def status(self):
        info = self.call("info")
        return {"connected": True, "device_id": self.config.device_id,
                "model": info.get("nombre_modelo"), "platform": info.get("plataforma"),
                "serial": info.get("serial"), "firmware": info.get("firmware"),
                "users_count": info.get("cantidad_usuarios"), "transport": info.get("transporte")}

    def users(self):
        # API needs only the ID, internal UID and name; omit cards and privileges.
        return [{key: row.get(key) for key in ("user_id", "uid", "name")}
                for row in self.call("users")]

    def download(self):
        return self.call("attendance")
