use std::sync::Arc;

use uuid::Uuid;

use crate::domain::ports::{
    Digest, DigestEntry, DigestSender, JobRepository, PortError, RecurringSearchRepository,
};
use crate::domain::{AgentStep, ApprovalFinding, JobUsage, ScanJob};

#[derive(Debug, thiserror::Error)]
pub enum IngestError {
    #[error("job not found")]
    JobNotFound,
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

/// Receives the agent's output (HTTP callback from the worker, see ADR-006).
pub struct IngestResults {
    jobs: Arc<dyn JobRepository>,
    recurring: Arc<dyn RecurringSearchRepository>,
    digests: Arc<dyn DigestSender>,
}

impl IngestResults {
    pub fn new(
        jobs: Arc<dyn JobRepository>,
        recurring: Arc<dyn RecurringSearchRepository>,
        digests: Arc<dyn DigestSender>,
    ) -> Self {
        Self {
            jobs,
            recurring,
            digests,
        }
    }

    /// Digest hook (ADR-036): a recurring run that delivered new findings
    /// notifies the saved webhook. Strictly best-effort — a dead webhook (or
    /// a deleted recurring scan) never fails the ingestion.
    async fn maybe_send_digest(&self, job: &ScanJob, results: &[ApprovalFinding]) {
        let Some(rs_id) = job.recurring_search_id else {
            return;
        };
        let new_results: Vec<&ApprovalFinding> = results.iter().filter(|r| r.is_new).collect();
        if new_results.is_empty() {
            return; // nothing new since the last run: no notification
        }
        let webhook_url = match self.recurring.find(rs_id).await {
            Ok(Some(search)) => match search.webhook_url {
                Some(url) => url,
                None => return,
            },
            Ok(None) => return, // deleted meanwhile
            Err(e) => {
                tracing::warn!(error = %e, "digest lookup failed");
                return;
            }
        };
        let digest = Digest {
            recurring_search_id: rs_id,
            job_id: job.id,
            wallet_address: job.wallet_address.clone(),
            new_count: new_results.len(),
            new_results: new_results
                .iter()
                .map(|r| DigestEntry {
                    token_symbol: r.token_symbol.clone(),
                    spender_address: r.spender_address.clone(),
                    tier: r.tier,
                    revocation_status: r.revocation_status,
                })
                .collect(),
        };
        if let Err(e) = self.digests.send(&webhook_url, &digest).await {
            tracing::warn!(job_id = %job.id, error = %e, "digest delivery failed");
        }
    }

    /// Worker picked the job up (POST /internal/jobs/{id}/started).
    pub async fn start(&self, job_id: Uuid) -> Result<(), IngestError> {
        let mut job = self
            .jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        job.start();
        self.jobs.update(&job).await?;
        Ok(())
    }

    pub async fn complete(
        &self,
        job_id: Uuid,
        results: &[ApprovalFinding],
    ) -> Result<(), IngestError> {
        let mut job = self
            .jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        self.jobs.store_results(job_id, results).await?;
        job.complete();
        self.jobs.update(&job).await?;
        // Audit trail (ADR-058/018): the moment a scan's findings land in the
        // system of record, alongside the durable rows themselves — greppable
        // without a DB query, the Rust-side half of the Python adapter's
        // per-attempt "revocation attempt" log.
        let dangerous = results
            .iter()
            .filter(|r| r.tier == crate::domain::RiskTier::Dangerous)
            .count();
        let revoked = results
            .iter()
            .filter(|r| r.revocation_status == crate::domain::RevocationStatus::Revoked)
            .count();
        tracing::info!(
            job_id = %job_id,
            wallet_address = %job.wallet_address,
            findings = results.len(),
            dangerous,
            revoked,
            "scan completed"
        );
        self.maybe_send_digest(&job, results).await;
        Ok(())
    }

    /// Records one decision of the agentic loop (ADR-030). Idempotent on
    /// `(job_id, seq)` — Celery retries re-send the same journal entries.
    pub async fn record_step(&self, job_id: Uuid, step: &AgentStep) -> Result<(), IngestError> {
        self.jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        self.jobs.append_step(job_id, step).await?;
        Ok(())
    }

    /// The agent asked a clarification question (ADR-032): the job pauses in
    /// `awaiting_input`. Idempotent like every worker notification.
    pub async fn request_input(&self, job_id: Uuid, question: &str) -> Result<(), IngestError> {
        let mut job = self
            .jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        job.request_input(question)
            .map_err(|e| IngestError::Infrastructure(PortError(e.to_string())))?;
        self.jobs.update(&job).await?;
        Ok(())
    }

    /// Accumulates one task attempt's spend (ADR-038): retries and HITL
    /// resumes each add their own real cost.
    pub async fn record_usage(&self, job_id: Uuid, usage: &JobUsage) -> Result<(), IngestError> {
        self.jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        self.jobs.add_usage(job_id, usage).await?;
        Ok(())
    }

    pub async fn fail(&self, job_id: Uuid, error: String) -> Result<(), IngestError> {
        let mut job = self
            .jobs
            .find(job_id)
            .await?
            .ok_or(IngestError::JobNotFound)?;
        job.fail(error);
        self.jobs.update(&job).await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::{
        InMemoryJobRepository, InMemoryRecurringSearchRepository,
    };
    use crate::domain::{JobMode, JobStatus, RecurringSearch, RevocationStatus, RiskTier, ScanJob};
    use std::sync::Mutex;

    const ADDR: &str = "0x1234567890123456789012345678901234567890";

    /// Records digest deliveries for assertions.
    #[derive(Default)]
    struct RecordingDigestSender {
        sent: Mutex<Vec<(String, Digest)>>,
    }

    #[async_trait::async_trait]
    impl DigestSender for RecordingDigestSender {
        async fn send(&self, url: &str, digest: &Digest) -> Result<(), PortError> {
            self.sent.lock().unwrap().push((url.into(), digest.clone()));
            Ok(())
        }
    }

    fn ingest_with(
        jobs: Arc<InMemoryJobRepository>,
        recurring: Arc<InMemoryRecurringSearchRepository>,
        digests: Arc<RecordingDigestSender>,
    ) -> IngestResults {
        IngestResults::new(jobs, recurring, digests)
    }

    fn a_result(spender: &str) -> ApprovalFinding {
        ApprovalFinding {
            chain_id: "1".into(),
            token_address: "0xtoken".into(),
            token_symbol: "TKN".into(),
            spender_address: spender.into(),
            spender_name: None,
            approved_amount: "Unlimited".into(),
            tier: RiskTier::Safe,
            malicious_behavior: vec![],
            explanation: None,
            is_new: true,
            revocation_status: RevocationStatus::NotAttempted,
            revocation_tx_hash: None,
            raw: serde_json::Value::Null,
        }
    }

    async fn repo_with_job() -> (Arc<InMemoryJobRepository>, ScanJob) {
        let jobs = Arc::new(InMemoryJobRepository::default());
        let job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        jobs.insert(&job).await.unwrap();
        (jobs, job)
    }

    #[tokio::test]
    async fn start_marks_the_job_running() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        ingest.start(job.id).await.unwrap();

        let stored = jobs.find(job.id).await.unwrap().unwrap();
        assert_eq!(stored.status, JobStatus::Running);
    }

    #[tokio::test]
    async fn late_start_does_not_reopen_a_finished_job() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        ingest
            .complete(job.id, &[a_result("0xspender")])
            .await
            .unwrap();
        ingest.start(job.id).await.unwrap(); // Celery retry after completion

        let stored = jobs.find(job.id).await.unwrap().unwrap();
        assert_eq!(stored.status, JobStatus::Completed);
    }

    #[tokio::test]
    async fn stores_results_and_completes_the_job() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        ingest
            .complete(job.id, &[a_result("0xa"), a_result("0xb")])
            .await
            .unwrap();

        let stored = jobs.find(job.id).await.unwrap().unwrap();
        assert_eq!(stored.status, JobStatus::Completed);
        assert_eq!(jobs.results_for(job.id).await.unwrap().len(), 2);
    }

    #[tokio::test]
    async fn records_agent_failure() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        ingest
            .fail(job.id, "GoPlus quota exceeded".into())
            .await
            .unwrap();

        let stored = jobs.find(job.id).await.unwrap().unwrap();
        assert_eq!(stored.status, JobStatus::Failed);
        assert_eq!(stored.error.as_deref(), Some("GoPlus quota exceeded"));
    }

    #[tokio::test]
    async fn unknown_job_is_an_error() {
        let jobs = Arc::new(InMemoryJobRepository::default());
        let ingest = IngestResults::new(
            jobs,
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        let err = ingest.complete(Uuid::new_v4(), &[]).await.unwrap_err();
        assert!(matches!(err, IngestError::JobNotFound));
    }

    fn a_step(seq: i32, kind: &str) -> AgentStep {
        AgentStep {
            seq,
            kind: kind.into(),
            detail: "1".into(),
            reason: "because".into(),
            new_hits: 2,
        }
    }

    #[tokio::test]
    async fn records_agent_steps_idempotently() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );

        ingest
            .record_step(job.id, &a_step(1, "scan"))
            .await
            .unwrap();
        ingest
            .record_step(job.id, &a_step(1, "scan"))
            .await
            .unwrap(); // retry
        ingest
            .record_step(job.id, &a_step(2, "finish"))
            .await
            .unwrap();

        let steps = jobs.steps_for(job.id).await.unwrap();
        assert_eq!(steps.len(), 2);
        assert_eq!(
            steps
                .iter()
                .map(|s| (s.seq, s.kind.as_str()))
                .collect::<Vec<_>>(),
            vec![(1, "scan"), (2, "finish")]
        );
    }

    #[tokio::test]
    async fn request_input_pauses_a_running_job() {
        let (jobs, job) = repo_with_job().await;
        let ingest = IngestResults::new(
            jobs.clone(),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );
        ingest.start(job.id).await.unwrap();

        ingest
            .request_input(job.id, "Which chains should I scan?")
            .await
            .unwrap();

        let stored = jobs.find(job.id).await.unwrap().unwrap();
        assert_eq!(stored.status, JobStatus::AwaitingInput);
        assert_eq!(
            stored.question.as_deref(),
            Some("Which chains should I scan?")
        );
    }

    #[tokio::test]
    async fn step_for_an_unknown_job_is_an_error() {
        let ingest = IngestResults::new(
            Arc::new(InMemoryJobRepository::default()),
            Arc::new(InMemoryRecurringSearchRepository::default()),
            Arc::new(RecordingDigestSender::default()),
        );
        let err = ingest
            .record_step(Uuid::new_v4(), &a_step(1, "scan"))
            .await
            .unwrap_err();
        assert!(matches!(err, IngestError::JobNotFound));
    }

    #[tokio::test]
    async fn recurring_run_with_news_delivers_a_digest() {
        let jobs = Arc::new(InMemoryJobRepository::default());
        let recurring = Arc::new(InMemoryRecurringSearchRepository::default());
        let digests = Arc::new(RecordingDigestSender::default());
        let ingest = ingest_with(jobs.clone(), recurring.clone(), digests.clone());

        let rs = RecurringSearch::new(
            Uuid::new_v4(),
            ADDR,
            JobMode::Agent,
            60,
            Some("https://hooks.example.com/digest"),
        )
        .unwrap();
        recurring.insert(&rs).await.unwrap();
        let job = ScanJob::new(rs.user_id, ADDR)
            .unwrap()
            .with_recurring(rs.id);
        jobs.insert(&job).await.unwrap();

        let mut seen = a_result("0xalready-seen");
        seen.is_new = false;
        ingest
            .complete(job.id, &[a_result("0xfresh"), seen])
            .await
            .unwrap();

        let sent = digests.sent.lock().unwrap();
        assert_eq!(sent.len(), 1);
        let (url, digest) = &sent[0];
        assert_eq!(url, "https://hooks.example.com/digest");
        assert_eq!(digest.wallet_address, ADDR);
        assert_eq!(digest.new_count, 1);
        // Only the NEW findings ride in the digest.
        assert_eq!(digest.new_results[0].spender_address, "0xfresh");
    }

    #[tokio::test]
    async fn no_digest_without_news_webhook_or_recurring_link() {
        let jobs = Arc::new(InMemoryJobRepository::default());
        let recurring = Arc::new(InMemoryRecurringSearchRepository::default());
        let digests = Arc::new(RecordingDigestSender::default());
        let ingest = ingest_with(jobs.clone(), recurring.clone(), digests.clone());

        // One-shot job: never a digest, even with new findings.
        let one_shot = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
        jobs.insert(&one_shot).await.unwrap();
        ingest
            .complete(one_shot.id, &[a_result("0xr")])
            .await
            .unwrap();

        // Recurring without a webhook: no digest.
        let silent = RecurringSearch::new(Uuid::new_v4(), ADDR, JobMode::Agent, 60, None).unwrap();
        recurring.insert(&silent).await.unwrap();
        let job = ScanJob::new(silent.user_id, ADDR)
            .unwrap()
            .with_recurring(silent.id);
        jobs.insert(&job).await.unwrap();
        ingest.complete(job.id, &[a_result("0xr")]).await.unwrap();

        // Recurring with a webhook but nothing new: no digest.
        let hooked = RecurringSearch::new(
            Uuid::new_v4(),
            ADDR,
            JobMode::Agent,
            60,
            Some("https://hooks.example.com/x"),
        )
        .unwrap();
        recurring.insert(&hooked).await.unwrap();
        let job2 = ScanJob::new(hooked.user_id, ADDR)
            .unwrap()
            .with_recurring(hooked.id);
        jobs.insert(&job2).await.unwrap();
        let mut old = a_result("0xold");
        old.is_new = false;
        ingest.complete(job2.id, &[old]).await.unwrap();

        assert!(digests.sent.lock().unwrap().is_empty());
    }
}
