//! Issuing a signed-in session (ADR-008).
//!
//! Two use cases end with the same three lines — password login, and answering
//! an email verification code (ADR-062). Both mint an access token, open a new
//! refresh-token family, and persist its hash. Sharing the step keeps them from
//! drifting: a change to token lifetimes or to what a login records must not
//! apply to one door into the account and not the other.

use std::sync::Arc;

use crate::domain::ports::{PortError, RefreshTokenRepository, TokenService};
use crate::domain::RefreshToken;
use uuid::Uuid;

/// What a successful authentication hands back (ADR-008): a short-lived JWT
/// for the Authorization header and a single-use refresh token for the cookie.
#[derive(Debug)]
pub struct SessionTokens {
    pub access_token: String,
    pub refresh_token: String,
}

pub struct SessionIssuer {
    tokens: Arc<dyn TokenService>,
    refresh_tokens: Arc<dyn RefreshTokenRepository>,
    refresh_ttl_days: i64,
}

impl SessionIssuer {
    pub fn new(
        tokens: Arc<dyn TokenService>,
        refresh_tokens: Arc<dyn RefreshTokenRepository>,
        refresh_ttl_days: i64,
    ) -> Self {
        Self {
            tokens,
            refresh_tokens,
            refresh_ttl_days,
        }
    }

    /// Opens a fresh rotation family for `user_id` (ADR-056) and returns both
    /// tokens. The caller has already decided the user is who they say.
    pub async fn issue(&self, user_id: Uuid) -> Result<SessionTokens, PortError> {
        let (record, plaintext) = RefreshToken::issue(user_id, self.refresh_ttl_days);
        self.refresh_tokens.insert(&record).await?;
        Ok(SessionTokens {
            access_token: self.tokens.issue(user_id)?,
            refresh_token: plaintext,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::InMemoryRefreshTokenRepository;
    use crate::application::login_user::tests::FakeTokens;
    use crate::domain::ports::RefreshTokenRepository;

    #[tokio::test]
    async fn issuing_persists_the_refresh_hash_and_never_the_plaintext() {
        let refresh = Arc::new(InMemoryRefreshTokenRepository::default());
        let issuer = SessionIssuer::new(Arc::new(FakeTokens), refresh.clone(), 30);
        let user_id = Uuid::new_v4();

        let tokens = issuer.issue(user_id).await.unwrap();

        assert_eq!(tokens.access_token, format!("token-for:{user_id}"));
        let stored = refresh
            .find_by_hash(&RefreshToken::hash(&tokens.refresh_token))
            .await
            .unwrap()
            .expect("the refresh token must be persisted hashed");
        assert_eq!(stored.user_id, user_id);
    }

    #[tokio::test]
    async fn each_session_opens_its_own_family() {
        // ADR-056: revoking one stolen lineage must not sign out other devices.
        let refresh = Arc::new(InMemoryRefreshTokenRepository::default());
        let issuer = SessionIssuer::new(Arc::new(FakeTokens), refresh.clone(), 30);
        let user_id = Uuid::new_v4();

        let first = issuer.issue(user_id).await.unwrap();
        let second = issuer.issue(user_id).await.unwrap();

        let family = |t: &SessionTokens| {
            let hash = RefreshToken::hash(&t.refresh_token);
            let refresh = refresh.clone();
            async move {
                refresh
                    .find_by_hash(&hash)
                    .await
                    .unwrap()
                    .unwrap()
                    .family_id
            }
        };
        assert_ne!(family(&first).await, family(&second).await);
    }
}
