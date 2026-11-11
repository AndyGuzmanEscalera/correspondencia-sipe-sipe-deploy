import codecs
import ipaddress
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    ip: str
    port: int = 4370
    password: int = field(default=0, repr=False)
    timeout: float = 10
    transport: str = "auto"
    poll_interval: float = 30
    encoding: str = "UTF-8"
    device_id: str = "reloj-01"

    def __post_init__(self):
        if ipaddress.ip_address(self.ip).version != 4:
            raise ValueError("BIOMETRIC_IP debe ser una dirección IPv4.")
        if not 1 <= self.port <= 65535:
            raise ValueError("BIOMETRIC_PORT debe estar entre 1 y 65535.")
        if not 0 <= self.password <= 0xFFFFFFFF:
            raise ValueError("BIOMETRIC_PASSWORD debe ser un entero entre 0 y 4294967295.")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise ValueError("BIOMETRIC_TIMEOUT debe ser mayor que 0 y como máximo 300 segundos.")
        if not math.isfinite(self.poll_interval) or self.poll_interval < 1:
            raise ValueError("BIOMETRIC_POLL_INTERVAL debe ser al menos 1 segundo.")
        if self.transport not in {"auto", "tcp", "udp"}:
            raise ValueError("BIOMETRIC_TRANSPORT debe ser auto, tcp o udp.")
        if not self.device_id.strip():
            raise ValueError("BIOMETRIC_DEVICE_ID no puede estar vacío.")
        codecs.lookup(self.encoding)


def load_config(path=ROOT / ".env"):
    load_dotenv(path, override=False)
    return Config(
        ip=os.getenv("BIOMETRIC_IP", "192.168.1.201").strip(),
        port=int(os.getenv("BIOMETRIC_PORT", "4370")),
        password=int(os.getenv("BIOMETRIC_PASSWORD", "0")),
        timeout=float(os.getenv("BIOMETRIC_TIMEOUT", "10")),
        transport=os.getenv("BIOMETRIC_TRANSPORT", "auto").lower().strip(),
        poll_interval=float(os.getenv("BIOMETRIC_POLL_INTERVAL", "30")),
        encoding=os.getenv("BIOMETRIC_ENCODING", "UTF-8"),
        device_id=os.getenv("BIOMETRIC_DEVICE_ID", "reloj-01").strip(),
    )
