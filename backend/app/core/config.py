from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="correspondencia-api")
    app_version: str = Field(default="0.1.0")
    api_prefix: str = Field(default="")
    public_tracking_hmac_secret: str = Field(default="", repr=False)

    postgres_db: str = Field(default="correspondencia_sipe")
    postgres_user: str = Field(default="sipe_app")
    postgres_password: str = Field(default="")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)

    database_url: str = Field(default="")

    app_env: str = Field(default="development")

    cors_allow_origins: str = Field(default="")
    cors_allow_origin_regex: str = Field(default="")
    cors_allow_credentials: bool = Field(default=True)

    jwt_access_secret: str = Field(default="")
    jwt_issuer: str = Field(default="correspondencia-api")
    jwt_audience: str = Field(default="correspondencia-web")
    access_token_ttl_minutes: int = Field(default=15)

    refresh_token_ttl_days: int = Field(default=7)

    storage_root: Path = Field(default=Path("storage"))
    institutional_logo_path: Path | None = Field(default=None)
    institutional_mayor_position_codes: str = Field(
        default="ALCALDE,ALCALDESA",
        description="Códigos de cargo del titular ejecutivo municipal (PARA / primer destinatario)",
    )
    max_attachment_size_mb: int = Field(default=25)
    attachment_allowed_mime_types: str = Field(
        default=(
            "application/pdf,"
            "application/msword,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.ms-excel,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "image/jpeg,"
            "image/png,"
            "image/webp,"
            "image/gif"
        )
    )

    cookie_name: str = Field(default="refresh_token")
    cookie_path: str = Field(default="/auth")
    cookie_secure: bool | None = Field(default=None)
    cookie_samesite: str = Field(default="lax")

    @property
    def cors_origins_list(self) -> list[str]:
        if self.app_env.lower() == "development":
            return []  # DEV permits loopback only, irrespective of external env overrides.
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def effective_cors_origin_regex(self) -> str | None:
        """DEV is loopback-only; QA/PROD need no CORS with Apache /api."""
        if self.app_env.lower() == "development":
            return r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
        return self.cors_allow_origin_regex.strip() or None

    @field_validator("cookie_secure", mode="before")
    @classmethod
    def _empty_cookie_secure(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def _validate_network_settings(self):
        self.app_env = self.app_env.strip().lower()
        if self.app_env not in {"development", "qa", "production"}:
            raise ValueError("APP_ENV must be development, qa or production")
        if self.cookie_secure is None:
            self.cookie_secure = self.app_env == "production"
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("Production requires HTTPS and COOKIE_SECURE=true")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None requires COOKIE_SECURE=true")
        if self.cors_allow_credentials and "*" in self.cors_origins_list:
            raise ValueError("Wildcard CORS origins cannot be used with credentials")
        return self

    @property
    def max_attachment_size_bytes(self) -> int:
        return self.max_attachment_size_mb * 1024 * 1024

    @property
    def institutional_mayor_position_codes_list(self) -> list[str]:
        return [
            code.strip().upper()
            for code in self.institutional_mayor_position_codes.split(",")
            if code.strip()
        ]

    @property
    def attachment_allowed_mime_types_list(self) -> list[str]:
        return [
            item.strip().lower()
            for item in self.attachment_allowed_mime_types.split(",")
            if item.strip()
        ]

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @field_validator("cookie_samesite")
    @classmethod
    def _validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError(
                "cookie_samesite must be one of: lax, strict, none"
            )
        return normalized

    @field_validator("jwt_access_secret")
    @classmethod
    def _validate_jwt_access_secret(cls, value: str) -> str:
        # Reject empty / whitespace-only / too-short secrets at startup.
        # HS256 requires entropy equivalent to 256 bits, ~32 ASCII chars minimum.
        if not value or not value.strip():
            raise ValueError(
                "JWT_ACCESS_SECRET is required: provide a non-empty value via .env or environment"
            )
        if len(value) < 32:
            raise ValueError(
                "JWT_ACCESS_SECRET must be at least 32 characters long (HS256)"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
