import jwt
import os
from datetime import datetime, timedelta, timezone

# The signing key must be a real secret. Without this check a missing variable only
# fails later, at the first login, and a copy of .env.example that keeps its
# placeholder would quietly sign every access token with a string that is public in
# the repository. Refusing to start turns both into an obvious startup error.
PLACEHOLDER_SECRET = "replace-with-a-random-secret"  # the value shipped in .env.example
# RFC 7518 section 3.2: an HS256 key must be at least as long as the hash output, 32 bytes.
# The suggested token_urlsafe(48) gives 64.
MIN_SECRET_BYTES = 32


def _load_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret or secret == PLACEHOLDER_SECRET or len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise RuntimeError(
            "JWT_SECRET is missing, still the .env.example placeholder, or shorter than "
            f"{MIN_SECRET_BYTES} bytes. Set a random value in .env, for example: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return secret


SECRET = _load_secret()


ACCESS_TOKEN_LIFETIME = timedelta(hours=1)


def create_access_token(user_id: str, auth_methods: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        # iat lets logout revoke every token issued before it (services/auth.py)
        "iat": now,
        "exp": now + ACCESS_TOKEN_LIFETIME,
        "auth_methods": auth_methods
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def decode_access_token_with_iat(token: str) -> tuple[str, int]:
    """Returns (user id, issued-at as epoch seconds). A token without iat, from
    before the claim existed, counts as issued at 0, so any logout revokes it.
    Raises jwt.PyJWTError (expired/malformed/bad signature) on failure."""
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    methods = set(payload.get("auth_methods", []))
    # otp is mandatory; the other factor is either a successful voice verify,
    # or the email-recovery path used when voice stops working for the owner.
    if "otp" not in methods or not ({"voice", "recovery"} & methods):
        raise jwt.InvalidTokenError("token was not issued after a full auth flow")
    return payload["sub"], int(payload.get("iat", 0))


def decode_access_token(token: str) -> str:
    """Raises jwt.PyJWTError (expired/malformed/bad signature) on failure."""
    return decode_access_token_with_iat(token)[0]
