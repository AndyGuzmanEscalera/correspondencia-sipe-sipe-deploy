"""Validaciones de baja lógica: dependencias vigentes, nunca referencias históricas."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.correspondence.correspondence import Correspondence
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position
from app.modules.system.constants import INSTITUTIONAL_MAYOR_EMPLOYEE_ID
from app.modules.system.system_setting import SystemSetting


def _blocked(message):
    raise HTTPException(status_code=409, detail=message)


def _has_active_correspondence(db, condition):
    return db.scalar(select(Correspondence.id).where(
        Correspondence.is_active.is_(True), Correspondence.status == 'ACTIVE', condition,
    ).limit(1)) is not None


def _configured_employee(db):
    setting = db.get(SystemSetting, INSTITUTIONAL_MAYOR_EMPLOYEE_ID)
    try:
        return uuid.UUID(setting.value) if setting and setting.value else None
    except ValueError:
        return None


def validate_deletion(db: Session, entity: str, row):
    """No cambia datos: el servicio aplica la baja después de validar."""
    from app.modules.correspondence.creation_policy import configured_creation_ids
    intake,receivers=configured_creation_ids(db)
    if entity=='units' and row.id==intake:
        _blocked('No se puede eliminar la Unidad de Ventanilla configurada. Cambie primero la configuración institucional.')
    if entity=='employees' and row.id in receivers or entity=='users' and row.employee_id in receivers:
        _blocked('No se puede eliminar un receptor inicial configurado. Cambie primero la configuración institucional.')
    if entity in {'units','positions'}:
        # Leer sólo la dependencia necesaria, sin cargar campos ajenos del funcionario.
        reference = Employee.unit_id if entity == 'units' else Employee.position_id
        configured_dependency = db.scalar(select(Employee.id).where(
            Employee.id.in_(receivers), reference == row.id,
        ).limit(1))
        if configured_dependency is not None:
            _blocked('No se puede eliminar este registro porque lo utiliza un receptor inicial configurado. Cambie primero la configuración institucional.')
    if entity == 'units':
        if _has_active_correspondence(db, Correspondence.current_unit_id == row.id):
            _blocked('No se puede eliminar esta unidad porque tiene correspondencia activa bajo su responsabilidad.')
        if db.scalar(select(Employee.id).where(Employee.unit_id == row.id, Employee.is_active.is_(True)).limit(1)):
            _blocked('No se puede eliminar esta unidad porque tiene funcionarios activos asignados.')
        if db.scalar(select(OrganizationalUnit.id).where(OrganizationalUnit.parent_id == row.id,
                                                       OrganizationalUnit.is_active.is_(True)).limit(1)):
            _blocked('No se puede eliminar esta unidad porque tiene unidades dependientes activas.')
    elif entity == 'positions':
        if db.scalar(select(Employee.id).where(Employee.position_id == row.id, Employee.is_active.is_(True)).limit(1)):
            _blocked('No se puede eliminar este cargo porque tiene funcionarios activos asignados.')
    elif entity == 'employees':
        users = select(User.id).where(User.employee_id == row.id)
        if _has_active_correspondence(db, Correspondence.current_user_id.in_(users)):
            _blocked('No se puede eliminar este funcionario porque tiene correspondencia activa asignada.')
        if _configured_employee(db) == row.id:
            _blocked('No se puede eliminar la autoridad institucional configurada. Cambie primero la configuración.')
        if db.scalar(select(User.id).where(User.employee_id == row.id, User.is_active.is_(True)).limit(1)):
            _blocked('No se puede eliminar este funcionario porque tiene una cuenta de usuario activa vinculada.')
    elif entity == 'users':
        if _has_active_correspondence(db, Correspondence.current_user_id == row.id):
            _blocked('No se puede eliminar este usuario porque tiene correspondencia activa asignada.')
        if row.employee_id is not None and _configured_employee(db) == row.employee_id:
            _blocked('No se puede eliminar el usuario de la autoridad institucional configurada. Cambie primero la configuración.')
    # Tipos documentales: las operaciones existentes resuelven la fila por ID,
    # incluso eliminada. La baja solo impide registrar documentos nuevos.

    # Proteger también la configuración persistida aunque el funcionario esté eliminado.
    if entity in {'units', 'positions'}:
        authority_id = _configured_employee(db)
        reference = Employee.unit_id if entity == 'units' else Employee.position_id
        if authority_id is not None and db.scalar(select(Employee.id).where(
            Employee.id == authority_id, reference == row.id,
        ).limit(1)) is not None:
            _blocked('No se puede eliminar este registro porque lo utiliza la autoridad institucional configurada. Cambie primero la configuración.')



def validate_employee_restoration(db: Session, employee: Employee):
    unit = db.get(OrganizationalUnit, employee.unit_id) if employee.unit_id else None
    position = db.get(Position, employee.position_id) if employee.position_id else None
    if not unit or not unit.is_active or not position or not position.is_active:
        _blocked('No se puede restaurar: primero restaure o cambie la unidad y el cargo del funcionario.')
