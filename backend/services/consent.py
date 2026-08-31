from models.db import get_pool

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
        await conn.execute("""
            UPDATE consent_records SET is_active=FALSE
            WHERE user_id=$1 AND is_active=TRUE
        """, user_id)
        await conn.execute("""
            UPDATE voiceprints SET is_active=FALSE
            WHERE user_id=$1
        """, user_id)


async def has_active_consent(user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id FROM consent_records
            WHERE user_id=$1 AND is_active=TRUE AND consent_version=$2
        """, user_id, CURRENT_CONSENT_VERSION)
        return row is not None
