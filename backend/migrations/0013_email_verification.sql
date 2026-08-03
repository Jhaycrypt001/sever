-- Email verification (ADR-062).
--
-- An account is unusable until the address behind it has proved it can receive
-- mail. `email_verified_at` is the single source of truth: NULL means the
-- credentials exist but no session may be issued.
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ;

-- Accounts created before this migration were never asked to verify. Locking
-- them out retroactively would be a data-loss event for someone who did
-- nothing wrong, so they are grandfathered in at their creation time.
UPDATE users SET email_verified_at = created_at;

-- Codes are stored hashed, exactly like refresh tokens (ADR-008): a leaked
-- table must not hand anyone a working code. `attempts` caps guessing at a
-- 6-digit code, and `consumed_at` makes a successful code single-use rather
-- than replayable for the rest of its TTL.
CREATE TABLE email_verifications (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    code_hash   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The lookup is always "the newest code for this user".
CREATE INDEX email_verifications_user_idx
    ON email_verifications (user_id, created_at DESC);
