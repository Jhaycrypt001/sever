//! Account recovery (ADR-063).
//!
//! Forgetting a password must not mean losing the wallets you were watching.
//! Recovery runs on the same mailed-code mechanism as sign-in, with its own
//! `CodePurpose` so the two can never be traded for one another: a sign-in code
//! cannot set a password, and a reset code cannot open a session by itself.
//!
//! Completing a reset **does** sign the person in, because by then they have
//! shown both of the things an ordinary sign-in asks for: possession of the
//! mailbox (the code) and knowledge of the password (the one they just chose).
//! Making them log in again afterwards would only send a second email.
//!
//! Resetting also verifies the address, for the same reason: answering a code
//! sent to it is exactly the proof `email_verified_at` records.

use std::sync::Arc;

use super::session::{SessionIssuer, SessionTokens};
use crate::domain::ports::{
    EmailSender, EmailVerificationRepository, PasswordHasher, PortError, RefreshTokenRepository,
    UserRepository,
};
use crate::domain::{CodePurpose, EmailVerification};

/// Minimum password length, shared with registration so recovery cannot be
/// used to sneak a weaker password past the rule.
pub const MIN_PASSWORD_LENGTH: usize = 8;

#[derive(Debug, thiserror::Error)]
pub enum RequestResetError {
    #[error("could not send the password reset email: {0}")]
    Delivery(PortError),
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

pub struct RequestPasswordReset {
    users: Arc<dyn UserRepository>,
    codes: Arc<dyn EmailVerificationRepository>,
    mailer: Arc<dyn EmailSender>,
    ttl_minutes: i64,
}

impl RequestPasswordReset {
    pub fn new(
        users: Arc<dyn UserRepository>,
        codes: Arc<dyn EmailVerificationRepository>,
        mailer: Arc<dyn EmailSender>,
        ttl_minutes: i64,
    ) -> Self {
        Self {
            users,
            codes,
            mailer,
            ttl_minutes,
        }
    }

    /// Mails a reset code. `None` when the address has no account.
    ///
    /// The caller must answer identically either way — "no account with that
    /// address" is precisely the sentence that turns a forgot-password form
    /// into an account enumeration tool, and this form is unauthenticated by
    /// definition.
    pub async fn execute(&self, email: &str) -> Result<Option<String>, RequestResetError> {
        let email = email.trim().to_lowercase();
        let Some(user) = self.users.find_by_email(&email).await? else {
            return Ok(None);
        };

        let (record, code) =
            EmailVerification::issue(user.id, CodePurpose::Reset, self.ttl_minutes);
        self.codes.insert(&record).await?;
        self.mailer
            .send_code(&user.email, &code, self.ttl_minutes, CodePurpose::Reset)
            .await
            .map_err(RequestResetError::Delivery)?;
        Ok(Some(code))
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ResetPasswordError {
    #[error("invalid or expired reset code")]
    InvalidCode,
    #[error("that code has been replaced by a newer one — check your most recent email")]
    Superseded,
    #[error("that code has expired, request a new one")]
    Expired,
    #[error("too many incorrect attempts, request a new code")]
    TooManyAttempts,
    #[error("password must be at least {MIN_PASSWORD_LENGTH} characters")]
    PasswordTooShort,
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

pub struct ResetPassword {
    users: Arc<dyn UserRepository>,
    codes: Arc<dyn EmailVerificationRepository>,
    hasher: Arc<dyn PasswordHasher>,
    refresh_tokens: Arc<dyn RefreshTokenRepository>,
    sessions: Arc<SessionIssuer>,
}

impl ResetPassword {
    pub fn new(
        users: Arc<dyn UserRepository>,
        codes: Arc<dyn EmailVerificationRepository>,
        hasher: Arc<dyn PasswordHasher>,
        refresh_tokens: Arc<dyn RefreshTokenRepository>,
        sessions: Arc<SessionIssuer>,
    ) -> Self {
        Self {
            users,
            codes,
            hasher,
            refresh_tokens,
            sessions,
        }
    }

    pub async fn execute(
        &self,
        email: &str,
        code: &str,
        new_password: &str,
    ) -> Result<SessionTokens, ResetPasswordError> {
        // Length is checked before anything is looked up, so a too-short
        // password never burns the code the person will need on their retry.
        if new_password.len() < MIN_PASSWORD_LENGTH {
            return Err(ResetPasswordError::PasswordTooShort);
        }

        let email = email.trim().to_lowercase();
        let user = self
            .users
            .find_by_email(&email)
            .await?
            .ok_or(ResetPasswordError::InvalidCode)?;

        let hash = EmailVerification::hash(code);
        let record = match self
            .codes
            .active_for_user(user.id, CodePurpose::Reset)
            .await?
        {
            Some(record) => record,
            None => {
                return Err(
                    match self
                        .codes
                        .find_by_hash(user.id, CodePurpose::Reset, &hash)
                        .await?
                    {
                        Some(_) => ResetPasswordError::Superseded,
                        None => ResetPasswordError::InvalidCode,
                    },
                )
            }
        };

        let now = crate::domain::now_utc();
        if record.is_exhausted() {
            return Err(ResetPasswordError::TooManyAttempts);
        }
        if record.is_expired(now) {
            return Err(ResetPasswordError::Expired);
        }
        if !record.matches(code) {
            // A stale reset email must not spend an attempt on the live code
            // (ADR-063), same rule as sign-in codes.
            if self
                .codes
                .find_by_hash(user.id, CodePurpose::Reset, &hash)
                .await?
                .is_some()
            {
                return Err(ResetPasswordError::Superseded);
            }
            self.codes.record_attempt(record.id).await?;
            return Err(ResetPasswordError::InvalidCode);
        }

        let password_hash = self.hasher.hash(new_password)?;
        self.codes.mark_consumed(record.id, now).await?;
        self.users
            .update_password_hash(user.id, &password_hash)
            .await?;
        // Answering a code mailed to the address is the same proof the verify
        // flow collects, so a recovered account is a verified one.
        self.users.mark_email_verified(user.id, now).await?;

        // Every existing session dies with the old password (ADR-008/056).
        // Someone resetting because they fear a compromise expects exactly
        // that; leaving the attacker's refresh cookie alive would defeat the
        // reason most people reach for this form.
        self.refresh_tokens.delete_for_user(user.id).await?;

        Ok(self.sessions.issue(user.id).await?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::email::DevEmailSender;
    use crate::adapters::persistence::in_memory::{
        InMemoryEmailVerificationRepository, InMemoryRefreshTokenRepository, InMemoryUserRepository,
    };
    use crate::application::login_user::tests::{verified, FakeHasher, FakeTokens};
    use crate::domain::email_verification::MAX_ATTEMPTS;
    use crate::domain::{RefreshToken, User};

    struct World {
        request: RequestPasswordReset,
        reset: ResetPassword,
        users: Arc<InMemoryUserRepository>,
        codes: Arc<InMemoryEmailVerificationRepository>,
        refresh: Arc<InMemoryRefreshTokenRepository>,
        mailer: Arc<DevEmailSender>,
    }

    async fn world() -> (World, User) {
        let users = Arc::new(InMemoryUserRepository::default());
        let codes = Arc::new(InMemoryEmailVerificationRepository::default());
        let refresh = Arc::new(InMemoryRefreshTokenRepository::default());
        let mailer = Arc::new(DevEmailSender::default());
        let sessions = Arc::new(SessionIssuer::new(
            Arc::new(FakeTokens),
            refresh.clone(),
            30,
        ));
        let user = verified("alice@example.com");
        users.insert(&user).await.unwrap();
        (
            World {
                request: RequestPasswordReset::new(
                    users.clone(),
                    codes.clone(),
                    mailer.clone(),
                    10,
                ),
                reset: ResetPassword::new(
                    users.clone(),
                    codes.clone(),
                    Arc::new(FakeHasher),
                    refresh.clone(),
                    sessions,
                ),
                users,
                codes,
                refresh,
                mailer,
            },
            user,
        )
    }

    #[tokio::test]
    async fn a_reset_sets_the_new_password_and_signs_in() {
        let (w, user) = world().await;
        let code = w
            .request
            .execute("Alice@Example.com")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(w.mailer.codes_for("alice@example.com"), vec![code.clone()]);

        let tokens = w
            .reset
            .execute("alice@example.com", &code, "brand-new-password")
            .await
            .unwrap();

        assert_eq!(tokens.access_token, format!("token-for:{}", user.id));
        let reloaded = w
            .users
            .find_by_email("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(reloaded.password_hash, "hashed:brand-new-password");
    }

    #[tokio::test]
    async fn a_reset_revokes_every_existing_session() {
        // The main reason to reset is "someone else may be in my account".
        // A reset that leaves their refresh cookie working does not help.
        let (w, user) = world().await;
        let (stolen, plaintext) = RefreshToken::issue(user.id, 30);
        w.refresh.insert(&stolen).await.unwrap();

        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        w.reset
            .execute("alice@example.com", &code, "brand-new-password")
            .await
            .unwrap();

        assert!(w
            .refresh
            .find_by_hash(&RefreshToken::hash(&plaintext))
            .await
            .unwrap()
            .is_none());
    }

    #[tokio::test]
    async fn a_sign_in_code_cannot_be_used_to_reset_a_password() {
        // ADR-063: the purposes are not interchangeable. Without this, a code
        // handed out for a sign-in would also authorise taking the account.
        let (w, user) = world().await;
        let (sign_in, plaintext) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
        w.codes.insert(&sign_in).await.unwrap();

        let err = w
            .reset
            .execute("alice@example.com", &plaintext, "brand-new-password")
            .await
            .unwrap_err();

        assert!(matches!(err, ResetPasswordError::InvalidCode));
        let unchanged = w
            .users
            .find_by_email("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(unchanged.password_hash, "hashed:good-password");
    }

    #[tokio::test]
    async fn requesting_a_reset_leaves_an_outstanding_sign_in_code_alone() {
        // Supersession is per purpose: someone who asks for a reset while a
        // sign-in code is in flight must not find the sign-in code dead.
        let (w, user) = world().await;
        let (sign_in, _) = EmailVerification::issue(user.id, CodePurpose::Verify, 10);
        w.codes.insert(&sign_in).await.unwrap();

        w.request.execute("alice@example.com").await.unwrap();

        let still_live = w
            .codes
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap();
        assert_eq!(still_live.map(|c| c.id), Some(sign_in.id));
    }

    #[tokio::test]
    async fn an_unknown_address_is_a_silent_no_op() {
        let (w, _) = world().await;
        assert!(w
            .request
            .execute("nobody@example.com")
            .await
            .unwrap()
            .is_none());
        assert!(w.mailer.codes_for("nobody@example.com").is_empty());
    }

    #[tokio::test]
    async fn a_short_password_is_refused_without_burning_the_code() {
        let (w, _) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();

        let err = w
            .reset
            .execute("alice@example.com", &code, "short")
            .await
            .unwrap_err();
        assert!(matches!(err, ResetPasswordError::PasswordTooShort));

        // The retry with a decent password still works.
        w.reset
            .execute("alice@example.com", &code, "long-enough-password")
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn guessing_a_reset_code_is_capped() {
        let (w, _) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        let wrong = if code == "000000" { "111111" } else { "000000" };

        for _ in 0..MAX_ATTEMPTS {
            let err = w
                .reset
                .execute("alice@example.com", wrong, "brand-new-password")
                .await
                .unwrap_err();
            assert!(matches!(err, ResetPasswordError::InvalidCode));
        }

        let err = w
            .reset
            .execute("alice@example.com", &code, "brand-new-password")
            .await
            .unwrap_err();
        assert!(matches!(err, ResetPasswordError::TooManyAttempts));
        let unchanged = w
            .users
            .find_by_email("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(unchanged.password_hash, "hashed:good-password");
    }

    #[tokio::test]
    async fn a_superseded_reset_code_says_so() {
        let (w, _) = world().await;
        let first = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        let second = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();

        let err = w
            .reset
            .execute("alice@example.com", &first, "brand-new-password")
            .await
            .unwrap_err();
        assert!(matches!(err, ResetPasswordError::Superseded));

        w.reset
            .execute("alice@example.com", &second, "brand-new-password")
            .await
            .unwrap();
    }
}
