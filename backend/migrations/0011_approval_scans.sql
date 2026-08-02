-- ADR-058: pivot the example's web-research domain to onchain approval-risk
-- scanning. Renames tables/columns in place (never drops job/user history);
-- the findings table changes shape entirely, so it is replaced.

ALTER TABLE research_jobs RENAME TO scan_jobs;
ALTER TABLE scan_jobs RENAME COLUMN keyword TO wallet_address;
ALTER INDEX research_jobs_user_id_idx RENAME TO scan_jobs_user_id_idx;
ALTER INDEX research_jobs_recurring_idx RENAME TO scan_jobs_recurring_idx;

ALTER TABLE recurring_searches RENAME COLUMN keyword TO wallet_address;

-- The web-research result (title/url/snippet/date) has no equivalent in the
-- approval-risk domain; replace rather than ALTER piecemeal.
DROP TABLE search_results;
CREATE TABLE approval_findings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id             UUID NOT NULL REFERENCES scan_jobs (id) ON DELETE CASCADE,
    chain_id           TEXT NOT NULL,
    token_address      TEXT NOT NULL,
    token_symbol       TEXT NOT NULL,
    spender_address    TEXT NOT NULL,
    spender_name       TEXT,
    approved_amount    TEXT NOT NULL,
    tier               TEXT NOT NULL CHECK (tier IN ('safe', 'watch', 'dangerous')),
    malicious_behavior JSONB NOT NULL DEFAULT '[]',
    explanation        TEXT,
    is_new             BOOLEAN NOT NULL DEFAULT TRUE,
    revocation_status  TEXT NOT NULL DEFAULT 'not_attempted'
                           CHECK (revocation_status IN ('not_attempted', 'pending', 'revoked', 'failed')),
    revocation_tx_hash TEXT,
    raw                JSONB NOT NULL DEFAULT 'null'
);
CREATE INDEX approval_findings_job_id_idx ON approval_findings (job_id);
