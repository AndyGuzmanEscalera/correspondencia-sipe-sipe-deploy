import platform
import socket
import subprocess
import warnings
from struct import unpack
from contextlib import contextmanager

from zk import ZK
from zk import const


class DeviceError(RuntimeError):
    pass


def explain_error(exc):
    text = str(exc).lower()
    if "unauth" in text or "auth" in text:
        return "Autenticación rechazada: comprueba la clave de comunicación configurada."
    if "timed out" in text or "timeout" in text:
        return "Sin respuesta dentro del tiempo límite: red, firewall, transporte o protocolo incompatible."
    if "refused" in text or "10061" in text:
        return "Conexión rechazada: puerto TCP cerrado o servicio no disponible."
    if "invalid" in text or "unpack" in text or "supported" in text:
        return "Respuesta no interpretable por pyzk: posible protocolo o formato incompatible."
    return "No se pudo completar la consulta: revisa red, clave, firmware y compatibilidad del protocolo."


def tcp_probe(config):
    try:
        with socket.create_connection((config.ip, config.port), timeout=config.timeout):
            return True, "Puerto TCP accesible; esto todavía no confirma compatibilidad ZKTeco."
    except OSError as exc:
        return False, explain_error(exc)


def ping_probe(config):
    windows = platform.system() == "Windows"
    args = ["ping", "-n", "1", "-w", str(int(config.timeout * 1000)), config.ip] if windows else [
        "ping", "-c", "1", "-W", str(max(1, int(config.timeout))), config.ip]
    try:
        result = subprocess.run(args, capture_output=True, timeout=config.timeout + 2, check=False)
        return "Responde a ICMP." if result.returncode == 0 else "Sin respuesta ICMP (puede estar bloqueado)."
    except (OSError, subprocess.TimeoutExpired):
        return "Prueba ICMP no disponible o agotó su tiempo."


class _BoundedZK(ZK):
    """Adaptación privada para pyzk==0.9: timeout y cierre de sockets temporales."""
    def _ZK__recieve_raw_data(self, size):
        chunks = []
        while size > 0:
            chunk = self._ZK__sock.recv(size)
            if not chunk:
                raise DeviceError("El equipo cerró la conexión durante la descarga; respuesta incompleta.")
            chunks.append(chunk)
            size -= len(chunk)
        return b"".join(chunks)

    def _ZK__send_command(self, command, command_string=b"", response_size=8):
        allowed = {const.CMD_CONNECT, const.CMD_AUTH, const.CMD_EXIT,
                   const.CMD_GET_TIME, const.CMD_GET_VERSION, const.CMD_OPTIONS_RRQ,
                   const.CMD_GET_FREE_SIZES, const.CMD_FREE_DATA, 1503, 1504}
        if command not in allowed:
            raise DeviceError("Comando bloqueado por la política de solo lectura.")
        if command == 1503:
            _, requested, function, _ = unpack("<bhii", command_string)
            if not (requested == const.CMD_ATTLOG_RRQ or
                    (requested == const.CMD_USERTEMP_RRQ and function == const.FCT_USER)):
                raise DeviceError("Solo se permiten usuarios y asistencias; plantillas bloqueadas.")
        return super()._ZK__send_command(command, command_string, response_size)

    def _ZK__create_socket(self):
        # pyzk reemplaza el socket inicial sin cerrarlo.
        self._ZK__sock.close()
        super()._ZK__create_socket()

    def close_socket(self):
        self._ZK__sock.close()


class Reader:
    def __init__(self, config):
        self.config = config
        self.last_transport = None

    @contextmanager
    def session(self, transport=None):
        cfg = self.config
        mode = transport or cfg.transport
        modes = ("tcp", "udp") if mode == "auto" else (mode,)
        errors = []
        client = None
        connection = None
        for candidate in modes:
            if candidate == "tcp":
                ok, message = tcp_probe(cfg)
                if not ok:
                    errors.append("TCP: " + message)
                    continue
            client = _BoundedZK(cfg.ip, port=cfg.port, timeout=cfg.timeout,
                                password=cfg.password, force_udp=candidate == "udp",
                                ommit_ping=True, encoding=cfg.encoding)
            # Ya se probó TCP con timeout configurable. Evitar el timeout fijo de 10s de pyzk.
            client.helper.test_tcp = lambda: 0
            try:
                connection = client.connect()
                self.last_transport = candidate.upper()
                break
            except BaseException as exc:
                client.close_socket()
                if not isinstance(exc, Exception):
                    raise
                errors.append(candidate.upper() + ": " + explain_error(exc))
                if "auth" in str(exc).lower():
                    break  # No repetir autenticación en otro transporte.
        if connection is None:
            raise DeviceError(" / ".join(errors) +
                              " No confirma que el equipo sea incompatible: falta diagnóstico real.")
        try:
            yield connection
        except Exception as exc:
            raise DeviceError(explain_error(exc)) from exc
        finally:
            try:
                connection.disconnect()
            except Exception:
                warnings.warn("No se confirmó el cierre de la sesión; se cerró el socket local.")
            finally:
                client.close_socket()

    def diagnose(self):
        result = {"ip": self.config.ip, "port": self.config.port, "icmp": ping_probe(self.config)}
        result["tcp_port"] = tcp_probe(self.config)[1]
        # UDP se comprueba con una sesión válida, nunca por enviar un datagrama vacío.
        for transport in ("tcp", "udp"):
            try:
                with self.session(transport):
                    result[transport + "_zkteco"] = "Sesión ZKTeco establecida y cerrada."
            except DeviceError as exc:
                result[transport + "_zkteco"] = str(exc)
                if "Autenticación rechazada" in str(exc):
                    result["resto"] = "Pruebas restantes omitidas por rechazo de autenticación."
                    break
        result["nota_udp"] = "Sin respuesta UDP no distingue puerto cerrado, firewall y protocolo incompatible."
        return result

    def info(self):
        result = {}
        with self.session() as conn:
            for label, method in (("nombre_modelo", "get_device_name"), ("plataforma", "get_platform"),
                                  ("serial", "get_serialnumber"), ("firmware", "get_firmware_version"),
                                  ("hora", "get_time")):
                try:
                    value = getattr(conn, method)()
                    result[label] = value.isoformat() if hasattr(value, "isoformat") else value
                except Exception as exc:
                    result[label] = "No disponible: " + explain_error(exc)
            try:
                conn.read_sizes()
                result["cantidad_usuarios"] = conn.users
            except Exception as exc:
                result["cantidad_usuarios"] = "No disponible: " + explain_error(exc)
            result["transporte"] = self.last_transport
        return result

    def users(self):
        from .records import user_record
        with self.session() as conn:
            return [user_record(user) for user in conn.get_users()]

    def attendance(self):
        from .records import attendance_record, filter_records
        with self.session() as conn:
            return filter_records([attendance_record(item, self.config.device_id)
                                   for item in conn.get_attendance()])
