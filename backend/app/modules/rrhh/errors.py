from fastapi import HTTPException


def problem(code, message, status=503):
    return HTTPException(status, detail={"code": code, "message": message})


def device_problem(exc):
    # Never expose exception text, network credentials or stack traces.
    text = str(exc).lower()
    if "autentic" in text or "auth" in text:
        return problem("biometric_auth_rejected", "El dispositivo rechazó la autenticación.")
    if "incomplet" in text:
        return problem("biometric_incomplete_download", "La descarga quedó incompleta; no se guardaron registros.")
    if "timeout" in text or "tiempo límite" in text:
        return problem("biometric_timeout", "Se agotó el tiempo de respuesta del dispositivo.")
    if "protocolo" in text or "interpretable" in text:
        return problem("biometric_protocol_error", "No se pudo completar la consulta del protocolo.")
    return problem("biometric_unreachable", "El dispositivo no está accesible.")
