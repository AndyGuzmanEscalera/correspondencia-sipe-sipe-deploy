"""Configuración del cliente de la API biométrica.

Variables de entorno:
  BIOMETRIC_API_URL      URL base HTTPS de la API (obligatoria).
  BIOMETRIC_API_TOKEN    Token Bearer (obligatorio, nunca se versiona).
  BIOMETRIC_API_TIMEOUT  Timeout de la llamada HTTP en segundos (por defecto 60).
  BIOMETRIC_DEVICE_ID    Identificador del dispositivo; debe coincidir con el
                         que informa la API para no romper la deduplicación.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class ApiConfig:
    url: str
    token: str = field(default="", repr=False)
    timeout: float = 60.0
    device_id: str = "alcaldia-central"

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("BIOMETRIC_API_URL debe ser una URL http/https válida.")
        if parsed.query or parsed.fragment:
            raise ValueError("BIOMETRIC_API_URL no admite query ni fragmento.")
        if not self.token.strip():
            raise ValueError("BIOMETRIC_API_TOKEN es obligatorio.")
        if not 0 < self.timeout <= 600:
            raise ValueError("BIOMETRIC_API_TIMEOUT debe estar entre 1 y 600 segundos.")
        if not self.device_id.strip():
            raise ValueError("BIOMETRIC_DEVICE_ID no puede estar vacío.")
        if len(self.device_id) > 100:
            raise ValueError("BIOMETRIC_DEVICE_ID no puede superar 100 caracteres.")

    @property
    def base_url(self):
        return self.url.rstrip("/")

    @property
    def host(self):
        return urlsplit(self.url).hostname or ""

    @property
    def ip(self):
        """Host del endpoint biométrico.

        La tabla `biometric_devices.ip` es obligatoria y describe el endpoint
        que sirve el dispositivo; ahora ese endpoint es la API biométrica.
        """
        return self.host

    @property
    def port(self):
        parsed = urlsplit(self.url)
        if parsed.port:
            return parsed.port
        return 443 if parsed.scheme == "https" else 80


def load_config(path=Path(".env")):
    """Lee la configuración del entorno sin aplicarla sobre variables ya fijadas."""
    load_dotenv(path, override=False)
    return ApiConfig(
        url=os.getenv("BIOMETRIC_API_URL", "").strip(),
        token=os.getenv("BIOMETRIC_API_TOKEN", "").strip(),
        timeout=float(os.getenv("BIOMETRIC_API_TIMEOUT", "60")),
        device_id=os.getenv("BIOMETRIC_DEVICE_ID", "").strip(),
    )
