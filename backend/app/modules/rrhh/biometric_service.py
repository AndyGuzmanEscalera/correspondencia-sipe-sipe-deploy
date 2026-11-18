"""Servicio biométrico del RRHH.

Consume la API biométrica publicada por HTTPS. La conexión directa al reloj
(pyzk/TCP 4370) queda fuera del camino de ejecución: el backend ya no abre
sockets contra el dispositivo.

El bloqueo por sesión sigue serializando las descargas entre la API y el worker,
ahora contra el endpoint de la API biométrica en lugar del par IP/puerto.
"""
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from sqlalchemy import text

from app.modules.rrhh.api_client.errors import BiometricApiError
from app.modules.rrhh.errors import problem


def ApiClient(config):
    # Import only when the connector is requested. Auth/health must still boot
    # if the running image has not installed the biometric requirements yet.
    from app.modules.rrhh.api_client.client import BiometricApiClient
    return BiometricApiClient(config)


class BiometricService:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
            self.config = client.config
            return
        try:
            # Use the backend's .env, never a fallback network IP or default token.
            from dotenv import load_dotenv
            load_dotenv(Path(".env"), override=False)
            if not os.getenv("BIOMETRIC_API_URL", "").strip() or not os.getenv("BIOMETRIC_API_TOKEN", "").strip():
                raise ValueError("Missing configuration")
            from app.modules.rrhh.api_client.config import load_config
            self.config = load_config(Path(".env"))
            if len(self.config.device_id) > 100:
                raise ValueError("Device ID too long")
            self.client = ApiClient(self.config)
        except (ValueError, LookupError, OSError):
            raise problem("biometric_invalid_configuration",
                          "Revise la configuración BIOMETRIC_API del backend.") from None
        except ImportError:
            raise problem("biometric_dependency_missing",
                          "El cliente HTTP de la API biométrica no está instalado. "
                          "Actualice las dependencias del backend.") from None

    @contextmanager
    def device_lock(self, engine):
        # Session advisory lock across API/worker processes, released on disconnect/crash.
        # The shared endpoint is now the biometric API, so the lock follows its URL.
        key = int.from_bytes(sha256(f"{self.config.url}".encode()).digest()[:8], "big", signed=True)
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
        """Ejecuta una operación del cliente y traduce sus fallos a errores RRHH."""
        try:
            return operation()
        except BiometricApiError as exc:
            # Nunca se serializa el detalle del servidor ni el texto de la excepción.
            raise problem(exc.code, exc.message) from None

    def status(self):
        """Distingue API disponible de dispositivo conectado.

        `api_available` indica que la API biométrica responde; `device_connected`
        indica que el equipo biométrico está accesible desde ella.
        """
        health = self.call(self.client.health)
        api_available = health.get("status") == "ok"
        try:
            device = self.client.get_device()
        except BiometricApiError as exc:
            if exc.code == "biometric_device_unreachable":
                # La API responde pero el dispositivo no: no es un fallo de la API.
                return {"connected": False, "api_available": True, "device_connected": False,
                        "device_id": self.config.device_id, "model": None, "platform": None,
                        "serial": None, "firmware": None, "users_count": None, "transport": None}
            raise problem(exc.code, exc.message) from None
        connected = bool(device.get("connected"))
        return {"connected": connected, "api_available": api_available, "device_connected": connected,
                "device_id": self.config.device_id, "model": device.get("device_name"),
                "platform": device.get("platform"), "serial": device.get("serial_number"),
                "firmware": device.get("firmware_version"), "users_count": None,
                "transport": device.get("transport")}

    def users(self):
        # API needs only the ID, internal UID and name; omit cards and privileges.
        rows = self.call(self.client.get_users)
        return [{key: row.get(key) for key in ("user_id", "uid", "name")} for row in rows]

    def download(self, start_date=None, end_date=None, ci=None):
        """Descarga las marcaciones del período solicitado.

        La API biométrica filtra en el servidor: sólo se pide el rango indicado,
        con `from`/`to` cuando hay período y `ci` cuando hay un identificador.
        """
        filters = {}
        if start_date and end_date:
            filters["from_date"] = start_date.isoformat()
            filters["to_date"] = end_date.isoformat()
        elif start_date:
            filters["date"] = start_date.isoformat()
        if ci:
            filters["ci"] = str(ci)
        return self.call(lambda: self.client.download_attendance(**filters))
