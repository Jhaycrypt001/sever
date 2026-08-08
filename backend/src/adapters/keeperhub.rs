//! KeeperHub directory adapter (ADR-076): resolves an API key to the wallet it
//! executes as, via `GET /api/user`.
//!
//! This is the only call the Rust side makes to KeeperHub. Executing
//! transactions stays entirely in the agent worker (ADR-006) — what happens
//! here is identity, not action: "whose key is this?", asked once when a user
//! connects one.

use async_trait::async_trait;

use crate::domain::ports::{KeeperHubDirectory, PortError};

/// How long to wait for KeeperHub when validating a key. Short: a user is
/// sitting in front of the settings panel waiting for an answer, and a slow
/// "try again" beats a spinner that never resolves.
const TIMEOUT_SECONDS: u64 = 10;

pub struct HttpKeeperHubDirectory {
    client: reqwest::Client,
    base_url: String,
}

impl HttpKeeperHubDirectory {
    pub fn new(base_url: String) -> Self {
        Self {
            client: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(TIMEOUT_SECONDS))
                .build()
                .unwrap_or_default(),
            base_url: base_url.trim_end_matches('/').to_string(),
        }
    }
}

#[async_trait]
impl KeeperHubDirectory for HttpKeeperHubDirectory {
    async fn wallet_for_key(&self, api_key: &str) -> Result<Option<String>, PortError> {
        let response = self
            .client
            .get(format!("{}/api/user", self.base_url))
            // `Bearer `, matching the worker's revoker. Without the scheme
            // KeeperHub answers 401 and a perfectly good key is reported back
            // to the user as unrecognised.
            .header("Authorization", format!("Bearer {api_key}"))
            .send()
            .await
            // Deliberately does not include the source error: it can carry the
            // request headers, and the API key is one of them.
            .map_err(|_| PortError("KeeperHub is unreachable".into()))?;

        // A rejected key is an answer, not a failure: 401/403 means "this key
        // is not valid", which the user can fix. Anything else 4xx/5xx is
        // KeeperHub having a problem, which they cannot.
        if response.status() == reqwest::StatusCode::UNAUTHORIZED
            || response.status() == reqwest::StatusCode::FORBIDDEN
        {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(PortError(format!(
                "KeeperHub returned {}",
                response.status()
            )));
        }

        let body: serde_json::Value = response
            .json()
            .await
            .map_err(|e| PortError(format!("KeeperHub sent an unreadable response: {e}")))?;

        // A 200 with no wallet is treated as a rejection rather than an error:
        // whatever the key is, it does not execute as anything, so it can never
        // satisfy the revoke guard (ADR-065).
        Ok(body
            .get("walletAddress")
            .and_then(|w| w.as_str())
            .filter(|w| !w.trim().is_empty())
            .map(|w| w.trim().to_lowercase()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, StatusCode};
    use axum::routing::get;
    use axum::{Json, Router};
    use std::sync::{Arc, Mutex};

    const WALLET: &str = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf";

    /// Spawns a stub KeeperHub returning `body` with `status`, capturing the
    /// Authorization header it was called with.
    async fn spawn_stub(
        status: StatusCode,
        body: serde_json::Value,
    ) -> (String, Arc<Mutex<Vec<String>>>) {
        let seen: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(vec![]));
        let captured = seen.clone();
        let app = Router::new().route(
            "/api/user",
            get(move |headers: HeaderMap| {
                let captured = captured.clone();
                let body = body.clone();
                async move {
                    captured.lock().unwrap().push(
                        headers
                            .get("authorization")
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or_default()
                            .to_string(),
                    );
                    (status, Json(body))
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (format!("http://{addr}"), seen)
    }

    #[tokio::test]
    async fn a_valid_key_resolves_to_its_wallet() {
        let (url, seen) = spawn_stub(
            StatusCode::OK,
            serde_json::json!({ "walletAddress": WALLET }),
        )
        .await;

        let wallet = HttpKeeperHubDirectory::new(url)
            .wallet_for_key("kh_test")
            .await
            .unwrap();

        assert_eq!(wallet.as_deref(), Some(WALLET));
        // The `Bearer ` scheme is the whole point of this assertion: the worker
        // sends it, KeeperHub requires it, and sending the bare key instead
        // returns 401 — which this adapter reports as "not recognised", so a
        // valid key looks invalid to the user. Asserting the exact header is
        // what stops that from coming back.
        assert_eq!(
            seen.lock().unwrap().as_slice(),
            &["Bearer kh_test".to_string()]
        );
    }

    #[tokio::test]
    async fn a_checksummed_wallet_comes_back_lowercase() {
        let (url, _) = spawn_stub(
            StatusCode::OK,
            serde_json::json!({ "walletAddress": "0xE13ED979BC6B23D6D9608939051E9488E9F304BF" }),
        )
        .await;

        let wallet = HttpKeeperHubDirectory::new(url)
            .wallet_for_key("kh_test")
            .await
            .unwrap();

        assert_eq!(wallet.as_deref(), Some(WALLET));
    }

    #[tokio::test]
    async fn a_rejected_key_is_none_rather_than_an_error() {
        for status in [StatusCode::UNAUTHORIZED, StatusCode::FORBIDDEN] {
            let (url, _) = spawn_stub(status, serde_json::json!({ "error": "nope" })).await;

            let result = HttpKeeperHubDirectory::new(url)
                .wallet_for_key("kh_wrong")
                .await;

            assert_eq!(result.unwrap(), None, "{status} should read as rejected");
        }
    }

    #[tokio::test]
    async fn a_broken_keeperhub_is_an_error_not_a_rejection() {
        // The distinction the use case relies on: this must not be reported to
        // the user as "your key is wrong".
        let (url, _) = spawn_stub(
            StatusCode::INTERNAL_SERVER_ERROR,
            serde_json::json!({ "error": "boom" }),
        )
        .await;

        assert!(HttpKeeperHubDirectory::new(url)
            .wallet_for_key("kh_test")
            .await
            .is_err());
    }

    #[tokio::test]
    async fn a_success_without_a_wallet_reads_as_rejected() {
        for body in [
            serde_json::json!({}),
            serde_json::json!({ "walletAddress": "" }),
            serde_json::json!({ "walletAddress": null }),
        ] {
            let (url, _) = spawn_stub(StatusCode::OK, body).await;

            let wallet = HttpKeeperHubDirectory::new(url)
                .wallet_for_key("kh_test")
                .await
                .unwrap();

            assert_eq!(wallet, None);
        }
    }

    #[tokio::test]
    async fn an_unreachable_keeperhub_is_an_error() {
        // Port 1 with nothing on it: connection refused.
        assert!(HttpKeeperHubDirectory::new("http://127.0.0.1:1".into())
            .wallet_for_key("kh_test")
            .await
            .is_err());
    }
}
