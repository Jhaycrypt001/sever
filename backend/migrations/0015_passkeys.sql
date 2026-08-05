-- Passkey (WebAuthn) sign-in — ADR-072.
--
-- A passkey is registered from an already-signed-in session, so every row here
-- belongs to an account that has already proved control of its address. That is
-- what makes it safe for a passkey to open a session on its own, without the
-- e-mailed code of ADR-063: the second factor was established at registration
-- and lives in the authenticator, not the inbox.

CREATE TABLE passkey_credentials (
    id           UUID PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- The raw WebAuthn credential ID, base64url as the browser reports it.
    -- Unique globally, not per user: an authenticator must never be able to
    -- present the same credential for two accounts.
    credential_id TEXT NOT NULL UNIQUE,
    -- The serialised `Passkey` (public key, signature counter, transports).
    -- Opaque to SQL on purpose — its shape belongs to the webauthn library,
    -- and reading it here would couple the schema to that version.
    credential   JSONB NOT NULL,
    -- What the person calls this device in the account screen.
    label        TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX idx_passkey_credentials_user ON passkey_credentials (user_id, created_at DESC);

-- In-flight ceremony state.
--
-- WebAuthn is two round trips: the server issues a challenge, the browser
-- answers it. The state between them must be held server-side — keeping it in
-- the client would let an attacker choose their own challenge, which is the
-- whole thing the challenge exists to prevent.
--
-- Rows are short-lived and deleted on use. `user_id` is NULL for a login
-- ceremony, because a discoverable credential identifies the account only once
-- the browser answers.
CREATE TABLE webauthn_ceremonies (
    id         UUID PRIMARY KEY,
    user_id    UUID REFERENCES users (id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL CHECK (purpose IN ('register', 'authenticate')),
    state      JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_webauthn_ceremonies_expiry ON webauthn_ceremonies (expires_at);
