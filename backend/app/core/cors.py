"""Environment-specific browser access policy, shared by app and contract tests."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings


def configure_cors(app: FastAPI, settings: Settings) -> None:
    origins = settings.cors_origins_list
    origin_regex = settings.effective_cors_origin_regex
    if not (origins or origin_regex):
        return  # Same-origin QA/production does not need CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_credentials=(
            True if settings.app_env == "development" else settings.cors_allow_credentials
        ),
        allow_methods=["*"],
        allow_headers=["*"],
    )
