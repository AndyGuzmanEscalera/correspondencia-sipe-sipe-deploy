"""Cliente HTTP de la API biométrica.

Encapsula la base URL, el token Bearer, el timeout, el manejo de errores y la
paginación. Ningún otro módulo del RRHH abre conexiones HTTP por su cuenta.
"""
import httpx

from app.modules.rrhh.api_client.errors import BiometricApiError, error_for_status

# Tamaño de página por defecto. Coincide con el límite por defecto de la API,
# de modo que funciona incluso con un ATTENDANCE_MAX_LIMIT menor en el servidor.
DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000
# Tope de páginas por descarga: evita bucles infinitos ante respuestas inconsistentes.
MAX_PAGES = 2000


class BiometricApiClient:
    """Cliente de solo lectura de la API biométrica."""

    def __init__(self, config, transport=None):
        self.config = config
        # `transport` permite sustituir la red por un doble en las pruebas.
        self._transport = transport

    # -- transporte ---------------------------------------------------------
    def _request(self, path, params=None):
        headers = {"Authorization": "Bearer " + self.config.token, "Accept": "application/json"}
        try:
            with httpx.Client(base_url=self.config.base_url, timeout=self.config.timeout,
                              headers=headers, transport=self._transport, follow_redirects=True) as client:
                response = client.get(path, params=params)
        except httpx.TimeoutException:
            raise BiometricApiError("biometric_api_timeout",
                                    "La API biométrica no respondió dentro del tiempo configurado.") from None
        except httpx.HTTPError:
            # DNS, TLS, Cloudflare o red: el servicio externo no está disponible.
            raise BiometricApiError("biometric_api_unreachable",
                                    "No fue posible contactar la API biométrica.") from None
        if response.status_code >= 400:
            raise error_for_status(response.status_code, _detail(response))
        try:
            return response.json()
        except ValueError:
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió una respuesta no interpretable.") from None

    # -- endpoints ----------------------------------------------------------
    def health(self):
        """Estado del servicio; no requiere token."""
        payload = self._request("/health")
        if not isinstance(payload, dict):
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió un estado no interpretable.")
        return payload

    def get_device(self):
        """Información del dispositivo disponible en modo lectura."""
        payload = self._request("/device")
        if not isinstance(payload, dict):
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió un dispositivo no interpretable.")
        return self._check_device(payload)

    def get_users(self):
        """Usuarios registrados en el dispositivo."""
        payload = self._request("/users")
        users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(users, list):
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió un listado de usuarios no interpretable.")
        self._check_device(payload)
        return users

    def get_attendance(self, *, date=None, from_date=None, to_date=None, ci=None,
                       limit=DEFAULT_PAGE_SIZE, offset=0):
        """Una página de marcaciones con los filtros indicados."""
        params = {"limit": limit, "offset": offset}
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if ci:
            params["ci"] = str(ci)
        payload = self._request("/attendance", params=params)
        if not isinstance(payload, dict):
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió una página no interpretable.")
        return self._check_device(payload)

    def iter_attendance(self, *, page_size=DEFAULT_PAGE_SIZE, **filters):
        """Recorre todas las páginas de un filtro y devuelve cada marcación.

        La paginación se guía por `total`, `count`, `limit` y `offset`. Si la
        respuesta es inconsistente se detiene el recorrido con un error controlado
        en lugar de acumular registros indefinidamente.
        """
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        offset = 0
        pages = 0
        attempts = 0
        while True:
            try:
                payload = self.get_attendance(limit=size, offset=offset, **filters)
            except BiometricApiError as exc:
                # El servidor puede admitir un límite menor que el configurado aquí.
                if (exc.code == "biometric_api_invalid_request" and "limit" in exc.detail
                        and size > 1 and attempts < 3):
                    size = max(1, size // 2)
                    attempts += 1
                    continue
                raise
            records, count, total = _validate_page(payload)
            for record in records:
                yield record
            offset += count
            pages += 1
            if count <= 0:
                return
            if offset >= total:
                return
            if count < size:
                # Página parcial: no hay más registros que pedir.
                return
            if pages >= MAX_PAGES:
                raise BiometricApiError("biometric_api_pagination_limit",
                                        "La API biométrica paginó demasiadas páginas; revise el rango solicitado.")

    def download_attendance(self, *, page_size=DEFAULT_PAGE_SIZE, **filters):
        """Todas las marcaciones del filtro, en una sola lista."""
        return list(self.iter_attendance(page_size=page_size, **filters))

    # -- utilidades ---------------------------------------------------------
    def _check_device(self, payload):
        """El identificador de la API debe coincidir con el configurado en RRHH.

        Si difieren, el `record_id` calculado en ambos lados no coincidiría y la
        deduplicación de marcaciones dejaría de funcionar.
        """
        remote = payload.get("device_id")
        if remote and remote != self.config.device_id:
            raise BiometricApiError("biometric_device_id_mismatch",
                                    "La API biométrica informa de otro dispositivo; revise BIOMETRIC_DEVICE_ID.")
        return payload


def _validate_page(payload):
    """Valida total/count/limit/offset y devuelve (records, count, total)."""
    for key in ("records", "count", "total", "limit", "offset"):
        if key not in payload:
            raise BiometricApiError("biometric_api_invalid_response",
                                    "La API biométrica devolvió una página incompleta.")
    records = payload["records"]
    if not isinstance(records, list):
        raise BiometricApiError("biometric_api_invalid_response",
                                "La API biométrica devolvió registros no interpretables.")
    count = payload["count"]
    total = payload["total"]
    if not isinstance(count, int) or not isinstance(total, int):
        raise BiometricApiError("biometric_api_invalid_response",
                                "La API biométrica devolvió totales no numéricos.")
    if count != len(records):
        raise BiometricApiError("biometric_api_invalid_response",
                                "La API biométrica devolvió un conteo inconsistente.")
    if total < count or count < 0 or total < 0:
        raise BiometricApiError("biometric_api_invalid_response",
                                "La API biométrica devolvió totales inconsistentes.")
    return records, count, total


def _detail(response):
    """Texto de error del servidor, truncado y sin uso fuera del cliente."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return ""
