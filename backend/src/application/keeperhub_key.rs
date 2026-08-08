//! Connecting an account to its own KeeperHub key (ADR-076).
//!
//! The key is validated before it is stored: Sever asks KeeperHub which wallet
//! the key executes as, and refuses a key KeeperHub does not recognise. That is
//! not politeness about error messages — the wallet it returns is what the
//! revoke guard (ADR-065) compares against, so a key saved without it could
//! never revoke anything, and the user would only discover that at the end of a
//! scan.

use std::sync::Arc;

use chrono::Utc;
use uuid::Uuid;

use crate::domain::ports::{
    KeeperHubCredentialRepository, KeeperHubDirectory, PortError, SecurityAudit,
};
use crate::domain::{mask, KeeperHubCredential, SecretBox, SecurityEvent, SecurityEventKind};

#[derive(Debug, thiserror::Error)]
pub enum KeeperHubKeyError {
    #[error("the API key is empty")]
    Empty,
    #[error("KeeperHub does not recognise this API key")]
    Rejected,
    #[error("KeeperHub could not be reached to verify the key")]
    DirectoryUnreachable,
    #[error(transparent)]
    Infrastructure(#[from] PortError),
}

/// What the account screen is told about a connected key. Never the key.
#[derive(Debug, Clone, PartialEq)]
pub struct ConnectedKey {
    /// The wallet this key executes as — the only wallet it can revoke for.
    pub wallet_address: Option<String>,
    /// `••••` plus the last four characters.
    pub masked: String,
}

impl From<&KeeperHubCredential> for ConnectedKey {
    fn from(credential: &KeeperHubCredential) -> Self {
        Self {
            wallet_address: credential.wallet_address.clone(),
            masked: credential.masked.clone(),
        }
    }
}

pub struct KeeperHubKeys {
    credentials: Arc<dyn KeeperHubCredentialRepository>,
    directory: Arc<dyn KeeperHubDirectory>,
    audit: Arc<dyn SecurityAudit>,
    secrets: SecretBox,
}

impl KeeperHubKeys {
    pub fn new(
        credentials: Arc<dyn KeeperHubCredentialRepository>,
        directory: Arc<dyn KeeperHubDirectory>,
        audit: Arc<dyn SecurityAudit>,
        secrets: SecretBox,
    ) -> Self {
        Self {
            credentials,
            directory,
            audit,
            secrets,
        }
    }

    /// Validates the key against KeeperHub, then stores it encrypted.
    ///
    /// Returns what the account screen may display. The plaintext key is not
    /// returned, not logged, and not echoed back — once saved, the only way it
    /// leaves this system is towards KeeperHub.
    pub async fn connect(
        &self,
        user_id: Uuid,
        api_key: &str,
    ) -> Result<ConnectedKey, KeeperHubKeyError> {
        let api_key = api_key.trim();
        if api_key.is_empty() {
            return Err(KeeperHubKeyError::Empty);
        }

        let wallet = match self.directory.wallet_for_key(api_key).await {
            Ok(Some(wallet)) => wallet.trim().to_lowercase(),
            Ok(None) => return Err(KeeperHubKeyError::Rejected),
            // A key that cannot be checked is not saved. Storing it would leave
            // an account believing it can revoke when nothing has confirmed the
            // key works, and the failure would surface mid-scan instead of here.
            Err(_) => return Err(KeeperHubKeyError::DirectoryUnreachable),
        };

        let now = Utc::now();
        let existing = self.credentials.find(user_id).await?;
        let credential = KeeperHubCredential {
            user_id,
            api_key_encrypted: self.secrets.seal(api_key),
            wallet_address: Some(wallet),
            masked: mask(api_key),
            // Rotating a key does not restart the connection.
            created_at: existing.as_ref().map_or(now, |c| c.created_at),
            updated_at: now,
        };
        self.credentials.upsert(&credential).await?;

        // Security-relevant: this is the moment an account gains the ability to
        // send transactions. It belongs in the audit trail (ADR-045).
        // The wallet, never the key: the address is public and is what makes
        // the trail useful for triage.
        self.audit
            .record(&SecurityEvent::new(
                SecurityEventKind::KeeperHubKeyConnected,
                Some(user_id),
                None,
                credential.wallet_address.clone().unwrap_or_default(),
            ))
            .await?;

        Ok(ConnectedKey::from(&credential))
    }

    /// What the account screen shows, or `None` when no key is connected.
    pub async fn status(&self, user_id: Uuid) -> Result<Option<ConnectedKey>, PortError> {
        Ok(self
            .credentials
            .find(user_id)
            .await?
            .as_ref()
            .map(ConnectedKey::from))
    }

    /// Forgets the account's key. Returns false when there was none, so the
    /// caller can answer honestly rather than reporting a disconnect that never
    /// happened.
    pub async fn disconnect(&self, user_id: Uuid) -> Result<bool, PortError> {
        let removed = self.credentials.delete(user_id).await?;
        if removed {
            self.audit
                .record(&SecurityEvent::new(
                    SecurityEventKind::KeeperHubKeyDisconnected,
                    Some(user_id),
                    None,
                    "",
                ))
                .await?;
        }
        Ok(removed)
    }

    /// The key to dispatch with, tolerating every failure as "no key".
    ///
    /// The dispatch paths all want the same thing: a scan must still run when
    /// the credential store is unhappy. Losing a revoke is bad; losing the
    /// findings that tell someone they *have* a dangerous approval is worse.
    /// `None` from an unset `keys` (feature disabled) is the same answer.
    pub async fn dispatch_key(keys: Option<&Arc<Self>>, user_id: Uuid) -> Option<String> {
        match keys?.api_key_for(user_id).await {
            Ok(key) => key,
            Err(err) => {
                tracing::error!(
                    %user_id,
                    error = %err,
                    "could not read the KeeperHub key; scanning without revoke"
                );
                None
            }
        }
    }

    /// The plaintext key for a dispatch, or `None` when the account has not
    /// connected one (the worker then falls back to its environment key).
    ///
    /// The one place a stored key is decrypted. Kept narrow deliberately: every
    /// other read path works from `masked` and `wallet_address`.
    pub async fn api_key_for(&self, user_id: Uuid) -> Result<Option<String>, PortError> {
        let Some(credential) = self.credentials.find(user_id).await? else {
            return Ok(None);
        };
        match self.secrets.open(&credential.api_key_encrypted) {
            Ok(key) => Ok(Some(key)),
            // Almost always a rotated CREDENTIAL_ENCRYPTION_KEY. Treated as
            // "no key" rather than an error: the scan should still run and
            // report findings, just without revoking. Logged loudly because it
            // needs an operator, not a user.
            Err(err) => {
                tracing::error!(
                    %user_id,
                    error = %err,
                    "stored KeeperHub key could not be decrypted; scanning without revoke"
                );
                Ok(None)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapters::persistence::in_memory::{
        InMemoryKeeperHubCredentialRepository, InMemorySecurityAudit,
    };
    use async_trait::async_trait;

    const KEY: &str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=";
    const WALLET: &str = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf";
    // Shaped like a KeeperHub key but not one: never put a live key in a test,
    // the masked assertions below would publish its last four characters.
    const API_KEY: &str = "kh_000000000000000000000000000wxyz";

    /// Answers like KeeperHub: a wallet for the key it knows, nothing for
    /// others, or a transport failure when `unreachable`.
    struct FakeDirectory {
        wallet: Option<String>,
        unreachable: bool,
    }

    impl FakeDirectory {
        fn knows(wallet: &str) -> Self {
            Self {
                wallet: Some(wallet.to_string()),
                unreachable: false,
            }
        }
        fn rejects() -> Self {
            Self {
                wallet: None,
                unreachable: false,
            }
        }
        fn down() -> Self {
            Self {
                wallet: None,
                unreachable: true,
            }
        }
    }

    #[async_trait]
    impl KeeperHubDirectory for FakeDirectory {
        async fn wallet_for_key(&self, _api_key: &str) -> Result<Option<String>, PortError> {
            if self.unreachable {
                return Err(PortError("connection refused".into()));
            }
            Ok(self.wallet.clone())
        }
    }

    fn keys(
        directory: FakeDirectory,
    ) -> (KeeperHubKeys, Arc<InMemoryKeeperHubCredentialRepository>) {
        let credentials = Arc::new(InMemoryKeeperHubCredentialRepository::default());
        let keys = KeeperHubKeys::new(
            credentials.clone(),
            Arc::new(directory),
            Arc::new(InMemorySecurityAudit::default()),
            SecretBox::from_base64(KEY).unwrap(),
        );
        (keys, credentials)
    }

    #[tokio::test]
    async fn connecting_stores_the_key_encrypted_and_reports_the_wallet() {
        let (keys, credentials) = keys(FakeDirectory::knows(WALLET));
        let user = Uuid::new_v4();

        let connected = keys.connect(user, API_KEY).await.unwrap();

        assert_eq!(connected.wallet_address.as_deref(), Some(WALLET));
        assert_eq!(connected.masked, "••••wxyz");

        // The row must not hold the key in the clear — this is the property the
        // whole feature rests on.
        let stored = credentials.find(user).await.unwrap().unwrap();
        assert!(!stored.api_key_encrypted.contains(API_KEY));
        assert_eq!(stored.masked, "••••wxyz");
    }

    #[tokio::test]
    async fn the_stored_key_round_trips_for_dispatch() {
        let (keys, _) = keys(FakeDirectory::knows(WALLET));
        let user = Uuid::new_v4();
        keys.connect(user, API_KEY).await.unwrap();

        assert_eq!(
            keys.api_key_for(user).await.unwrap().as_deref(),
            Some(API_KEY)
        );
    }

    #[tokio::test]
    async fn an_account_without_a_key_dispatches_without_one() {
        let (keys, _) = keys(FakeDirectory::knows(WALLET));
        assert_eq!(keys.api_key_for(Uuid::new_v4()).await.unwrap(), None);
        assert_eq!(keys.status(Uuid::new_v4()).await.unwrap(), None);
    }

    #[tokio::test]
    async fn a_key_keeperhub_rejects_is_not_stored() {
        let (keys, credentials) = keys(FakeDirectory::rejects());
        let user = Uuid::new_v4();

        let err = keys.connect(user, "kh_wrong").await.unwrap_err();

        assert!(matches!(err, KeeperHubKeyError::Rejected));
        assert_eq!(credentials.find(user).await.unwrap(), None);
    }

    #[tokio::test]
    async fn a_key_that_cannot_be_verified_is_not_stored() {
        // Saving it would promise a revoke capability nothing has confirmed.
        let (keys, credentials) = keys(FakeDirectory::down());
        let user = Uuid::new_v4();

        let err = keys.connect(user, API_KEY).await.unwrap_err();

        assert!(matches!(err, KeeperHubKeyError::DirectoryUnreachable));
        assert_eq!(credentials.find(user).await.unwrap(), None);
    }

    #[tokio::test]
    async fn an_empty_key_is_refused_without_calling_keeperhub() {
        let (keys, _) = keys(FakeDirectory::rejects());
        let err = keys.connect(Uuid::new_v4(), "   ").await.unwrap_err();
        assert!(matches!(err, KeeperHubKeyError::Empty));
    }

    #[tokio::test]
    async fn the_wallet_is_stored_lowercase() {
        // KeeperHub may answer with an EIP-55 checksummed address; ADR-065's
        // guard and this schema both work in lowercase.
        let (keys, _) = keys(FakeDirectory::knows(
            "0xE13ED979BC6B23D6D9608939051E9488E9F304BF",
        ));
        let user = Uuid::new_v4();

        let connected = keys.connect(user, API_KEY).await.unwrap();

        assert_eq!(connected.wallet_address.as_deref(), Some(WALLET));
    }

    #[tokio::test]
    async fn rotating_a_key_replaces_it_and_keeps_the_connection_date() {
        let (keys, credentials) = keys(FakeDirectory::knows(WALLET));
        let user = Uuid::new_v4();
        keys.connect(user, API_KEY).await.unwrap();
        let first = credentials.find(user).await.unwrap().unwrap();

        keys.connect(user, "kh_rotated_aaaaaaaaaaaaaaaaaaaaBEEF")
            .await
            .unwrap();

        let second = credentials.find(user).await.unwrap().unwrap();
        assert_eq!(second.created_at, first.created_at);
        assert_eq!(second.masked, "••••BEEF");
        assert_eq!(
            keys.api_key_for(user).await.unwrap().as_deref(),
            Some("kh_rotated_aaaaaaaaaaaaaaaaaaaaBEEF")
        );
    }

    #[tokio::test]
    async fn disconnecting_forgets_the_key() {
        let (keys, _) = keys(FakeDirectory::knows(WALLET));
        let user = Uuid::new_v4();
        keys.connect(user, API_KEY).await.unwrap();

        assert!(keys.disconnect(user).await.unwrap());

        assert_eq!(keys.status(user).await.unwrap(), None);
        assert_eq!(keys.api_key_for(user).await.unwrap(), None);
        // Nothing left to remove the second time.
        assert!(!keys.disconnect(user).await.unwrap());
    }

    #[tokio::test]
    async fn one_accounts_key_is_never_served_to_another() {
        let (keys, _) = keys(FakeDirectory::knows(WALLET));
        let owner = Uuid::new_v4();
        keys.connect(owner, API_KEY).await.unwrap();

        assert_eq!(keys.api_key_for(Uuid::new_v4()).await.unwrap(), None);
        assert_eq!(keys.status(Uuid::new_v4()).await.unwrap(), None);
    }

    #[tokio::test]
    async fn a_key_sealed_with_a_different_encryption_key_does_not_block_the_scan() {
        // CREDENTIAL_ENCRYPTION_KEY rotated without re-encrypting the rows. The
        // scan must still run; it just cannot revoke.
        let credentials = Arc::new(InMemoryKeeperHubCredentialRepository::default());
        let user = Uuid::new_v4();
        credentials
            .upsert(&KeeperHubCredential {
                user_id: user,
                api_key_encrypted: SecretBox::from_base64(
                    "ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA=",
                )
                .unwrap()
                .seal(API_KEY),
                wallet_address: Some(WALLET.into()),
                masked: "••••wxyz".into(),
                created_at: Utc::now(),
                updated_at: Utc::now(),
            })
            .await
            .unwrap();
        let keys = KeeperHubKeys::new(
            credentials,
            Arc::new(FakeDirectory::knows(WALLET)),
            Arc::new(InMemorySecurityAudit::default()),
            SecretBox::from_base64(KEY).unwrap(),
        );

        assert_eq!(keys.api_key_for(user).await.unwrap(), None);
    }
}
