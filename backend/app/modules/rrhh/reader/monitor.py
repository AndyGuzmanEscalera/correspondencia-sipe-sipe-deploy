import time
from .connection import DeviceError


def monitor(reader, store, interval, emit=print, stop=None):
    emit("Monitoreo por consultas periódicas; Ctrl+C para volver al menú.")
    emit("La primera consulta incorpora el histórico aún no guardado en la base local.")
    while stop is None or not stop.is_set():
        try:
            records = reader.attendance()
            new = store.add(records)
            emit(f"Consulta completada: {len(records)} descargados, {len(new)} nuevos.")
            for record in new:
                emit(record)
        except DeviceError as exc:
            emit(f"Consulta fallida: {exc} Se volverá a consultar tras el intervalo.")
        if stop is not None:
            stop.wait(interval)
        else:
            time.sleep(interval)
