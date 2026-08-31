import jwt
import os
from datetime import datetime, timedelta, timezone

SECRET = os.getenv("JWT_SECRET")


def create_access_token(user_id: str, auth_methods: list[str]) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "auth_methods": auth_methods
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def decode_access_token(token: str) -> str:
    """Raises jwt.PyJWTError (expired/malformed/bad signature) on failure."""
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    methods = set(payload.get("auth_methods", []))
    # otp is mandatory; the other factor is either a successful voice verify,
    # or the email-recovery path used when voice stops working for the owner.
    if "otp" not in methods or not ({"voice", "recovery"} & methods):
        raise jwt.InvalidTokenError("token was not issued after a full auth flow")
    return payload["sub"]
