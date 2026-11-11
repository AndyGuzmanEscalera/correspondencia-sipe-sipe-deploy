from app.modules.auth.models import AuthSession
from app.modules import identity, organization  # noqa: F401, E402  -- ensures FK target tables are registered

__all__ = ["AuthSession"]
