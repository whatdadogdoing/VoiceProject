import os
from slowapi import Limiter
from utils import get_client_ip

limiter = Limiter(
    key_func=get_client_ip,
    storage_uri=os.getenv("REDIS_URL", "redis://localhost:6379"),
)
