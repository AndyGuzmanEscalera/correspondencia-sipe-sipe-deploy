"""Errores de la API biométrica y su clasificación.

El detalle del servidor nunca se devuelve al cliente de RRHH: sólo se usa
internamente. El código de error es estable y distingue el origen del fallo.
"""


class BiometricApiError(RuntimeError):
    """Fallo controlado al hablar con la API biométrica."""

    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code = code
        self.message = message
        # Texto original del servidor, truncado y nunca expuesto al cliente.
        self.detail = (detail or "")[:200]


def error_for_status(status, detail=""):
    """Traduce el código HTTP de la API biométrica a un error de dominio.

    401/403 -> token o configuración incorrecta.
    502     -> la API responde pero el dispositivo no es accesible.
    504     -> la API agotó el tiempo de espera del dispositivo.
    """
    if status in (401, 403):
        return BiometricApiError("biometric_api_unauthorized",
                                 "La API biométrica rechazó el token configurado.", detail)
    if status == 404:
        return BiometricApiError("biometric_api_not_found",
                                 "El recurso solicitado no existe en la API biométrica.", detail)
    if status in (400, 422):
        return BiometricApiError("biometric_api_invalid_request",
                                 "La API biométrica rechazó los parámetros de la consulta.", detail)
    if status == 502:
        return BiometricApiError("biometric_device_unreachable",
                                 "La API biométrica no pudo contactar con el dispositivo.", detail)
    if status == 504:
        return BiometricApiError("biometric_api_timeout",
                                 "La API biométrica agotó el tiempo de espera del dispositivo.", detail)
    if status >= 500:
        return BiometricApiError("biometric_api_error",
                                 "La API biométrica devolvió un error interno.", detail)
    return BiometricApiError("biometric_api_error",
                             "La API biométrica devolvió un error inesperado.", detail)
