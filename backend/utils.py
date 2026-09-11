from fastapi import Request


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
