"""Institutional presentation timezone. Never changes persisted UTC instants."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BOLIVIA = ZoneInfo("America/La_Paz")


def institutional_datetime(value: datetime) -> datetime:
    # Historical naive instants are interpreted as UTC, matching DB/API policy.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BOLIVIA)
