"""Errores internos seguros dentro del middleware CORS."""
import logging
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

class SafeErrorMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False
        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            logger.exception("Error interno al procesar la solicitud")
            if started:
                raise
            response = JSONResponse(status_code=500, content={"detail": "Error del servidor. Intente nuevamente."})
            await response(scope, receive, send)
