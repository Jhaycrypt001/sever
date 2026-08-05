//! Ports (hexagonal architecture): the domain and use cases depend only on these
//! traits. Adapters (persistence, auth, dispatch) implement them.

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use uuid::Uuid;

use super::{
    AgentStep, ApprovalFinding, CodePurpose, EmailVerification, JobUsage, PasskeyCredential,
    RecurringSearch, RefreshToken, ScanJob, SecurityEvent, User, WebauthnCeremony,
};

/// Infrastructure failure surfaced through a port (DB down, network error...).
#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct PortError(pub String);

#[async_trait]
pub trait UserRepository: Send + Sync {
    async fn insert(&self, user: &User) -> Result<(), PortError>;
    async fn find_by_email(&self, email: &str) -> Result<Option<User>, PortError>;
    /// Records that the address behind the account answered its code (ADR-062).
    /// Idempotent: verifying an already-verified account is a no-op, not an
    /// error, because a double-submitted form must not fail.
    async fn mark_email_verified(&self, id: Uuid, at: DateTime<Utc>) -> Result<(), PortError>;
    /// Replaces the stored password hash (ADR-063, account recovery).
    async fn update_password_hash(&self, id: Uuid, hash: &str) -> Result<(), PortError>;
}

/// Outstanding verification codes (ADR-062), stored hashed like refresh tokens.
///
/// At most one code per account is live at a time: `insert` supersedes any
/// previous one. Without that, "the current code" would have to be resolved by
/// comparing timestamps, and two codes issued inside the same microsecond
/// (`now_utc` truncates there) would make it a coin flip — an ambiguity that
/// resend, of all operations, would hit first.
#[async_trait]
pub trait EmailVerificationRepository: Send + Sync {
    /// Stores a new code and consumes any code the user still had outstanding
    /// **of the same purpose** (ADR-063): requesting a password reset must not
    /// silently kill the sign-in code the same person is holding.
    async fn insert(&self, verification: &EmailVerification) -> Result<(), PortError>;
    /// The user's one live (unconsumed) code of that purpose, whatever its
    /// state otherwise. Expired and exhausted codes are still returned, so the
    /// caller can tell "expired" from "never existed" and say which.
    async fn active_for_user(
        &self,
        user_id: Uuid,
        purpose: CodePurpose,
    ) -> Result<Option<EmailVerification>, PortError>;
    /// Any code of that purpose whose hash matches, live or spent (ADR-063).
    ///
    /// Used to recognise a code that *was* real but has since been superseded,
    /// so the answer can say "that one is stale, use the newest email" instead
    /// of "invalid" — and so a stale code does not burn an attempt on the code
    /// that replaced it.
    async fn find_by_hash(
        &self,
        user_id: Uuid,
        purpose: CodePurpose,
        code_hash: &str,
    ) -> Result<Option<EmailVerification>, PortError>;
    /// Counts a wrong guess against the attempt cap.
    async fn record_attempt(&self, id: Uuid) -> Result<(), PortError>;
    async fn mark_consumed(&self, id: Uuid, at: DateTime<Utc>) -> Result<(), PortError>;
    /// Purges codes that expired before `cutoff` (called by the reaper).
    async fn delete_expired(&self, now: DateTime<Utc>) -> Result<u64, PortError>;
}

/// Registered passkeys and in-flight WebAuthn ceremonies (ADR-072).
///
/// One port rather than two: a ceremony exists only to produce or consume a
/// credential, and every use case here touches both.
#[async_trait]
pub trait PasskeyRepository: Send + Sync {
    async fn insert_credential(&self, credential: &PasskeyCredential) -> Result<(), PortError>;
    async fn credentials_for_user(
        &self,
        user_id: Uuid,
    ) -> Result<Vec<PasskeyCredential>, PortError>;
    /// Looks a credential up by the ID the browser presented, across all
    /// accounts — this is what turns a discoverable sign-in into a user.
    async fn find_by_credential_id(
        &self,
        credential_id: &str,
    ) -> Result<Option<PasskeyCredential>, PortError>;
    /// Persists the library's updated credential (the signature counter moves
    /// on every use, and a counter that goes backwards is how a cloned
    /// authenticator is detected).
    async fn update_credential(
        &self,
        id: Uuid,
        credential: &serde_json::Value,
        last_used_at: DateTime<Utc>,
    ) -> Result<(), PortError>;
    async fn delete_credential(&self, id: Uuid, user_id: Uuid) -> Result<bool, PortError>;

    async fn insert_ceremony(&self, ceremony: &WebauthnCeremony) -> Result<(), PortError>;
    /// Fetches and deletes in one step. A challenge is single-use: leaving it
    /// readable after the answer arrives is what makes a replay possible.
    async fn take_ceremony(&self, id: Uuid) -> Result<Option<WebauthnCeremony>, PortError>;
    /// Purges ceremonies abandoned before `cutoff` (called by the reaper).
    async fn delete_expired_ceremonies(&self, now: DateTime<Utc>) -> Result<u64, PortError>;
}

/// Delivers transactional email (ADR-062).
///
/// The port names the *intent*, not the template, so an adapter is free to
/// send HTML, plain text, or hand off to a provider's own template. A failure
/// here is not best-effort: if the code never left the building, registration
/// has to say so rather than park someone in front of a code entry box that
/// can never be satisfied.
#[async_trait]
pub trait EmailSender: Send + Sync {
    /// `purpose` picks the wording — one method rather than two, so a new kind
    /// of code cannot ship with a provider adapter that forgot to implement it.
    async fn send_code(
        &self,
        to: &str,
        code: &str,
        ttl_minutes: i64,
        purpose: CodePurpose,
    ) -> Result<(), PortError>;
}

#[async_trait]
pub trait JobRepository: Send + Sync {
    async fn insert(&self, job: &ScanJob) -> Result<(), PortError>;
    async fn update(&self, job: &ScanJob) -> Result<(), PortError>;
    async fn find(&self, id: Uuid) -> Result<Option<ScanJob>, PortError>;
    async fn list_for_user(&self, user_id: Uuid) -> Result<Vec<ScanJob>, PortError>;
    /// Number of jobs the user created since `since` — quota input (ADR-017).
    async fn count_created_since(
        &self,
        user_id: Uuid,
        since: DateTime<Utc>,
    ) -> Result<u64, PortError>;
    /// Unfinished (pending/running) jobs created before `cutoff` — reaper input.
    async fn list_unfinished_older_than(
        &self,
        cutoff: DateTime<Utc>,
    ) -> Result<Vec<ScanJob>, PortError>;
    async fn store_results(
        &self,
        job_id: Uuid,
        results: &[ApprovalFinding],
    ) -> Result<(), PortError>;
    async fn results_for(&self, job_id: Uuid) -> Result<Vec<ApprovalFinding>, PortError>;
    /// Records one decision of the agentic loop (ADR-030). Idempotent on
    /// `(job_id, seq)` so Celery retries never duplicate journal entries.
    async fn append_step(&self, job_id: Uuid, step: &AgentStep) -> Result<(), PortError>;
    /// The journal in `seq` order.
    async fn steps_for(&self, job_id: Uuid) -> Result<Vec<AgentStep>, PortError>;
    /// Replace semantics on resume (ADR-032): answering a clarification
    /// re-runs the loop from scratch, so the journal starts fresh too.
    async fn clear_steps(&self, job_id: Uuid) -> Result<(), PortError>;
    /// Accumulates one task attempt's spend onto the job (ADR-038).
    async fn add_usage(&self, job_id: Uuid, usage: &JobUsage) -> Result<(), PortError>;
    /// Approval keys (chain:token:spender) already delivered by previous runs
    /// of a recurring scan — the memory the agent receives to flag deltas
    /// (ADR-033). Most recent first, capped by `limit` to bound the task
    /// payload.
    async fn recent_approval_keys_for_recurring(
        &self,
        recurring_search_id: Uuid,
        limit: u32,
    ) -> Result<Vec<String>, PortError>;
}

/// Saved scans re-run by the scheduler (ADR-033).
#[async_trait]
pub trait RecurringSearchRepository: Send + Sync {
    async fn insert(&self, search: &RecurringSearch) -> Result<(), PortError>;
    async fn find(&self, id: Uuid) -> Result<Option<RecurringSearch>, PortError>;
    async fn list_for_user(&self, user_id: Uuid) -> Result<Vec<RecurringSearch>, PortError>;
    /// Deletes the user's recurring scan; false when unknown or foreign.
    async fn delete(&self, user_id: Uuid, id: Uuid) -> Result<bool, PortError>;
    /// Every recurring scan due at `now` (never run, or interval elapsed).
    async fn list_due(&self, now: DateTime<Utc>) -> Result<Vec<RecurringSearch>, PortError>;
    async fn mark_ran(&self, id: Uuid, at: DateTime<Utc>) -> Result<(), PortError>;
}

/// Persisted refresh tokens (ADR-008): stored hashed, single use (rotation).
#[async_trait]
pub trait RefreshTokenRepository: Send + Sync {
    async fn insert(&self, token: &RefreshToken) -> Result<(), PortError>;
    async fn find_by_hash(&self, hash: &str) -> Result<Option<RefreshToken>, PortError>;
    /// Marks a rotated-away token consumed (ADR-056): it is kept, not deleted,
    /// so a later replay is caught as reuse instead of looking unknown.
    async fn mark_consumed(&self, id: Uuid, at: DateTime<Utc>) -> Result<(), PortError>;
    async fn delete(&self, id: Uuid) -> Result<(), PortError>;
    /// Revokes an entire rotation lineage (ADR-056): called on reuse detection
    /// to kill the stolen token's whole family in one shot.
    async fn delete_family(&self, family_id: Uuid) -> Result<(), PortError>;
    /// Revokes every session the user has, across all families. Used by
    /// password reset (ADR-063): someone recovering an account they think is
    /// compromised expects the intruder to be signed out, not just themselves.
    async fn delete_for_user(&self, user_id: Uuid) -> Result<(), PortError>;
    /// Purges expired tokens (called by the background reaper).
    async fn delete_expired(&self, now: DateTime<Utc>) -> Result<u64, PortError>;
}

/// Append-only security audit log (ADR-057): failed/throttled logins, refresh
/// reuse, quota hits. Writes are best-effort — a recording failure must never
/// break the request that triggered it (the caller logs and continues).
#[async_trait]
pub trait SecurityAudit: Send + Sync {
    async fn record(&self, event: &SecurityEvent) -> Result<(), PortError>;
    /// Most recent events first, capped at `limit` — for an operator view.
    async fn list_recent(&self, limit: i64) -> Result<Vec<SecurityEvent>, PortError>;
    /// Retention purge (ADR-057): drops events older than `cutoff`. Called by
    /// the background loop, like the refresh-token purge.
    async fn delete_before(&self, cutoff: DateTime<Utc>) -> Result<u64, PortError>;
}

/// Sends a scan job to the agent (via the FastAPI micro-API, see ADR-005).
/// `seen_approval_keys` is the recurring-scan memory (ADR-033): approval keys
/// (chain:token:spender) delivered by previous runs, empty for one-shot scans.
#[async_trait]
pub trait JobDispatcher: Send + Sync {
    async fn dispatch(&self, job: &ScanJob, seen_approval_keys: &[String])
        -> Result<(), PortError>;
}

/// A digest of a recurring run that found something new (ADR-036/058).
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Digest {
    pub recurring_search_id: Uuid,
    pub job_id: Uuid,
    pub wallet_address: String,
    pub new_count: usize,
    /// The new findings only, most-dangerous-first.
    pub new_results: Vec<DigestEntry>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct DigestEntry {
    pub token_symbol: String,
    pub spender_address: String,
    pub tier: super::RiskTier,
    pub revocation_status: super::RevocationStatus,
}

/// Delivers digests (ADR-036) — webhook in this repository; an e-mail sender
/// is one more adapter behind the same port. Best-effort by contract: a
/// failed delivery is logged, never fails the ingestion.
#[async_trait]
pub trait DigestSender: Send + Sync {
    async fn send(&self, webhook_url: &str, digest: &Digest) -> Result<(), PortError>;
}

pub trait PasswordHasher: Send + Sync {
    fn hash(&self, password: &str) -> Result<String, PortError>;
    fn verify(&self, password: &str, hash: &str) -> bool;
}

pub trait TokenService: Send + Sync {
    fn issue(&self, user_id: Uuid) -> Result<String, PortError>;
    fn verify(&self, token: &str) -> Option<Uuid>;
}
