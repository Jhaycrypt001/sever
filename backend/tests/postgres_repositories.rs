//! Integration tests for the PostgreSQL adapter (ADR-007).
//!
//! They run against the database pointed to by `DATABASE_URL` — the compose
//! `postgres` service locally, a GitLab CI service in the pipeline (ADR-012).
//! Without `DATABASE_URL` they are skipped so `cargo test` stays usable offline.
//! Each test works on freshly generated UUIDs, so tests are isolated and can
//! run in parallel against a shared database.

use backend::adapters::persistence::postgres::{
    run_migrations, PostgresEmailVerificationRepository, PostgresJobRepository,
    PostgresRecurringSearchRepository, PostgresRefreshTokenRepository, PostgresSecurityAudit,
    PostgresUserRepository,
};
use backend::domain::ports::{
    EmailVerificationRepository, JobRepository, RecurringSearchRepository, RefreshTokenRepository,
    SecurityAudit, UserRepository,
};
use backend::domain::{
    AgentStep, ApprovalFinding, CodePurpose, EmailVerification, JobMode, JobStatus,
    RecurringSearch, RefreshToken, RevocationStatus, RiskTier, ScanJob, SecurityEvent,
    SecurityEventKind, User,
};
use chrono::Utc;
use sqlx::PgPool;
use uuid::Uuid;

// Valid EVM addresses: 0x + exactly 40 hex characters. ScanJob::new rejects
// anything else, so a short constant here fails only once a real DATABASE_URL
// makes these tests actually run.
const ADDR: &str = "0x1234567890123456789012345678901234567890";
const ADDR_A: &str = "0x111111111111111111111111111111111111111a";
const ADDR_B: &str = "0x222222222222222222222222222222222222222b";
const ADDR_C: &str = "0x333333333333333333333333333333333333333c";

async fn pool() -> Option<PgPool> {
    let Ok(url) = std::env::var("DATABASE_URL") else {
        eprintln!("skipping postgres tests: DATABASE_URL not set");
        return None;
    };
    let pool = PgPool::connect(&url)
        .await
        .expect("cannot connect to DATABASE_URL");
    run_migrations(&pool).await.expect("migrations failed");
    Some(pool)
}

fn unique_email() -> String {
    format!("{}@test.dev", Uuid::new_v4())
}

async fn insert_user(pool: &PgPool) -> User {
    let users = PostgresUserRepository::new(pool.clone());
    let user = User::new(unique_email(), "hash".into());
    users.insert(&user).await.unwrap();
    user
}

#[tokio::test]
async fn user_roundtrip() {
    let Some(pool) = pool().await else { return };
    let users = PostgresUserRepository::new(pool);

    let user = User::new(unique_email(), "argon2-hash".into());
    users.insert(&user).await.unwrap();

    let found = users.find_by_email(&user.email).await.unwrap();
    assert_eq!(found, Some(user.clone()));
    assert_eq!(users.find_by_email("nobody@test.dev").await.unwrap(), None);

    // ADR-062: a stored account starts unverified, and verifying it sticks.
    assert!(!found.unwrap().is_verified());
    let at = backend::domain::RefreshToken::issue(user.id, 1)
        .0
        .created_at;
    users.mark_email_verified(user.id, at).await.unwrap();
    let reloaded = users.find_by_email(&user.email).await.unwrap().unwrap();
    assert_eq!(reloaded.email_verified_at, Some(at));

    // Re-verifying keeps the original timestamp rather than rewriting it.
    users
        .mark_email_verified(user.id, at + chrono::Duration::hours(1))
        .await
        .unwrap();
    let reloaded = users.find_by_email(&user.email).await.unwrap().unwrap();
    assert_eq!(reloaded.email_verified_at, Some(at));
}

#[tokio::test]
async fn email_verification_roundtrip_and_supersession() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let codes = PostgresEmailVerificationRepository::new(pool);

    let (first, first_plain) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&first).await.unwrap();
    let active = codes
        .active_for_user(user.id, CodePurpose::Verify)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(active, first);
    assert!(active.matches(&first_plain));

    // Attempts accumulate on the stored row, which is what caps guessing.
    codes.record_attempt(first.id).await.unwrap();
    codes.record_attempt(first.id).await.unwrap();
    assert_eq!(
        codes
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap()
            .unwrap()
            .attempts,
        2
    );

    // A second code supersedes the first: exactly one is ever live (ADR-062).
    let (second, _) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&second).await.unwrap();
    let active = codes
        .active_for_user(user.id, CodePurpose::Verify)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(active.id, second.id);

    // Consuming it leaves nothing live, so a replay finds no code at all.
    codes.mark_consumed(second.id, Utc::now()).await.unwrap();
    assert_eq!(
        codes
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap(),
        None
    );
}

#[tokio::test]
async fn the_two_code_purposes_do_not_touch_each_other() {
    // ADR-063: issuing a reset code must leave a live sign-in code alone, and
    // neither may be found through the other's purpose.
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let codes = PostgresEmailVerificationRepository::new(pool);

    let (sign_in, sign_in_plain) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&sign_in).await.unwrap();
    let (reset, _) = EmailVerification::issue(user.id, CodePurpose::Reset, 10);
    codes.insert(&reset).await.unwrap();

    // Both live, each under its own purpose.
    assert_eq!(
        codes
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap()
            .map(|c| c.id),
        Some(sign_in.id)
    );
    assert_eq!(
        codes
            .active_for_user(user.id, CodePurpose::Reset)
            .await
            .unwrap()
            .map(|c| c.id),
        Some(reset.id)
    );

    // A sign-in code is invisible to a reset lookup, which is what stops one
    // being traded for the other.
    let hash = EmailVerification::hash(&sign_in_plain);
    assert!(codes
        .find_by_hash(user.id, CodePurpose::Reset, &hash)
        .await
        .unwrap()
        .is_none());
    assert!(codes
        .find_by_hash(user.id, CodePurpose::Verify, &hash)
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn a_superseded_code_is_still_findable_by_hash() {
    // ADR-063: this is how "that code was replaced" is told apart from "that
    // was never a code", so a stale email does not burn an attempt.
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let codes = PostgresEmailVerificationRepository::new(pool);

    let (first, first_plain) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&first).await.unwrap();
    let (second, _) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&second).await.unwrap();

    let found = codes
        .find_by_hash(
            user.id,
            CodePurpose::Verify,
            &EmailVerification::hash(&first_plain),
        )
        .await
        .unwrap()
        .expect("the superseded code is still on file");
    assert_eq!(found.id, first.id);
    assert!(found.is_consumed(), "superseding consumes it");
}

#[tokio::test]
async fn every_session_can_be_revoked_at_once() {
    // ADR-063: what a password reset does to an intruder's refresh cookie.
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let other = insert_user(&pool).await;
    let tokens = PostgresRefreshTokenRepository::new(pool);

    let (mine_a, _) = RefreshToken::issue(user.id, 30);
    let (mine_b, _) = RefreshToken::issue(user.id, 30);
    let (theirs, theirs_plain) = RefreshToken::issue(other.id, 30);
    for token in [&mine_a, &mine_b, &theirs] {
        tokens.insert(token).await.unwrap();
    }

    tokens.delete_for_user(user.id).await.unwrap();

    assert!(tokens
        .find_by_hash(&mine_a.token_hash)
        .await
        .unwrap()
        .is_none());
    assert!(tokens
        .find_by_hash(&mine_b.token_hash)
        .await
        .unwrap()
        .is_none());
    // Another account's sessions are untouched.
    assert!(tokens
        .find_by_hash(&RefreshToken::hash(&theirs_plain))
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn expired_verification_codes_are_purged() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let codes = PostgresEmailVerificationRepository::new(pool);

    // A negative TTL makes this code *already* expired, so the purge can be
    // driven with a `now` cutoff. Sweeping with a future cutoff instead would
    // delete live codes belonging to the tests running alongside this one —
    // `delete_expired` is a global sweep, not scoped to a user.
    let (stale, _) = EmailVerification::issue(user.id, CodePurpose::Reset, -1);
    codes.insert(&stale).await.unwrap();
    let (live, _) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&live).await.unwrap();

    let purged = codes.delete_expired(Utc::now()).await.unwrap();
    assert!(purged >= 1, "the expired code should have been swept");

    // The one still inside its TTL survives.
    assert!(codes
        .active_for_user(user.id, CodePurpose::Verify)
        .await
        .unwrap()
        .is_some());
    assert_eq!(
        codes
            .active_for_user(user.id, CodePurpose::Reset)
            .await
            .unwrap(),
        None
    );
}

#[tokio::test]
async fn a_live_code_survives_a_purge() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let codes = PostgresEmailVerificationRepository::new(pool);

    let (live, _) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
    codes.insert(&live).await.unwrap();

    codes.delete_expired(Utc::now()).await.unwrap();
    assert!(
        codes
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap()
            .is_some(),
        "a code inside its TTL must not be swept"
    );
}

#[tokio::test]
async fn duplicate_email_is_a_database_error() {
    let Some(pool) = pool().await else { return };
    let users = PostgresUserRepository::new(pool);

    let email = unique_email();
    users
        .insert(&User::new(email.clone(), "h1".into()))
        .await
        .unwrap();
    let err = users
        .insert(&User::new(email, "h2".into()))
        .await
        .unwrap_err();
    assert!(err.to_string().contains("database error"));
}

#[tokio::test]
async fn job_lifecycle_roundtrip() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);

    let mut job = ScanJob::new(user.id, ADDR).unwrap();
    jobs.insert(&job).await.unwrap();
    assert_eq!(jobs.find(job.id).await.unwrap().as_ref(), Some(&job));

    job.fail("boom".into());
    jobs.update(&job).await.unwrap();

    let stored = jobs.find(job.id).await.unwrap().unwrap();
    assert_eq!(stored.status, JobStatus::Failed);
    assert_eq!(stored.error.as_deref(), Some("boom"));
    assert!(stored.completed_at.is_some());
}

#[tokio::test]
async fn list_for_user_is_scoped_and_newest_first() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let other = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);

    let first = ScanJob::new(user.id, ADDR_A).unwrap();
    let second = ScanJob::new(user.id, ADDR_B).unwrap();
    let foreign = ScanJob::new(other.id, ADDR_C).unwrap();
    for job in [&first, &second, &foreign] {
        jobs.insert(job).await.unwrap();
    }

    let listed = jobs.list_for_user(user.id).await.unwrap();
    let addresses: Vec<&str> = listed.iter().map(|j| j.wallet_address.as_str()).collect();
    assert_eq!(addresses, vec![ADDR_B, ADDR_A]);
}

#[tokio::test]
async fn refresh_token_roundtrip_delete_and_purge() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let repo = PostgresRefreshTokenRepository::new(pool);

    let (valid, _) = RefreshToken::issue(user.id, 30);
    let (mut expired, _) = RefreshToken::issue(user.id, 30);
    expired.expires_at = Utc::now() - chrono::Duration::hours(1);
    repo.insert(&valid).await.unwrap();
    repo.insert(&expired).await.unwrap();

    // Roundtrip by hash.
    let found = repo.find_by_hash(&valid.token_hash).await.unwrap();
    assert_eq!(found, Some(valid.clone()));

    // Purge removes only the expired one.
    assert_eq!(repo.delete_expired(Utc::now()).await.unwrap(), 1);
    assert!(repo
        .find_by_hash(&expired.token_hash)
        .await
        .unwrap()
        .is_none());
    assert!(repo
        .find_by_hash(&valid.token_hash)
        .await
        .unwrap()
        .is_some());

    // Reuse detection (ADR-056): mark_consumed keeps the row but flags it.
    let at = Utc::now();
    repo.mark_consumed(valid.id, at).await.unwrap();
    let consumed = repo.find_by_hash(&valid.token_hash).await.unwrap().unwrap();
    assert!(consumed.is_consumed());
    assert_eq!(consumed.consumed_at.unwrap().timestamp(), at.timestamp());

    // Explicit delete (logout of a single row).
    repo.delete(valid.id).await.unwrap();
    assert!(repo
        .find_by_hash(&valid.token_hash)
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn security_events_record_list_newest_first_and_purge() {
    // ADR-057: append, read back newest-first, and retention purge.
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let audit = PostgresSecurityAudit::new(pool);

    // Clean slate: this is the only test writing security_events, and
    // list_recent is a global operator view, so wipe first for a deterministic
    // count (the shared DB persists rows between runs).
    audit
        .delete_before(Utc::now() + chrono::Duration::days(1))
        .await
        .unwrap();

    let mut old = SecurityEvent::new(
        SecurityEventKind::LoginFailed,
        None,
        Some("1.2.3.4".into()),
        "old",
    );
    old.created_at = Utc::now() - chrono::Duration::days(120);
    let recent = SecurityEvent::new(
        SecurityEventKind::RefreshReuseDetected,
        Some(user.id),
        None,
        "recent",
    );
    audit.record(&old).await.unwrap();
    audit.record(&recent).await.unwrap();

    // Newest first.
    let listed = audit.list_recent(10).await.unwrap();
    assert_eq!(listed.len(), 2);
    assert_eq!(listed[0].detail, "recent");
    assert_eq!(listed[0].user_id, Some(user.id));
    assert_eq!(listed[1].client_ip.as_deref(), Some("1.2.3.4"));

    // Retention purge drops only the old one.
    let cutoff = Utc::now() - chrono::Duration::days(90);
    assert_eq!(audit.delete_before(cutoff).await.unwrap(), 1);
    let remaining = audit.list_recent(10).await.unwrap();
    assert_eq!(remaining.len(), 1);
    assert_eq!(remaining[0].detail, "recent");
}

#[tokio::test]
async fn delete_family_revokes_a_whole_rotation_lineage() {
    // ADR-056: reuse detection revokes every token sharing a family_id.
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let repo = PostgresRefreshTokenRepository::new(pool);

    let (root, _) = RefreshToken::issue(user.id, 30);
    let (child, _) = RefreshToken::issue_in_family(user.id, root.family_id, 30);
    let (other, _) = RefreshToken::issue(user.id, 30); // a different family
    repo.insert(&root).await.unwrap();
    repo.insert(&child).await.unwrap();
    repo.insert(&other).await.unwrap();

    repo.delete_family(root.family_id).await.unwrap();

    assert!(repo.find_by_hash(&root.token_hash).await.unwrap().is_none());
    assert!(repo
        .find_by_hash(&child.token_hash)
        .await
        .unwrap()
        .is_none());
    // The unrelated family is untouched.
    assert!(repo
        .find_by_hash(&other.token_hash)
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn count_created_since_scopes_by_user_and_window() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let other = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);

    let recent = ScanJob::new(user.id, ADDR).unwrap();
    let mut old = ScanJob::new(user.id, ADDR).unwrap();
    old.created_at = Utc::now() - chrono::Duration::hours(25);
    let foreign = ScanJob::new(other.id, ADDR).unwrap();
    for job in [&recent, &old, &foreign] {
        jobs.insert(job).await.unwrap();
    }

    let since = Utc::now() - chrono::Duration::hours(24);
    assert_eq!(jobs.count_created_since(user.id, since).await.unwrap(), 1);
}

#[tokio::test]
async fn list_unfinished_older_than_feeds_the_reaper() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);

    let mut stale_pending = ScanJob::new(user.id, ADDR).unwrap();
    stale_pending.created_at = Utc::now() - chrono::Duration::hours(1);
    let mut stale_running = ScanJob::new(user.id, ADDR).unwrap();
    stale_running.created_at = Utc::now() - chrono::Duration::hours(1);
    stale_running.start();
    let mut old_completed = ScanJob::new(user.id, ADDR).unwrap();
    old_completed.created_at = Utc::now() - chrono::Duration::hours(1);
    old_completed.complete();
    let fresh = ScanJob::new(user.id, ADDR).unwrap();
    for job in [&stale_pending, &stale_running, &old_completed, &fresh] {
        jobs.insert(job).await.unwrap();
    }

    let cutoff = Utc::now() - chrono::Duration::minutes(15);
    let stale = jobs.list_unfinished_older_than(cutoff).await.unwrap();
    let ids: Vec<_> = stale.iter().map(|j| j.id).collect();
    assert!(ids.contains(&stale_pending.id));
    assert!(ids.contains(&stale_running.id));
    assert!(!ids.contains(&old_completed.id));
    assert!(!ids.contains(&fresh.id));
}

#[tokio::test]
async fn results_roundtrip_with_replace_semantics() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);
    let job = ScanJob::new(user.id, ADDR).unwrap();
    jobs.insert(&job).await.unwrap();

    let finding = |token_symbol: &str, spender: &str, tier: RiskTier| ApprovalFinding {
        chain_id: "1".into(),
        token_address: "0xtoken000000000000000000000000000000000".into(),
        token_symbol: token_symbol.into(),
        spender_address: spender.into(),
        spender_name: Some("Some Spender".into()),
        approved_amount: "Unlimited".into(),
        tier,
        malicious_behavior: vec!["phishing_activities".into()],
        explanation: Some("explanation".into()),
        is_new: true,
        revocation_status: RevocationStatus::NotAttempted,
        revocation_tx_hash: None,
        raw: serde_json::json!({"source": "test"}),
    };

    jobs.store_results(job.id, &[finding("STALE", ADDR_A, RiskTier::Safe)])
        .await
        .unwrap();
    // Worker re-delivery replaces, never duplicates.
    jobs.store_results(
        job.id,
        &[
            finding("USDC", ADDR_A, RiskTier::Dangerous),
            finding("WETH", ADDR_B, RiskTier::Watch),
        ],
    )
    .await
    .unwrap();

    let mut stored = jobs.results_for(job.id).await.unwrap();
    stored.sort_by(|a, b| a.token_symbol.cmp(&b.token_symbol));
    let symbols: Vec<&str> = stored.iter().map(|r| r.token_symbol.as_str()).collect();
    assert_eq!(symbols, vec!["USDC", "WETH"]);
    assert_eq!(stored[0].raw["source"], "test");
    // Rich fields roundtrip (ADR-058).
    assert_eq!(stored[0].tier, RiskTier::Dangerous);
    assert_eq!(stored[0].malicious_behavior, vec!["phishing_activities"]);
    assert_eq!(stored[0].explanation.as_deref(), Some("explanation"));
    assert_eq!(stored[0].spender_name.as_deref(), Some("Some Spender"));
}

/// Migration 0012: a dry run stores `simulated`, which the pre-0012 CHECK
/// constraint would have rejected outright. `simulated` must never be read
/// back as `revoked` — that distinction is the whole point of the migration.
#[tokio::test]
async fn simulated_revocation_status_roundtrips() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);
    let job = ScanJob::new(user.id, ADDR).unwrap();
    jobs.insert(&job).await.unwrap();

    let finding = ApprovalFinding {
        chain_id: "11155111".into(),
        token_address: "0xtoken000000000000000000000000000000000".into(),
        token_symbol: "WETH".into(),
        spender_address: ADDR_A.into(),
        spender_name: None,
        approved_amount: "Unlimited".into(),
        tier: RiskTier::Dangerous,
        malicious_behavior: vec![],
        explanation: None,
        is_new: true,
        revocation_status: RevocationStatus::Simulated,
        revocation_tx_hash: None,
        raw: serde_json::Value::Null,
    };
    jobs.store_results(job.id, &[finding]).await.unwrap();

    let stored = jobs.results_for(job.id).await.unwrap();
    assert_eq!(stored[0].revocation_status, RevocationStatus::Simulated);
    assert_ne!(stored[0].revocation_status, RevocationStatus::Revoked);
    assert!(stored[0].revocation_tx_hash.is_none());
}

/// Agent mode + journal roundtrip (ADR-030): the mode survives persistence and
/// steps are idempotent on (job_id, seq), returned in order.
#[tokio::test]
async fn agent_mode_and_steps_roundtrip() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool);

    let job = ScanJob::new(user.id, ADDR)
        .unwrap()
        .with_mode(JobMode::Agent);
    jobs.insert(&job).await.unwrap();
    let stored = jobs.find(job.id).await.unwrap().unwrap();
    assert_eq!(stored.mode, JobMode::Agent);

    let step = |seq: i32, kind: &str| AgentStep {
        seq,
        kind: kind.into(),
        detail: "1".into(),
        reason: "because".into(),
        new_hits: 3,
    };
    jobs.append_step(job.id, &step(1, "scan")).await.unwrap();
    jobs.append_step(job.id, &step(1, "scan")).await.unwrap(); // Celery retry
    jobs.append_step(job.id, &step(2, "finish")).await.unwrap();

    let steps = jobs.steps_for(job.id).await.unwrap();
    assert_eq!(
        steps
            .iter()
            .map(|s| (s.seq, s.kind.as_str(), s.new_hits))
            .collect::<Vec<_>>(),
        vec![(1, "scan", 3), (2, "finish", 3)]
    );
}

#[tokio::test]
async fn recurring_search_roundtrip_due_and_memory() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let recurring_repo = PostgresRecurringSearchRepository::new(pool.clone());
    let jobs = PostgresJobRepository::new(pool.clone());

    let rs = RecurringSearch::new(user.id, ADDR, JobMode::Agent, 60, None).unwrap();
    recurring_repo.insert(&rs).await.unwrap();

    // Roundtrip + due immediately (never run).
    let listed = recurring_repo.list_for_user(user.id).await.unwrap();
    assert_eq!(listed, vec![rs.clone()]);
    assert!(recurring_repo
        .list_due(Utc::now())
        .await
        .unwrap()
        .iter()
        .any(|s| s.id == rs.id));

    // After a run: not due until the interval elapses.
    recurring_repo.mark_ran(rs.id, Utc::now()).await.unwrap();
    assert!(!recurring_repo
        .list_due(Utc::now())
        .await
        .unwrap()
        .iter()
        .any(|s| s.id == rs.id));

    // A linked run delivers results: they become the memory of the next run.
    let job = ScanJob::new(user.id, ADDR)
        .unwrap()
        .with_mode(JobMode::Agent)
        .with_recurring(rs.id);
    jobs.insert(&job).await.unwrap();
    let finding = |spender: &str| ApprovalFinding {
        chain_id: "1".into(),
        token_address: "0xtoken000000000000000000000000000000000".into(),
        token_symbol: "TKN".into(),
        spender_address: spender.into(),
        spender_name: None,
        approved_amount: "Unlimited".into(),
        tier: RiskTier::Watch,
        malicious_behavior: vec![],
        explanation: None,
        is_new: true,
        revocation_status: RevocationStatus::NotAttempted,
        revocation_tx_hash: None,
        raw: serde_json::Value::Null,
    };
    jobs.store_results(job.id, &[finding(ADDR_A), finding(ADDR_B)])
        .await
        .unwrap();
    let mut keys = jobs
        .recent_approval_keys_for_recurring(rs.id, 200)
        .await
        .unwrap();
    keys.sort();
    let mut expected = vec![
        format!("1:0xtoken000000000000000000000000000000000:{ADDR_A}"),
        format!("1:0xtoken000000000000000000000000000000000:{ADDR_B}"),
    ];
    expected.sort();
    assert_eq!(keys, expected);
    // The stored job kept its recurring link and the is_new flag roundtrips.
    let stored = jobs.find(job.id).await.unwrap().unwrap();
    assert_eq!(stored.recurring_search_id, Some(rs.id));
    assert!(jobs.results_for(job.id).await.unwrap()[0].is_new);

    // Ownership guard on delete.
    assert!(!recurring_repo.delete(Uuid::new_v4(), rs.id).await.unwrap());
    assert!(recurring_repo.delete(user.id, rs.id).await.unwrap());
    // History survives the deletion (ON DELETE SET NULL).
    let kept = jobs.find(job.id).await.unwrap().unwrap();
    assert_eq!(kept.recurring_search_id, None);
}

#[tokio::test]
async fn usage_accumulates_and_survives_lifecycle_updates() {
    let Some(pool) = pool().await else { return };
    let user = insert_user(&pool).await;
    let jobs = PostgresJobRepository::new(pool.clone());
    let mut job = ScanJob::new(user.id, ADDR).unwrap();
    jobs.insert(&job).await.unwrap();

    let attempt = backend::domain::JobUsage {
        llm_calls: 3,
        llm_input_tokens: 1000,
        llm_output_tokens: 200,
        search_calls: 1,
        cost_usd: 0.02,
    };
    jobs.add_usage(job.id, &attempt).await.unwrap();
    jobs.add_usage(job.id, &attempt).await.unwrap(); // second attempt adds

    // A lifecycle update (completion) must not clobber the accumulated spend.
    job.complete();
    jobs.update(&job).await.unwrap();

    let stored = jobs.find(job.id).await.unwrap().unwrap();
    assert_eq!(stored.usage.llm_calls, 6);
    assert_eq!(stored.usage.llm_input_tokens, 2000);
    assert_eq!(stored.usage.search_calls, 2);
    assert!((stored.usage.cost_usd - 0.04).abs() < 1e-9);
}
