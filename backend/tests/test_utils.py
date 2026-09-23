from datetime import datetime, timezone

from starlette.requests import Request

from utils import get_client_ip, get_device_fingerprint, local_hour


def _make_request(headers: dict, client_host: str | None = "203.0.113.9"):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": raw_headers,
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


def test_trusts_x_real_ip_set_by_nginx():
    req = _make_request({"x-real-ip": "198.51.100.7"})
    assert get_client_ip(req) == "198.51.100.7"


def test_ignores_spoofed_x_forwarded_for():
    # Regression test: nginx only APPENDS to X-Forwarded-For
    # ($proxy_add_x_forwarded_for), so a client-supplied first hop survives
    # untouched and must never be trusted for rate limiting or the risk
    # engine's "new IP" signal. X-Real-IP is what nginx overwrites
    # unconditionally, so it's the one that must win here.
    req = _make_request({
        "x-forwarded-for": "6.6.6.6, 203.0.113.9",
        "x-real-ip": "203.0.113.9",
    })
    assert get_client_ip(req) == "203.0.113.9"


def test_falls_back_to_direct_client_when_no_real_ip_header():
    req = _make_request({}, client_host="192.0.2.55")
    assert get_client_ip(req) == "192.0.2.55"


def test_falls_back_to_empty_string_when_no_client_info_at_all():
    req = _make_request({}, client_host=None)
    assert get_client_ip(req) == ""


def test_device_fingerprint_prefers_x_device_id():
    req = _make_request({"x-device-id": "dev-123", "user-agent": "Mozilla/5.0"})
    assert get_device_fingerprint(req) == "dev-123"


def test_device_fingerprint_falls_back_to_user_agent():
    req = _make_request({"user-agent": "Mozilla/5.0"})
    assert get_device_fingerprint(req) == "Mozilla/5.0"


def test_device_fingerprint_empty_when_no_headers():
    assert get_device_fingerprint(_make_request({})) == ""


def test_local_hour_converts_utc_to_vietnam_time():
    # Regression test: hour_of_day used to be the container's UTC clock, so
    # the "1am-5am is suspicious" rule actually fired at 8am-noon Vietnam time.
    assert local_hour(datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)) == 1   # 01:00 VN
    assert local_hour(datetime(2026, 9, 21, 22, 30, tzinfo=timezone.utc)) == 5  # 05:30 VN
    assert local_hour(datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)) == 8    # 08:00 VN


def test_local_hour_wraps_past_midnight():
    assert local_hour(datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc)) == 0   # 00:00 VN next day


def test_local_hour_treats_naive_datetime_as_utc():
    assert local_hour(datetime(2026, 9, 21, 18, 0)) == 1
