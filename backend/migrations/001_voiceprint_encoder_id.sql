-- Records which encoder produced each stored voiceprint.
--
-- schema.sql only runs when a Postgres data volume is first created, so a database
-- that already exists needs this applied by hand:
--
--   docker compose exec -T postgres psql -U user -d voiceauth < backend/migrations/001_voiceprint_encoder_id.sql
--
-- Safe to run more than once. The defaults describe the rows that exist today
-- (Resemblyzer 0.1.4, 256 dimensions), so existing voiceprints keep working.
ALTER TABLE voiceprints
    ADD COLUMN IF NOT EXISTS model_id VARCHAR(64) NOT NULL DEFAULT 'resemblyzer-0.1.4',
    ADD COLUMN IF NOT EXISTS embedding_dim INT NOT NULL DEFAULT 256;
