-- ADR-058 amendment: a dry run gets its own revocation status.
--
-- Before this, a KEEPERHUB_SIMULATE_ONLY run stored 'revoked' even though no
-- transaction was ever broadcast, so the dashboard showed a still-live
-- draining approval as neutralized. 'simulated' makes the distinction
-- durable: 'revoked' now means exactly "confirmed onchain, tx hash attached".

ALTER TABLE approval_findings DROP CONSTRAINT approval_findings_revocation_status_check;
ALTER TABLE approval_findings ADD CONSTRAINT approval_findings_revocation_status_check
    CHECK (revocation_status IN ('not_attempted', 'pending', 'simulated', 'revoked', 'failed'));
