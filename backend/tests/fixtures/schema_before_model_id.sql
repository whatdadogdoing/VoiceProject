CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- user_id is intentionally not UNIQUE: re-enrollment stages a new row
-- (is_active=FALSE) alongside the existing active one so the old voiceprint
-- stays usable until the new one is fully captured and swapped in. Only one
-- row per user may be active at a time.
CREATE TABLE voiceprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    embedding FLOAT4[] NOT NULL,
    enrolled_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE UNIQUE INDEX idx_voiceprints_user_active ON voiceprints(user_id) WHERE is_active = TRUE;

CREATE TABLE voice_auth_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    attempted_at TIMESTAMP DEFAULT NOW(),
    ip_address INET,
    device_fingerprint VARCHAR(255),
    voiceprint_score FLOAT,
    antispoofing_score FLOAT,
    risk_decision VARCHAR(20),
    failure_reason VARCHAR(100)
);
CREATE INDEX idx_voice_auth_attempts_user_time ON voice_auth_attempts(user_id, attempted_at DESC);

CREATE TABLE consent_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    consented_at TIMESTAMP DEFAULT NOW(),
    ip_address INET,
    user_agent TEXT,
    consent_version VARCHAR(10) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_consent_records_user_active ON consent_records(user_id, is_active);
