from services import auth as auth_service


def _patch_redis(monkeypatch, fake_redis):
    monkeypatch.setattr(auth_service, "get_redis", lambda: fake_redis)


async def test_not_locked_initially(monkeypatch, fake_redis):
    _patch_redis(monkeypatch, fake_redis)
    assert not await auth_service.is_login_locked("user@example.com")


async def test_locks_after_max_attempts(monkeypatch, fake_redis):
    _patch_redis(monkeypatch, fake_redis)
    email = "user@example.com"
    for _ in range(auth_service.LOGIN_MAX_ATTEMPTS):
        await auth_service.record_login_failure(email)
    assert await auth_service.is_login_locked(email)


async def test_below_max_attempts_not_locked(monkeypatch, fake_redis):
    _patch_redis(monkeypatch, fake_redis)
    email = "user2@example.com"
    for _ in range(auth_service.LOGIN_MAX_ATTEMPTS - 1):
        await auth_service.record_login_failure(email)
    assert not await auth_service.is_login_locked(email)


async def test_clear_resets_lock(monkeypatch, fake_redis):
    _patch_redis(monkeypatch, fake_redis)
    email = "user3@example.com"
    for _ in range(auth_service.LOGIN_MAX_ATTEMPTS):
        await auth_service.record_login_failure(email)
    assert await auth_service.is_login_locked(email)

    await auth_service.clear_login_failures(email)
    assert not await auth_service.is_login_locked(email)


async def test_lockout_key_ignores_case_and_surrounding_whitespace(monkeypatch, fake_redis):
    _patch_redis(monkeypatch, fake_redis)
    for _ in range(auth_service.LOGIN_MAX_ATTEMPTS):
        await auth_service.record_login_failure("  User@Example.com ")
    assert await auth_service.is_login_locked("user@example.com")


def test_dummy_hash_is_a_valid_bcrypt_hash():
    # verify_password must actually run its full bcrypt comparison against
    # this constant when the account doesn't exist, or the login-timing fix
    # is a no-op; a malformed dummy hash would raise instead of returning
    # False, which would itself become a distinguishing timing/behavior signal.
    assert auth_service.verify_password("anything", auth_service.DUMMY_PASSWORD_HASH) is False
