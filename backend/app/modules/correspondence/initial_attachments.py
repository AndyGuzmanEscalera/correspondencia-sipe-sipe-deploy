"""Short-lived, file-bound capability for uploads immediately after creation."""
import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException
from app.core.config import get_settings

ALLOWED_BEHAVIORS = {"ROUTE_SHEET", "INTERNAL_COMMUNICATION", "REPORT", "MEMORANDUM", "CIRCULAR"}
TTL_SECONDS = 1800

def issue_grant(user_id, correspondence_id, files):
    payload = json.dumps({"purpose": "initial-attachments", "user": str(user_id),
        "document": str(correspondence_id), "expires": int(time.time()) + TTL_SECONDS,
        "files": files}, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode()
    signature = hmac.new(get_settings().jwt_access_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature

def verify_grant(token, user_id, correspondence_id, filename, payload):
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(get_settings().jwt_access_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        data = json.loads(base64.urlsafe_b64decode(encoded))
        entry = {"filename": filename, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        if (data["purpose"] != "initial-attachments" or data["user"] != str(user_id)
            or data["document"] != str(correspondence_id) or data["expires"] <= time.time()
            or entry not in data["files"]):
            raise ValueError("scope")
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(status_code=403, detail="Autorización inicial de adjuntos inválida o vencida") from None
