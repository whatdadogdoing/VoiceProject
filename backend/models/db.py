import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL")
_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def get_user_embedding(user_id: str) -> list[float] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT embedding FROM voiceprints WHERE user_id=$1 AND is_active=TRUE",
            user_id
        )
        return list(row["embedding"]) if row else None


async def update_voiceprint_embedding(user_id: str, embedding: list[float]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE voiceprints SET embedding=$2, last_used_at=NOW()
            WHERE user_id=$1 AND is_active=TRUE
        """, user_id, embedding)


async def has_voiceprint(user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM voiceprints WHERE user_id=$1 AND is_active=TRUE", user_id
        )
        return row is not None


async def get_user_email(user_id: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email FROM users WHERE id=$1", user_id
        )
        return row["email"] if row else None


async def create_user(email: str, password_hash: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
            email, password_hash
        )
        return str(row["id"])


async def get_user_by_email(email: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, email, password_hash FROM users WHERE email=$1", email
        )


async def is_known_device(user_id: str, device_fingerprint: str | None) -> bool:
    if not device_fingerprint:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT 1 FROM voice_auth_attempts
            WHERE user_id=$1 AND device_fingerprint=$2 AND risk_decision != 'rejected'
            LIMIT 1
        """, user_id, device_fingerprint)
        return row is not None


async def is_known_ip(user_id: str, ip: str | None) -> bool:
    if not ip:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT 1 FROM voice_auth_attempts
            WHERE user_id=$1 AND ip_address=$2::inet AND risk_decision != 'rejected'
            LIMIT 1
        """, user_id, ip)
        return row is not None


async def save_voiceprint(user_id: str, embedding: list[float], replace_existing: bool = False) -> None:
    """First-time enrollment just inserts. Re-enrollment stages the new
    embedding as an inactive row first, then swaps it in and removes the old
    row in one transaction — the old voiceprint keeps working right up until
    the new one is fully captured, instead of being wiped before the
    replacement exists."""
    pool = await get_pool()
    if not replace_existing:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO voiceprints (user_id, embedding) VALUES ($1, $2)",
                user_id, embedding
            )
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            new_id = await conn.fetchval("""
                INSERT INTO voiceprints (user_id, embedding, is_active)
                VALUES ($1, $2, FALSE)
                RETURNING id
            """, user_id, embedding)
            await conn.execute(
                "UPDATE voiceprints SET is_active=FALSE WHERE user_id=$1 AND id != $2",
                user_id, new_id
            )
            await conn.execute(
                "UPDATE voiceprints SET is_active=TRUE WHERE id=$1",
                new_id
            )
            await conn.execute(
                "DELETE FROM voiceprints WHERE user_id=$1 AND id != $2",
                user_id, new_id
            )


async def log_attempt(user_id, voiceprint_score, spoof_score, decision, reason, ip=None, device=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO voice_auth_attempts
            (user_id, voiceprint_score, antispoofing_score, risk_decision, failure_reason, ip_address, device_fingerprint)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, user_id, voiceprint_score, spoof_score, decision, reason, ip, device)


async def count_recent_failures(user_id: str, minutes: int = 10, exclude_reasons=()) -> int:
    """Rejected attempts in the window. `exclude_reasons` lets the caller leave
    out failure reasons that say nothing about security (a bad mic, a misread
    phrase); COALESCE keeps a NULL reason from silently dropping the row, since
    `NULL <> ALL(...)` is NULL rather than true."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT COUNT(*) as cnt FROM voice_auth_attempts
            WHERE user_id=$1
            AND risk_decision='rejected'
            AND COALESCE(failure_reason, '') <> ALL($3::text[])
            AND attempted_at > NOW() - ($2 || ' minutes')::INTERVAL
        """, user_id, str(minutes), list(exclude_reasons))
        return row["cnt"]


async def count_recent_fraud_failures(user_id: str, minutes: int = 30) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT COUNT(*) as cnt FROM voice_auth_attempts
            WHERE user_id=$1
            AND failure_reason='spoofing_detected'
            AND attempted_at > NOW() - ($2 || ' minutes')::INTERVAL
        """, user_id, str(minutes))
        return row["cnt"]
