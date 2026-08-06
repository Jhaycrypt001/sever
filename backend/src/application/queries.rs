use std::sync::Arc;

use uuid::Uuid;

use crate::domain::ports::{JobRepository, PortError};
use crate::domain::{sort_by_risk, AgentStep, ApprovalFinding, ScanJob};

/// How much scan history the console is served in one response (ADR-075).
///
/// The list is unpaginated and fetched on every console load, so its cost
/// grows with the account's whole history while the panel only ever shows a
/// dozen rows. 50 covers "what have I run lately", which is what the panel is
/// for; the individual job endpoint still serves any older scan by id, so
/// nothing becomes unreachable.
pub const HISTORY_LIMIT: usize = 50;

/// Read-side use cases. Ownership is enforced here: a user can only see their own jobs.
pub struct SearchQueries {
    jobs: Arc<dyn JobRepository>,
}

impl SearchQueries {
    pub fn new(jobs: Arc<dyn JobRepository>) -> Self {
        Self { jobs }
    }

    /// The account's most recent scans, newest first, capped at
    /// [`HISTORY_LIMIT`].
    pub async fn list(&self, user_id: Uuid) -> Result<Vec<ScanJob>, PortError> {
        let mut jobs = self.jobs.list_for_user(user_id).await?;
        jobs.truncate(HISTORY_LIMIT);
        Ok(jobs)
    }

    /// Returns the job with its findings sorted most-dangerous-first (ADR-058)
    /// and its agent journal (ADR-030, empty in workflow mode), or `None` if
    /// the job does not exist or belongs to another user.
    pub async fn get(
        &self,
        user_id: Uuid,
        job_id: Uuid,
    ) -> Result<Option<(ScanJob, Vec<ApprovalFinding>, Vec<AgentStep>)>, PortError> {
        let Some(job) = self.jobs.find(job_id).await? else {
            return Ok(None);
        };
        if job.user_id != user_id {
            return Ok(None);
        }
        let mut results = self.jobs.results_for(job_id).await?;
        sort_by_risk(&mut results);
        let steps = self.jobs.steps_for(job_id).await?;
        Ok(Some((job, results, steps)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::InMemoryJobRepository;

    const ADDR: &str = "0x1234567890123456789012345678901234567890";

    #[tokio::test]
    async fn the_history_is_capped_and_keeps_the_newest_scans() {
        // Without a cap this returns every scan the account ever ran. The
        // console fetches it on every load, so an account that has scanned a
        // few hundred times pays for all of them to render a list that shows
        // about a dozen. The newest are the ones anyone is looking for.
        let jobs = Arc::new(InMemoryJobRepository::default());
        let user = Uuid::new_v4();
        for _ in 0..(HISTORY_LIMIT + 25) {
            let job = ScanJob::new(user, ADDR).unwrap();
            jobs.insert(&job).await.unwrap();
        }
        let queries = SearchQueries::new(jobs.clone());

        let listed = queries.list(user).await.unwrap();
        assert_eq!(listed.len(), HISTORY_LIMIT);

        // Newest first, and the cap takes from that end rather than the tail.
        let all = jobs.list_for_user(user).await.unwrap();
        assert_eq!(listed.first().unwrap().id, all.first().unwrap().id);
    }

    #[tokio::test]
    async fn a_user_cannot_read_another_users_job() {
        let jobs = Arc::new(InMemoryJobRepository::default());
        let owner = Uuid::new_v4();
        let job = ScanJob::new(owner, ADDR).unwrap();
        jobs.insert(&job).await.unwrap();
        let queries = SearchQueries::new(jobs);

        assert!(queries.get(owner, job.id).await.unwrap().is_some());
        assert!(queries.get(Uuid::new_v4(), job.id).await.unwrap().is_none());
    }
}
