-- One code table, two jobs (ADR-063).
--
-- `email_verifications` now also carries password-reset codes. The table keeps
-- its name — renaming it would churn every query for no behavioural gain — but
-- a code issued for one purpose must never be accepted for the other, so the
-- purpose is stored and every lookup filters on it.
ALTER TABLE email_verifications
    ADD COLUMN purpose TEXT NOT NULL DEFAULT 'verify';

ALTER TABLE email_verifications
    ADD CONSTRAINT email_verifications_purpose_check
    CHECK (purpose IN ('verify', 'reset'));

-- Supersession is per purpose (ADR-063): asking to reset a password must not
-- silently kill the sign-in code the same person is holding. The lookup is
-- always "the one live code of this purpose for this user".
DROP INDEX IF EXISTS email_verifications_user_idx;
CREATE INDEX email_verifications_user_purpose_idx
    ON email_verifications (user_id, purpose, created_at DESC);
