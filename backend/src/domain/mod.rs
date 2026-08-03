pub mod approval_finding;
pub mod email_verification;
pub mod job;
pub mod ports;
pub mod recurring;
pub mod refresh_token;
pub mod security_event;
pub mod user;

pub use approval_finding::{sort_by_risk, ApprovalFinding, RevocationStatus, RiskTier};
pub use email_verification::{CodePurpose, EmailVerification};
pub use job::{AgentStep, JobMode, JobStatus, JobUsage, ScanJob};
pub use recurring::RecurringSearch;
pub use refresh_token::RefreshToken;
pub use security_event::{SecurityEvent, SecurityEventKind};
pub use user::User;

use chrono::{DateTime, SubsecRound, Utc};

/// Now, truncated to microseconds. PostgreSQL `TIMESTAMPTZ` stores microsecond
/// precision, so domain timestamps must not carry nanoseconds or they would
/// not survive a persistence roundtrip (equality would break after reload).
pub(crate) fn now_utc() -> DateTime<Utc> {
    Utc::now().trunc_subsecs(6)
}

#[cfg(test)]
mod tests {
    #[test]
    fn now_utc_has_at_most_microsecond_precision() {
        use chrono::Timelike;
        for _ in 0..100 {
            assert_eq!(super::now_utc().nanosecond() % 1_000, 0);
        }
    }
}
