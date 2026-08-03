use chrono::{DateTime, Utc};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq)]
pub struct User {
    pub id: Uuid,
    pub email: String,
    pub password_hash: String,
    /// When the address proved it can receive mail (ADR-062). `None` means the
    /// credentials exist but no session may be issued for them.
    pub email_verified_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
}

impl User {
    /// A newly registered, not-yet-verified account.
    pub fn new(email: String, password_hash: String) -> Self {
        Self {
            id: Uuid::new_v4(),
            email,
            password_hash,
            email_verified_at: None,
            created_at: super::now_utc(),
        }
    }

    pub fn is_verified(&self) -> bool {
        self.email_verified_at.is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_account_starts_unverified() {
        // ADR-062: registration alone must never be enough to sign in.
        let user = User::new("a@b.com".into(), "hash".into());
        assert!(!user.is_verified());
        assert_eq!(user.email_verified_at, None);
    }
}
