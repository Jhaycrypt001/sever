//! Proving an address can receive mail (ADR-062), and the second factor of
//! every sign-in (ADR-063).
//!
//! One mechanism does both jobs, because they are the same question asked at
//! different moments: *can the person in front of us read that inbox?* A code
//! is issued in exactly two situations — registration (where the person just
//! chose the password) and a sign-in whose password was already accepted — so
//! holding a code always implies one of those. Answering it is therefore a
//! full sign-in, and `ConfirmEmailVerification` is the only path in the
//! codebase to a `SessionIssuer`.
//!
//! The one endpoint that issues a code *without* a password is `resend`, and
//! it is restricted to accounts that have never been verified. That restriction
//! is load-bearing: without it, someone with access to a mailbox could request
//! a code and sign in without ever knowing the password.

use std::sync::Arc;

use super::session::{SessionIssuer, SessionTokens};
use crate::domain::ports::{EmailSender, EmailVerificationRepository, PortError, UserRepository};
use crate::domain::{CodePurpose, EmailVerification, User};

#[derive(Debug, thiserror::Error)]
pub enum RequestVerificationError {
    /// The code was minted but the provider would not take it. Surfaced rather
    /// than swallowed: parking someone in front of a code box that can never
    /// be satisfied is worse than telling them the mailer is down.
    #[error("could not send the verification email: {0}")]
    Delivery(PortError),
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

pub struct RequestEmailVerification {
    users: Arc<dyn UserRepository>,
    verifications: Arc<dyn EmailVerificationRepository>,
    mailer: Arc<dyn EmailSender>,
    ttl_minutes: i64,
}

impl RequestEmailVerification {
    pub fn new(
        users: Arc<dyn UserRepository>,
        verifications: Arc<dyn EmailVerificationRepository>,
        mailer: Arc<dyn EmailSender>,
        ttl_minutes: i64,
    ) -> Self {
        Self {
            users,
            verifications,
            mailer,
            ttl_minutes,
        }
    }

    /// Issues and sends a code to an account the caller has **already
    /// authenticated** — registration, or a sign-in whose password was
    /// accepted. Unconditional: a verified account gets one too, because that
    /// is the second factor of its sign-in (ADR-063).
    pub async fn issue_for(&self, user: &User) -> Result<String, RequestVerificationError> {
        let (record, code) =
            EmailVerification::issue(user.id, CodePurpose::Verify, self.ttl_minutes);
        // Persist before sending. The other order can deliver a code that was
        // never stored, which is unanswerable.
        self.verifications.insert(&record).await?;
        self.mailer
            .send_code(&user.email, &code, self.ttl_minutes, CodePurpose::Verify)
            .await
            .map_err(RequestVerificationError::Delivery)?;
        Ok(code)
    }

    /// The unauthenticated "send it again" path.
    ///
    /// Returns the plaintext when one was sent, and `None` when there was
    /// nothing to do — the address is not registered, or is **already
    /// verified**. The caller must answer the client identically in all three
    /// cases, or this endpoint becomes a way to ask "does this person have an
    /// account?".
    ///
    /// Refusing already-verified accounts is the security boundary, not a
    /// nicety: this is the one code-issuing path with no password behind it,
    /// so letting it serve a verified account would make mailbox access alone
    /// sufficient to sign in.
    pub async fn execute(&self, email: &str) -> Result<Option<String>, RequestVerificationError> {
        let email = email.trim().to_lowercase();
        let Some(user) = self.users.find_by_email(&email).await? else {
            return Ok(None);
        };
        if user.is_verified() {
            return Ok(None);
        }
        self.issue_for(&user).await.map(Some)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfirmVerificationError {
    /// Wrong code, no outstanding code, or an address that was never
    /// registered. One error for all three: each distinction would tell an
    /// attacker something about an account they cannot open.
    #[error("invalid or expired verification code")]
    InvalidCode,
    /// A code that really was issued to this account but has since been
    /// replaced (ADR-063). Worth its own answer: "invalid" sends people
    /// hunting for a typo that is not there, when the fix is to read the
    /// newest email.
    #[error("that code has been replaced by a newer one — check your most recent email")]
    Superseded,
    #[error("that code has expired, request a new one")]
    Expired,
    #[error("too many incorrect attempts, request a new code")]
    TooManyAttempts,
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

pub struct ConfirmEmailVerification {
    users: Arc<dyn UserRepository>,
    verifications: Arc<dyn EmailVerificationRepository>,
    sessions: Arc<SessionIssuer>,
}

impl ConfirmEmailVerification {
    pub fn new(
        users: Arc<dyn UserRepository>,
        verifications: Arc<dyn EmailVerificationRepository>,
        sessions: Arc<SessionIssuer>,
    ) -> Self {
        Self {
            users,
            verifications,
            sessions,
        }
    }

    /// Accepts a code and signs the account in.
    pub async fn execute(
        &self,
        email: &str,
        code: &str,
    ) -> Result<SessionTokens, ConfirmVerificationError> {
        let email = email.trim().to_lowercase();
        let user = self
            .users
            .find_by_email(&email)
            .await?
            .ok_or(ConfirmVerificationError::InvalidCode)?;

        let record = match self
            .verifications
            .active_for_user(user.id, CodePurpose::Verify)
            .await?
        {
            Some(record) => record,
            // No live code at all. If what they typed was a real code once,
            // say so rather than implying they mistyped.
            None => return Err(self.stale_or_invalid(&user, code).await),
        };

        let now = crate::domain::now_utc();
        if record.is_exhausted() {
            return Err(ConfirmVerificationError::TooManyAttempts);
        }
        if record.is_expired(now) {
            return Err(ConfirmVerificationError::Expired);
        }
        if !record.matches(code) {
            // A superseded code is a stale email, not a guess, so it must not
            // spend an attempt on the code that replaced it — otherwise five
            // stale-email submissions lock someone out of their own account.
            let stale = self.stale_or_invalid(&user, code).await;
            if matches!(stale, ConfirmVerificationError::InvalidCode) {
                // Counted before the answer goes out, so a client that gives
                // up on the response cannot get a free guess.
                self.verifications.record_attempt(record.id).await?;
            }
            return Err(stale);
        }

        // Burn the code first: if issuing the session fails, a retry must not
        // find the code still live.
        self.verifications.mark_consumed(record.id, now).await?;
        self.users.mark_email_verified(user.id, now).await?;
        Ok(self.sessions.issue(user.id).await?)
    }

    /// `Superseded` when the presented code was genuinely issued to this
    /// account at some point, `InvalidCode` when it was never a code here.
    async fn stale_or_invalid(&self, user: &User, code: &str) -> ConfirmVerificationError {
        let hash = EmailVerification::hash(code);
        match self
            .verifications
            .find_by_hash(user.id, CodePurpose::Verify, &hash)
            .await
        {
            Ok(Some(_)) => ConfirmVerificationError::Superseded,
            Ok(None) => ConfirmVerificationError::InvalidCode,
            Err(e) => ConfirmVerificationError::Infrastructure(e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::email::DevEmailSender;
    use crate::adapters::persistence::in_memory::{
        InMemoryEmailVerificationRepository, InMemoryRefreshTokenRepository, InMemoryUserRepository,
    };
    use crate::application::login_user::tests::{verified, FakeTokens};
    use crate::domain::email_verification::MAX_ATTEMPTS;
    use crate::domain::ports::EmailSender;
    use chrono::{Duration, Utc};

    struct World {
        request: RequestEmailVerification,
        confirm: ConfirmEmailVerification,
        users: Arc<InMemoryUserRepository>,
        verifications: Arc<InMemoryEmailVerificationRepository>,
        mailer: Arc<DevEmailSender>,
    }

    async fn world_with(user: User) -> (World, User) {
        let users = Arc::new(InMemoryUserRepository::default());
        let verifications = Arc::new(InMemoryEmailVerificationRepository::default());
        let mailer = Arc::new(DevEmailSender::default());
        let sessions = Arc::new(SessionIssuer::new(
            Arc::new(FakeTokens),
            Arc::new(InMemoryRefreshTokenRepository::default()),
            30,
        ));
        users.insert(&user).await.unwrap();
        (
            World {
                request: RequestEmailVerification::new(
                    users.clone(),
                    verifications.clone(),
                    mailer.clone(),
                    10,
                ),
                confirm: ConfirmEmailVerification::new(
                    users.clone(),
                    verifications.clone(),
                    sessions,
                ),
                users,
                verifications,
                mailer,
            },
            user,
        )
    }

    /// An account mid-registration: exists, never verified.
    async fn world() -> (World, User) {
        world_with(User::new("alice@example.com".into(), "hash".into())).await
    }

    #[tokio::test]
    async fn requesting_mails_a_code_and_stores_only_its_hash() {
        let (w, user) = world().await;

        let code = w
            .request
            .execute("Alice@Example.com")
            .await
            .unwrap()
            .unwrap();

        assert_eq!(w.mailer.codes_for("alice@example.com"), vec![code.clone()]);
        let stored = w
            .verifications
            .active_for_user(user.id, CodePurpose::Verify)
            .await
            .unwrap()
            .unwrap();
        assert_ne!(stored.code_hash, code, "plaintext must not be stored");
        assert!(stored.matches(&code));
    }

    #[tokio::test]
    async fn the_right_code_verifies_the_account_and_signs_it_in() {
        let (w, user) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();

        let tokens = w.confirm.execute("alice@example.com", &code).await.unwrap();

        assert_eq!(tokens.access_token, format!("token-for:{}", user.id));
        let reloaded = w
            .users
            .find_by_email("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        assert!(reloaded.is_verified());
    }

    #[tokio::test]
    async fn a_verified_account_still_gets_a_code_for_its_next_sign_in() {
        // ADR-063: the code is the second factor of *every* sign-in, so
        // `issue_for` must not skip an account just because it is verified.
        let (w, user) = world_with(verified("bob@example.com")).await;

        let code = w.request.issue_for(&user).await.unwrap();
        let tokens = w.confirm.execute("bob@example.com", &code).await.unwrap();

        assert_eq!(tokens.access_token, format!("token-for:{}", user.id));
    }

    #[tokio::test]
    async fn a_code_cannot_be_used_twice() {
        // Otherwise a code sitting in an inbox stays a working key to the
        // account for the rest of its TTL.
        let (w, _) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        w.confirm.execute("alice@example.com", &code).await.unwrap();

        let err = w
            .confirm
            .execute("alice@example.com", &code)
            .await
            .unwrap_err();
        // It was a real code here once, so "replaced" is the honest answer.
        assert!(matches!(err, ConfirmVerificationError::Superseded));
    }

    #[tokio::test]
    async fn a_superseded_code_says_so_and_does_not_spend_an_attempt() {
        // ADR-063. Someone with two emails open will reach for the older one;
        // that is a stale email, not a guess, and five of them must not lock
        // the account out.
        let (w, user) = world().await;
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
        assert_ne!(first, second);

        for _ in 0..MAX_ATTEMPTS + 2 {
            let err = w
                .confirm
                .execute("alice@example.com", &first)
                .await
                .unwrap_err();
            assert!(matches!(err, ConfirmVerificationError::Superseded));
        }

        assert_eq!(
            w.verifications
                .active_for_user(user.id, CodePurpose::Verify)
                .await
                .unwrap()
                .unwrap()
                .attempts,
            0,
            "a stale email must not count against the live code"
        );
        // And the newest code still works.
        w.confirm
            .execute("alice@example.com", &second)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn guessing_is_capped_and_burns_the_code() {
        let (w, user) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        let wrong = if code == "000000" { "111111" } else { "000000" };

        for _ in 0..MAX_ATTEMPTS {
            let err = w
                .confirm
                .execute("alice@example.com", wrong)
                .await
                .unwrap_err();
            assert!(matches!(err, ConfirmVerificationError::InvalidCode));
        }

        // The cap is real: even the correct code is now refused.
        let err = w
            .confirm
            .execute("alice@example.com", &code)
            .await
            .unwrap_err();
        assert!(matches!(err, ConfirmVerificationError::TooManyAttempts));
        assert!(!w
            .users
            .find_by_email("alice@example.com")
            .await
            .unwrap()
            .unwrap()
            .is_verified());
        assert_eq!(
            w.verifications
                .active_for_user(user.id, CodePurpose::Verify)
                .await
                .unwrap()
                .unwrap()
                .attempts,
            MAX_ATTEMPTS
        );
    }

    #[tokio::test]
    async fn an_expired_code_is_refused_and_says_so() {
        let (w, user) = world().await;
        let code = w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .unwrap();
        w.verifications
            .expire_for_test(
                user.id,
                CodePurpose::Verify,
                Utc::now() - Duration::minutes(1),
            )
            .await;

        let err = w
            .confirm
            .execute("alice@example.com", &code)
            .await
            .unwrap_err();
        // Distinct from InvalidCode: "request a new one" is actionable, and it
        // reveals nothing to someone who did not already hold a code.
        assert!(matches!(err, ConfirmVerificationError::Expired));
    }

    #[tokio::test]
    async fn resend_refuses_unknown_and_already_verified_addresses_alike() {
        // No enumeration, and — for the verified case — no way to sign in with
        // mailbox access alone, since this is the only passwordless issuer.
        let (w, user) = world().await;

        assert!(w
            .request
            .execute("nobody@example.com")
            .await
            .unwrap()
            .is_none());
        assert!(w.mailer.codes_for("nobody@example.com").is_empty());

        w.users
            .mark_email_verified(user.id, Utc::now())
            .await
            .unwrap();
        assert!(w
            .request
            .execute("alice@example.com")
            .await
            .unwrap()
            .is_none());
        assert!(w.mailer.codes_for("alice@example.com").is_empty());
    }

    #[tokio::test]
    async fn confirming_an_unknown_address_looks_exactly_like_a_wrong_code() {
        let (w, _) = world().await;
        let err = w
            .confirm
            .execute("nobody@example.com", "123456")
            .await
            .unwrap_err();
        assert!(matches!(err, ConfirmVerificationError::InvalidCode));
    }

    #[tokio::test]
    async fn a_delivery_failure_is_reported_not_swallowed() {
        struct BrokenMailer;
        #[async_trait::async_trait]
        impl EmailSender for BrokenMailer {
            async fn send_code(
                &self,
                _to: &str,
                _code: &str,
                _ttl: i64,
                _purpose: CodePurpose,
            ) -> Result<(), PortError> {
                Err(PortError("provider down".into()))
            }
        }

        let users = Arc::new(InMemoryUserRepository::default());
        let user = User::new("alice@example.com".into(), "hash".into());
        users.insert(&user).await.unwrap();
        let request = RequestEmailVerification::new(
            users,
            Arc::new(InMemoryEmailVerificationRepository::default()),
            Arc::new(BrokenMailer),
            10,
        );

        let err = request.execute("alice@example.com").await.unwrap_err();
        assert!(matches!(err, RequestVerificationError::Delivery(_)));
    }
}
