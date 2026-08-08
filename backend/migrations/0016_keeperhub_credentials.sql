-- Per-user KeeperHub API keys — ADR-076.
--
-- Until now one key lived in the environment and Sever executed as one wallet
-- (ADR-065). A key stored here lets an account revoke for *its own* delegated
-- wallet: the key authenticates as that wallet, so the delegation check in the
-- agent passes on its own terms rather than being relaxed.
--
-- The key is encrypted, not hashed: unlike a password, Sever must send it to
-- KeeperHub, so it has to be recoverable. `api_key_encrypted` holds
-- base64(nonce || XChaCha20-Poly1305 ciphertext) and the encryption key lives
-- in CREDENTIAL_ENCRYPTION_KEY, never in this database — a dump of this table
-- alone decrypts nothing.

CREATE TABLE keeperhub_credentials (
    -- One key per account, so the user id *is* the primary key. A second
    -- delegated wallet would be a second account.
    user_id            UUID PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    api_key_encrypted  TEXT NOT NULL,
    -- The wallet this key executes as, read back from KeeperHub's own
    -- GET /api/user when the key was saved. Stored so the account screen can
    -- show which wallet is connected without decrypting the key, and so a scan
    -- of a different address can be warned about before it is launched.
    -- Lowercase hex, as everywhere else in this schema.
    wallet_address     TEXT,
    -- The last four characters, for the account screen. Kept alongside the
    -- ciphertext so rendering the settings panel never needs the decryption
    -- key in the request path.
    masked             TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
