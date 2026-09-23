from datetime import datetime, timedelta, timezone

from fastapi import Request

# The risk engine's "unusual hour" rule is about the user's local night, not
# the server's clock. The container has no TZ set, so datetime.now() was UTC --
# which shifted the 1am-5am rule onto 8am-noon in Vietnam. A fixed offset
# (Vietnam has no DST) avoids needing tzdata in the slim image.
VN_TZ = timezone(timedelta(hours=7))


def local_hour(now: datetime | None = None) -> int:
    """Hour of day (0-23) in Vietnam local time. `now` is injectable for tests;
    a naive datetime is treated as UTC."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(VN_TZ).hour


def get_device_fingerprint(request: Request) -> str:
    """X-Device-Id is a random id the frontend generates once and persists in
    localStorage -- far higher entropy than the User-Agent string, which is
    identical for every visitor on the same browser/OS build and so barely
    distinguishes "new device" at all. Falls back to User-Agent when absent.
    Enrollment and verification must both use this so a device seen at
    enrollment is recognised at verify time."""
    return request.headers.get("X-Device-Id") or request.headers.get("User-Agent", "")


def get_client_ip(request: Request) -> str:
    """X-Real-IP is set by nginx (proxy_set_header X-Real-IP $remote_addr),
    which always overwrites any client-supplied value -- unlike
    X-Forwarded-For, which nginx only appends to, leaving a client-supplied
    first hop intact and spoofable. Trusting that first hop let a client
    defeat per-IP rate limiting and the risk engine's "new IP" signal just by
    sending a different X-Forwarded-For value on every request."""
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else ""
