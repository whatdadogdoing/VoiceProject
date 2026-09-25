from models.db import get_pool

# The text users agree to is in frontend/index.html. Whenever it changes, bump this
# (users then have to agree again) and add its hash in tests/test_consent_version.py,
# which fails if the text and the version drift apart.
CURRENT_CONSENT_VERSION = "1.0"


async def record_consent(user_id: str, ip: str, user_agent: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO consent_records (user_id, ip_address, user_agent, consent_version)
            VALUES ($1, $2, $3, $4)
        """, user_id, ip, user_agent, CURRENT_CONSENT_VERSION)


async def revoke_consent(user_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE consent_records SET is_active=FALSE
                WHERE user_id=$1 AND is_active=TRUE
            """, user_id)
            # Revoking consent means the stored biometric template is no
            # longer authorized to exist at all -- merely flipping is_active
            # (like the blue-green re-enrollment swap does) would leave the
            # embedding sitting in the table indefinitely with no way back to
            # active, which doesn't actually honor "delete my voice data".
            await conn.execute("""
                DELETE FROM voiceprints WHERE user_id=$1
            """, user_id)


async def has_active_consent(user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id FROM consent_records
            WHERE user_id=$1 AND is_active=TRUE AND consent_version=$2
        """, user_id, CURRENT_CONSENT_VERSION)
        return row is not None
