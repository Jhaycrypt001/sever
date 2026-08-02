use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Pending,
    Running,
    /// The agent asked the user a clarification question (ADR-032): the job is
    /// paused — not stuck, so the reaper leaves it alone — until the answer
    /// arrives and re-dispatches it.
    #[serde(rename = "awaiting_input")]
    AwaitingInput,
    Completed,
    Failed,
}

/// How the scan runs (ADR-030/058): the fixed pipeline, or the agentic loop
/// where the LLM policy decides which chains to scan, when to stop, and
/// (agent mode only) auto-revokes DANGEROUS-tier approvals afterward. The
/// default keeps pre-ADR-058 clients and payloads working unchanged.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default, ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum JobMode {
    #[default]
    Workflow,
    Agent,
}

/// One decision of the agentic loop (ADR-030/058), recorded for the live
/// journal. `kind` stays an open string ("scan" / "finish" / "revoke" /
/// "report" today) so newer agents can introduce step kinds without breaking
/// older backends.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, ToSchema)]
pub struct AgentStep {
    pub seq: i32,
    pub kind: String,
    pub detail: String,
    pub reason: String,
    #[serde(default)]
    pub new_hits: i32,
}

/// Per-run API spend (ADR-038). Accumulates across task attempts and HITL
/// resumes — each attempt spends real provider credits.
#[derive(Debug, Clone, Copy, PartialEq, Default, Serialize, Deserialize, ToSchema)]
pub struct JobUsage {
    pub llm_calls: i32,
    pub llm_input_tokens: i64,
    pub llm_output_tokens: i64,
    pub search_calls: i32,
    pub cost_usd: f64,
}

impl JobUsage {
    pub fn add(&mut self, other: &JobUsage) {
        self.llm_calls += other.llm_calls;
        self.llm_input_tokens += other.llm_input_tokens;
        self.llm_output_tokens += other.llm_output_tokens;
        self.search_calls += other.search_calls;
        self.cost_usd += other.cost_usd;
    }
}

/// Input cap (ADR-056): reject oversized free-text at the domain boundary,
/// before it reaches storage, the agent or an outbound webhook. Measured in
/// Unicode scalar values (`chars`), generous enough that no legitimate value
/// hits it — it only stops abusive/accidental multi-KB payloads.
pub const MAX_ANSWER_LEN: usize = 2_000;

/// True for a syntactically valid EVM address: `0x` followed by exactly 40
/// hex digits. Deliberately no checksum validation (EIP-55 mixed-case) — a
/// lowercase address is common and legitimate; correctness of the address
/// itself is verified onchain when KeeperHub submits the revocation, not here.
pub(crate) fn is_valid_evm_address(value: &str) -> bool {
    value
        .strip_prefix("0x")
        .is_some_and(|hex| hex.len() == 40 && hex.chars().all(|c| c.is_ascii_hexdigit()))
}

#[derive(Debug, thiserror::Error, PartialEq)]
pub enum JobError {
    #[error("wallet address must not be empty")]
    EmptyWalletAddress,
    #[error("wallet address must be a 0x-prefixed 40 hex character EVM address")]
    InvalidWalletAddress,
    #[error("interval must be between 1 minute and 7 days")]
    InvalidInterval,
    #[error("webhook url must start with http:// or https://")]
    InvalidWebhookUrl,
    #[error("webhook url must be at most 2048 characters")]
    WebhookUrlTooLong,
    #[error("question must not be empty")]
    EmptyQuestion,
    #[error("answer must not be empty")]
    EmptyAnswer,
    #[error("answer must be at most 2000 characters")]
    AnswerTooLong,
    #[error("job is not awaiting input")]
    NotAwaitingInput,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScanJob {
    pub id: Uuid,
    pub user_id: Uuid,
    pub wallet_address: String,
    pub mode: JobMode,
    pub status: JobStatus,
    pub error: Option<String>,
    /// Clarification dialog (ADR-032): the agent's question and, once the
    /// user replied, the answer forwarded back to the agent on re-dispatch.
    pub question: Option<String>,
    pub answer: Option<String>,
    /// Set when the job was launched by the scheduler for a recurring scan
    /// (ADR-033); one-shot scans leave it null.
    pub recurring_search_id: Option<Uuid>,
    /// Accumulated API spend (ADR-038); written only through
    /// `JobRepository::add_usage`, never by `update`.
    pub usage: JobUsage,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

impl ScanJob {
    pub fn new(user_id: Uuid, wallet_address: &str) -> Result<Self, JobError> {
        let wallet_address = wallet_address.trim();
        if wallet_address.is_empty() {
            return Err(JobError::EmptyWalletAddress);
        }
        if !is_valid_evm_address(wallet_address) {
            return Err(JobError::InvalidWalletAddress);
        }
        Ok(Self {
            id: Uuid::new_v4(),
            user_id,
            wallet_address: wallet_address.to_string(),
            mode: JobMode::default(),
            status: JobStatus::Pending,
            error: None,
            question: None,
            answer: None,
            recurring_search_id: None,
            usage: JobUsage::default(),
            created_at: super::now_utc(),
            completed_at: None,
        })
    }

    pub fn with_mode(mut self, mode: JobMode) -> Self {
        self.mode = mode;
        self
    }

    /// Links a scheduler-launched run to its recurring scan (ADR-033).
    pub fn with_recurring(mut self, recurring_search_id: Uuid) -> Self {
        self.recurring_search_id = Some(recurring_search_id);
        self
    }

    /// Worker picked the job up. Only a pending job transitions; anything else
    /// is a no-op so retried/out-of-order notifications stay harmless.
    pub fn start(&mut self) {
        if self.status == JobStatus::Pending {
            self.status = JobStatus::Running;
        }
    }

    /// Completing always wins: results arriving after a timeout-failure are
    /// still valuable, so a late completion overwrites `Failed`.
    pub fn complete(&mut self) {
        self.status = JobStatus::Completed;
        self.error = None;
        self.completed_at = Some(super::now_utc());
    }

    /// A failure never clobbers a completed job (late duplicate callbacks).
    pub fn fail(&mut self, error: String) {
        if self.status == JobStatus::Completed {
            return;
        }
        self.status = JobStatus::Failed;
        self.error = Some(error);
        self.completed_at = Some(super::now_utc());
    }

    /// The agent asked a clarification question (ADR-032). Only a running job
    /// pauses; a repeat of the same notification (Celery retry) is a no-op,
    /// and a question never reopens a finished job.
    pub fn request_input(&mut self, question: &str) -> Result<(), JobError> {
        let question = question.trim();
        if question.is_empty() {
            return Err(JobError::EmptyQuestion);
        }
        if self.status == JobStatus::Running || self.status == JobStatus::Pending {
            self.status = JobStatus::AwaitingInput;
            self.question = Some(question.to_string());
        }
        Ok(())
    }

    /// The user answered (ADR-032): the job goes back to `pending` for
    /// re-dispatch, carrying the answer as the clarification.
    pub fn provide_answer(&mut self, answer: &str) -> Result<(), JobError> {
        let answer = answer.trim();
        if answer.is_empty() {
            return Err(JobError::EmptyAnswer);
        }
        if answer.chars().count() > MAX_ANSWER_LEN {
            return Err(JobError::AnswerTooLong);
        }
        if self.status != JobStatus::AwaitingInput {
            return Err(JobError::NotAwaitingInput);
        }
        self.answer = Some(answer.to_string());
        self.status = JobStatus::Pending;
        Ok(())
    }

    pub fn is_finished(&self) -> bool {
        matches!(self.status, JobStatus::Completed | JobStatus::Failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ADDR: &str = "0x1234567890123456789012345678901234567890";

    #[test]
    fn new_job_starts_pending_with_trimmed_wallet_address() {
        let job = ScanJob::new(Uuid::new_v4(), &format!("  {ADDR}  ")).unwrap();
        assert_eq!(job.status, JobStatus::Pending);
        assert_eq!(job.wallet_address, ADDR);
        assert!(job.error.is_none());
        assert!(job.completed_at.is_none());
    }

    #[test]
    fn empty_wallet_address_is_rejected() {
        let err = ScanJob::new(Uuid::new_v4(), "   ").unwrap_err();
        assert_eq!(err, JobError::EmptyWalletAddress);
    }

    #[test]
    fn malformed_wallet_address_is_rejected() {
        for bad in [
            "0x123",
            "not-an-address",
            "1234567890123456789012345678901234567890",
        ] {
            assert_eq!(
                ScanJob::new(Uuid::new_v4(), bad).unwrap_err(),
                JobError::InvalidWalletAddress
            );
        }
        assert!(is_valid_evm_address(ADDR));
    }

    #[test]
    fn overlong_answer_is_rejected() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.request_input("which one?").unwrap();
        assert_eq!(
            job.provide_answer(&"x".repeat(MAX_ANSWER_LEN + 1))
                .unwrap_err(),
            JobError::AnswerTooLong
        );
    }

    #[test]
    fn complete_sets_status_and_timestamp() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.complete();
        assert_eq!(job.status, JobStatus::Completed);
        assert!(job.completed_at.is_some());
    }

    #[test]
    fn fail_records_the_error() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.fail("boom".into());
        assert_eq!(job.status, JobStatus::Failed);
        assert_eq!(job.error.as_deref(), Some("boom"));
    }

    #[test]
    fn start_transitions_only_from_pending() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.start();
        assert_eq!(job.status, JobStatus::Running);

        job.complete();
        job.start(); // late/duplicate notification is a no-op
        assert_eq!(job.status, JobStatus::Completed);
    }

    #[test]
    fn late_completion_overwrites_a_timeout_failure() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.fail("timed out".into());
        job.complete();
        assert_eq!(job.status, JobStatus::Completed);
        assert!(job.error.is_none());
    }

    #[test]
    fn failure_never_clobbers_a_completed_job() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.complete();
        job.fail("late duplicate".into());
        assert_eq!(job.status, JobStatus::Completed);
        assert!(job.error.is_none());
    }

    #[test]
    fn request_input_pauses_a_running_job_idempotently() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.start();
        job.request_input("Which chains?").unwrap();
        assert_eq!(job.status, JobStatus::AwaitingInput);
        assert_eq!(job.question.as_deref(), Some("Which chains?"));

        job.request_input("Which chains?").unwrap(); // Celery retry
        assert_eq!(job.status, JobStatus::AwaitingInput);
    }

    #[test]
    fn request_input_never_reopens_a_finished_job() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.complete();
        job.request_input("late question").unwrap();
        assert_eq!(job.status, JobStatus::Completed);
        assert!(job.question.is_none());
    }

    #[test]
    fn empty_question_is_rejected() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.start();
        assert_eq!(job.request_input("  "), Err(JobError::EmptyQuestion));
    }

    #[test]
    fn provide_answer_requeues_the_job_with_the_answer() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.start();
        job.request_input("Which chains?").unwrap();

        job.provide_answer(" ethereum only ").unwrap();

        assert_eq!(job.status, JobStatus::Pending);
        assert_eq!(job.answer.as_deref(), Some("ethereum only"));
        assert_eq!(job.question.as_deref(), Some("Which chains?"));
    }

    #[test]
    fn provide_answer_requires_the_awaiting_state_and_a_non_empty_answer() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        assert_eq!(
            job.provide_answer("ethereum"),
            Err(JobError::NotAwaitingInput)
        );
        job.start();
        job.request_input("q?").unwrap();
        assert_eq!(job.provide_answer("   "), Err(JobError::EmptyAnswer));
    }

    #[test]
    fn awaiting_input_is_not_a_terminal_state() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        job.start();
        job.request_input("q?").unwrap();
        assert!(!job.is_finished());
    }

    #[test]
    fn is_finished_matches_terminal_states() {
        let mut job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        assert!(!job.is_finished());
        job.start();
        assert!(!job.is_finished());
        job.complete();
        assert!(job.is_finished());
    }
}
