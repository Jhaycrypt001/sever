use std::sync::Arc;

use crate::domain::ports::{PasswordHasher, PortError, UserRepository};
use crate::domain::User;

#[derive(Debug, thiserror::Error)]
pub enum LoginError {
    #[error("invalid credentials")]
    InvalidCredentials,
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

/// Checks a password. **Does not sign anyone in** (ADR-063).
///
/// Every sign-in takes two factors: the password, and a code mailed to the
/// address. This use case owns the first one only; it returns the account so
/// the caller can send that account its code. `SessionIssuer` is reached
/// exclusively through `ConfirmEmailVerification`, which means there is one
/// place in the codebase where a session can come into existence, and it is
/// downstream of the code.
pub struct LoginUser {
    users: Arc<dyn UserRepository>,
    hasher: Arc<dyn PasswordHasher>,
}

impl LoginUser {
    pub fn new(users: Arc<dyn UserRepository>, hasher: Arc<dyn PasswordHasher>) -> Self {
        Self { users, hasher }
    }

    pub async fn execute(&self, email: &str, password: &str) -> Result<User, LoginError> {
        let email = email.trim().to_lowercase();
        let user = self
            .users
            .find_by_email(&email)
            .await?
            .ok_or(LoginError::InvalidCredentials)?;
        if !self.hasher.verify(password, &user.password_hash) {
            return Err(LoginError::InvalidCredentials);
        }
        // Whether the address was verified before does not change the answer:
        // both cases mail a code and land on the same screen. That is also why
        // login can no longer be used to ask "is this address registered?".
        Ok(user)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::InMemoryUserRepository;
    use crate::domain::ports::TokenService;
    use uuid::Uuid;

    pub(crate) struct FakeHasher;
    impl PasswordHasher for FakeHasher {
        fn hash(&self, password: &str) -> Result<String, PortError> {
            Ok(format!("hashed:{password}"))
        }
        fn verify(&self, password: &str, hash: &str) -> bool {
            hash == format!("hashed:{password}")
        }
    }

    pub(crate) struct FakeTokens;
    impl TokenService for FakeTokens {
        fn issue(&self, user_id: Uuid) -> Result<String, PortError> {
            Ok(format!("token-for:{user_id}"))
        }
        fn verify(&self, token: &str) -> Option<Uuid> {
            token
                .strip_prefix("token-for:")
                .and_then(|id| Uuid::parse_str(id).ok())
        }
    }

    /// A user whose address has already been verified — the ordinary case.
    pub(crate) fn verified(email: &str) -> User {
        let mut user = User::new(email.into(), "hashed:good-password".into());
        user.email_verified_at = Some(chrono::Utc::now());
        user
    }

    async fn login_for(user: User) -> (LoginUser, User) {
        let users = Arc::new(InMemoryUserRepository::default());
        users.insert(&user).await.unwrap();
        (LoginUser::new(users, Arc::new(FakeHasher)), user)
    }

    async fn login_with_user() -> (LoginUser, User) {
        login_for(verified("a@b.com")).await
    }

    #[tokio::test]
    async fn a_correct_password_returns_the_account_and_no_session() {
        // ADR-063: the password is one factor of two. Sessions are issued by
        // `ConfirmEmailVerification` alone, downstream of the emailed code —
        // this use case has no access to a `SessionIssuer` at all, which is
        // what makes "login cannot sign you in" a compile-time property.
        let (login, user) = login_with_user().await;

        let returned = login.execute("a@b.com", "good-password").await.unwrap();

        assert_eq!(returned.id, user.id);
    }

    #[tokio::test]
    async fn an_unverified_account_passes_the_password_check_like_any_other() {
        // Both cases go on to be mailed a code, so login must not branch on
        // verification — branching is what made it an enumeration oracle.
        let (login, user) =
            login_for(User::new("u@b.com".into(), "hashed:good-password".into())).await;

        let returned = login.execute("u@b.com", "good-password").await.unwrap();

        assert_eq!(returned.id, user.id);
        assert!(!returned.is_verified());
    }

    #[tokio::test]
    async fn rejects_wrong_password() {
        let (login, _) = login_with_user().await;
        let err = login.execute("a@b.com", "wrong").await.unwrap_err();
        assert!(matches!(err, LoginError::InvalidCredentials));
    }

    #[tokio::test]
    async fn rejects_unknown_email() {
        let (login, _) = login_with_user().await;
        let err = login
            .execute("nobody@b.com", "good-password")
            .await
            .unwrap_err();
        assert!(matches!(err, LoginError::InvalidCredentials));
    }
}
