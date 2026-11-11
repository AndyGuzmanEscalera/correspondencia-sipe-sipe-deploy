"""Content validation for a route sheet; never authorizes its selected origin."""

from fastapi import HTTPException


def validate_route_sheet(body):
    external = (body.sender_name or "").strip()
    if bool(body.origin_employee_id) == bool(external):
        raise HTTPException(
            422, "Seleccione exactamente un DE: funcionario o nombre libre"
        )
    if not (body.reference or "").strip():
        raise HTTPException(422, "La referencia es obligatoria para Hoja de Ruta")
    if not (body.description or "").strip():
        raise HTTPException(422, "El contenido es obligatorio para Hoja de Ruta")
