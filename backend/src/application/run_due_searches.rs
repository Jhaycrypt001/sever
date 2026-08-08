//! The scheduler tick (ADR-033): launches a job for every recurring scan
//! that is due. Runs inside the backend's existing background loop (next to
//! the ADR-016 reaper) — deliberately **not** Celery beat: that would add a
//! fifth process and hand scheduling state to the brick that must not own
//! the database (ADR-006).

use std::sync::Arc;

use chrono::Utc;

use crate::domain::ports::{
    DispatchContext, JobDispatcher, JobRepository, PortError, RecurringSearchRepository,
};
use crate::domain::ScanJob;

use super::KeeperHubKeys;

/// Bound on the memory sent to the agent (task payload size).
const SEEN_APPROVAL_KEYS_LIMIT: u32 = 200;

pub struct RunDueSearches {
    recurring: Arc<dyn RecurringSearchRepository>,
    jobs: Arc<dyn JobRepository>,
    dispatcher: Arc<dyn JobDispatcher>,
    daily_quota: u32,
    /// Per-user KeeperHub keys (ADR-076). This is the path where they matter
    /// most: an unattended run is the one that can revoke a dangerous approval
    /// before the owner has even seen it.
    keeperhub_keys: Option<Arc<KeeperHubKeys>>,
}

impl RunDueSearches {
    pub fn new(
        recurring: Arc<dyn RecurringSearchRepository>,
        jobs: Arc<dyn JobRepository>,
        dispatcher: Arc<dyn JobDispatcher>,
        daily_quota: u32,
    ) -> Self {
        Self {
            recurring,
            jobs,
            dispatcher,
            daily_quota,
            keeperhub_keys: None,
        }
    }

    /// Runs scheduled scans with each owner's own KeeperHub key (ADR-076).
    #[must_use]
    pub fn with_keeperhub_keys(mut self, keys: Option<Arc<KeeperHubKeys>>) -> Self {
        self.keeperhub_keys = keys;
        self
    }

    /// Launches every due recurring scan; returns how many jobs started.
    ///
    /// Every outcome marks the scan as ran — a quota-skipped or failed run
    /// waits for the next interval instead of hammering every tick. Runs
    /// count against the owner's daily quota (ADR-017) exactly like manual
    /// scans, so a schedule cannot outspend a user.
    pub async fn execute(&self) -> Result<u32, PortError> {
        let now = Utc::now();
        let mut launched = 0;
        for search in self.recurring.list_due(now).await? {
            let since = now - chrono::Duration::hours(24);
            let used = self.jobs.count_created_since(search.user_id, since).await?;
            if used >= u64::from(self.daily_quota) {
                tracing::warn!(
                    recurring_search_id = %search.id,
                    "recurring run skipped: daily quota reached"
                );
                self.recurring.mark_ran(search.id, now).await?;
                continue;
            }

            let seen_approval_keys = self
                .jobs
                .recent_approval_keys_for_recurring(search.id, SEEN_APPROVAL_KEYS_LIMIT)
                .await?;
            let mut job = ScanJob::new(search.user_id, &search.wallet_address)
                .map_err(|e| PortError(e.to_string()))?
                .with_mode(search.mode)
                .with_recurring(search.id);
            self.jobs.insert(&job).await?;
            let api_key =
                KeeperHubKeys::dispatch_key(self.keeperhub_keys.as_ref(), search.user_id).await;
            let context = DispatchContext {
                seen_approval_keys: &seen_approval_keys,
                keeperhub_api_key: api_key.as_deref(),
            };
            if let Err(err) = self.dispatcher.dispatch(&job, context).await {
                tracing::error!(job_id = %job.id, error = %err, "recurring dispatch failed");
                job.fail(format!("dispatch failed: {err}"));
                self.jobs.update(&job).await?;
            } else {
                launched += 1;
            }
            self.recurring.mark_ran(search.id, now).await?;
        }
        Ok(launched)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::{
        InMemoryJobRepository, InMemoryRecurringSearchRepository,
    };
    use crate::domain::ports::JobRepository;
    use crate::domain::{
        ApprovalFinding, JobMode, JobStatus, RecurringSearch, RevocationStatus, RiskTier,
    };
    use async_trait::async_trait;
    use std::sync::Mutex;
    use uuid::Uuid;

    const ADDR: &str = "0x1234567890123456789012345678901234567890";

    #[derive(Default)]
    struct RecordingDispatcher {
        dispatched: Mutex<Vec<(Uuid, Vec<String>)>>,
    }

    #[async_trait]
    impl JobDispatcher for RecordingDispatcher {
        async fn dispatch(
            &self,
            job: &ScanJob,
            context: DispatchContext<'_>,
        ) -> Result<(), PortError> {
            self.dispatched
                .lock()
                .unwrap()
                .push((job.id, context.seen_approval_keys.to_vec()));
            Ok(())
        }
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

    struct Harness {
        recurring: Arc<InMemoryRecurringSearchRepository>,
        jobs: Arc<InMemoryJobRepository>,
        dispatcher: Arc<RecordingDispatcher>,
        run: RunDueSearches,
    }

    fn harness(quota: u32) -> Harness {
        let recurring = Arc::new(InMemoryRecurringSearchRepository::default());
        let jobs = Arc::new(InMemoryJobRepository::default());
        let dispatcher = Arc::new(RecordingDispatcher::default());
        let run = RunDueSearches::new(recurring.clone(), jobs.clone(), dispatcher.clone(), quota);
        Harness {
            recurring,
            jobs,
            dispatcher,
            run,
        }
    }

    #[tokio::test]
    async fn launches_due_searches_with_the_memory_of_previous_runs() {
        let h = harness(10);
        let user = Uuid::new_v4();
        let rs = RecurringSearch::new(user, ADDR, JobMode::Agent, 60, None).unwrap();
        h.recurring.insert(&rs).await.unwrap();

        // First tick: due immediately, no memory yet.
        assert_eq!(h.run.execute().await.unwrap(), 1);
        let (first_job, first_seen) = h.dispatcher.dispatched.lock().unwrap()[0].clone();
        assert!(first_seen.is_empty());
        let stored = h.jobs.find(first_job).await.unwrap().unwrap();
        assert_eq!(stored.recurring_search_id, Some(rs.id));
        assert_eq!(stored.mode, JobMode::Agent);

        // The first run delivered two approvals.
        h.jobs
            .store_results(first_job, &[a_result("0xa"), a_result("0xb")])
            .await
            .unwrap();

        // Not due again before the interval.
        assert_eq!(h.run.execute().await.unwrap(), 0);

        // Force due again: the dispatch now carries the seen approval keys.
        h.recurring
            .mark_ran(rs.id, Utc::now() - chrono::Duration::minutes(61))
            .await
            .unwrap();
        assert_eq!(h.run.execute().await.unwrap(), 1);
        let (_, second_seen) = h.dispatcher.dispatched.lock().unwrap()[1].clone();
        assert_eq!(
            {
                let mut keys = second_seen;
                keys.sort();
                keys
            },
            vec!["1:0xtoken:0xa".to_string(), "1:0xtoken:0xb".to_string()]
        );
    }

    #[tokio::test]
    async fn quota_exhausted_skips_the_run_but_marks_it_ran() {
        let h = harness(1);
        let user = Uuid::new_v4();
        // The user already spent the quota on a manual scan.
        h.jobs
            .insert(&ScanJob::new(user, ADDR).unwrap())
            .await
            .unwrap();
        let rs = RecurringSearch::new(user, ADDR, JobMode::Workflow, 60, None).unwrap();
        h.recurring.insert(&rs).await.unwrap();

        assert_eq!(h.run.execute().await.unwrap(), 0);
        assert!(h.dispatcher.dispatched.lock().unwrap().is_empty());
        // Not retried on the next tick: it waits for the next interval.
        assert_eq!(h.run.execute().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn dispatch_failure_marks_the_job_failed_and_moves_on() {
        struct FailingDispatcher;
        #[async_trait]
        impl JobDispatcher for FailingDispatcher {
            async fn dispatch(&self, _: &ScanJob, _: DispatchContext<'_>) -> Result<(), PortError> {
                Err(PortError("agent unreachable".into()))
            }
        }
        let recurring = Arc::new(InMemoryRecurringSearchRepository::default());
        let jobs = Arc::new(InMemoryJobRepository::default());
        let run = RunDueSearches::new(
            recurring.clone(),
            jobs.clone(),
            Arc::new(FailingDispatcher),
            10,
        );
        let user = Uuid::new_v4();
        let rs = RecurringSearch::new(user, ADDR, JobMode::Workflow, 60, None).unwrap();
        recurring.insert(&rs).await.unwrap();

        assert_eq!(run.execute().await.unwrap(), 0);
        let runs = jobs.list_for_user(user).await.unwrap();
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].status, JobStatus::Failed);
    }
}
