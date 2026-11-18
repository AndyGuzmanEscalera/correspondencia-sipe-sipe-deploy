"""Cliente HTTPS de la API biométrica publicada.

Sustituye la conexión directa al reloj (pyzk/TCP 4370). El backend RRHH es el
único consumidor de esta capa: Flutter nunca habla con la API biométrica.
"""
