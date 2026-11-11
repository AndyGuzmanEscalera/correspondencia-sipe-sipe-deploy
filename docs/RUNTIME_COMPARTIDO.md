# Runtime compartido que se conserva

No se modificó funcionalidad RRHH. app/main.py importa y registra rrhh.router;
su cadena incluye modelos, esquemas, repositorios de asistencia, evaluación,
fuentes staging/turnos y lector biométrico (algunos imports son diferidos).
Se conserva el paquete completo como unidad runtime para no podar dependencias
dinámicas. No se ejecutan tests RRHH, worker, seeds ni lecturas de dispositivos.

alembic/env.py importa rrhh.models. La revisión h1b2c3d4e5f6 es una rama que m1
fusiona con l1; n1 depende de m1. Se copian las 24 revisiones sin reescribirlas.
requirements.txt incluye requirements-biometric.txt: pyzk 0.9,
python-dotenv 1.2.1 y future 1.0.0, necesarios para el runtime compartido.

Archivos Python RRHH conservados, idénticos por SHA-256 al desarrollo:

- backend/app/modules/rrhh/__init__.py
- backend/app/modules/rrhh/attendance_repository.py
- backend/app/modules/rrhh/attendance_service.py
- backend/app/modules/rrhh/attendance_source.py
- backend/app/modules/rrhh/biometric_service.py
- backend/app/modules/rrhh/errors.py
- backend/app/modules/rrhh/evaluation.py
- backend/app/modules/rrhh/evaluation_models.py
- backend/app/modules/rrhh/models.py
- backend/app/modules/rrhh/periods.py
- backend/app/modules/rrhh/reader/__init__.py
- backend/app/modules/rrhh/reader/config.py
- backend/app/modules/rrhh/reader/connection.py
- backend/app/modules/rrhh/reader/monitor.py
- backend/app/modules/rrhh/reader/records.py
- backend/app/modules/rrhh/reader/storage.py
- backend/app/modules/rrhh/router.py
- backend/app/modules/rrhh/schemas.py
- backend/app/modules/rrhh/shift_source.py
- backend/app/modules/rrhh/staging_check.py
- backend/app/modules/rrhh/staging_source.py
- backend/app/modules/rrhh/storage.py
- backend/app/modules/rrhh/worker.py

El entrypoint es Uvicorn app.main:app. No inicia app.modules.rrhh.worker.
