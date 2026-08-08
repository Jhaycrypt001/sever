//! The per-user KeeperHub key routes (ADR-076).
//!
//! What matters here is not that the happy path returns 200 — the use-case
//! tests cover the logic — but that the HTTP edge never leaks a key and never
//! lets one account reach another's.

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use backend::adapters::auth::{Argon2PasswordHasher, JwtTokenService};
use backend::adapters::dispatch::NoopJobDispatcher;
use backend::adapters::email::DevEmailSender;
use backend::adapters::http::rate_limit::Limiter;
use backend::adapters::http::{
    router_with_limits, AppState, EmailVerificationSetup, KeeperHubSetup, RateLimitConfig,
};
use backend::adapters::persistence::in_memory::{
    InMemoryEmailVerificationRepository, InMemoryJobRepository,
    InMemoryKeeperHubCredentialRepository, InMemoryRecurringSearchRepository,
    InMemoryRefreshTokenRepository, InMemorySecurityAudit, InMemoryUserRepository,
};
use backend::domain::ports::{KeeperHubDirectory, PortError};
use backend::domain::SecretBox;
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tower::ServiceExt;

const INTERNAL_TOKEN: &str = "test-internal-token";
const WALLET: &str = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf";
// Shaped like a KeeperHub key but not one: never put a live key in a test,
// the masked assertions below would publish its last four characters.
const API_KEY: &str = "kh_000000000000000000000000000wxyz";
/// 32 bytes, base64. Test-only.
const ENCRYPTION_KEY: &str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=";

struct FakeDirectory {
    accepts: bool,
}

#[async_trait]
impl KeeperHubDirectory for FakeDirectory {
    async fn wallet_for_key(&self, _api_key: &str) -> Result<Option<String>, PortError> {
        Ok(self.accepts.then(|| WALLET.to_string()))
    }
}

/// The app with per-user keys enabled, plus the credential store so a test can
/// look at what was actually written.
fn app_with_keys(accepts: bool) -> (Router, Arc<InMemoryKeeperHubCredentialRepository>) {
    let credentials = Arc::new(InMemoryKeeperHubCredentialRepository::default());
    let state = base_state().with_keeperhub_keys(KeeperHubSetup {
        credentials: credentials.clone(),
        directory: Arc::new(FakeDirectory { accepts }),
        secrets: SecretBox::from_base64(ENCRYPTION_KEY).unwrap(),
    });
    (
        router_with_limits(state, RateLimitConfig::default()),
        credentials,
    )
}

/// The app as a deployment without `CREDENTIAL_ENCRYPTION_KEY` runs it.
fn app_without_keys() -> Router {
    router_with_limits(base_state(), RateLimitConfig::default())
}

fn base_state() -> AppState {
    AppState::new(
        Arc::new(InMemoryUserRepository::default()),
        Arc::new(InMemoryJobRepository::default()),
        Arc::new(InMemoryRefreshTokenRepository::default()),
        Arc::new(InMemoryRecurringSearchRepository::default()),
        Arc::new(NoopJobDispatcher),
        Arc::new(backend::adapters::digest::NoopDigestSender),
        Arc::new(Argon2PasswordHasher),
        Arc::new(JwtTokenService::new("test-secret", 15)),
        Arc::new(InMemorySecurityAudit::default()),
        EmailVerificationSetup {
            verifications: Arc::new(InMemoryEmailVerificationRepository::default()),
            mailer: Arc::new(DevEmailSender::default()),
            ttl_minutes: 10,
            expose_codes: true,
            throttle: Limiter::per_minute(100, "verify", None),
        },
        Limiter::per_minute(100, "login", None),
        INTERNAL_TOKEN.into(),
        100,
        30,
    )
}

async fn send(app: &Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap()
    };
    (status, body)
}

fn json_request(method: &str, uri: &str, body: Value, token: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json");
    if let Some(token) = token {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    builder.body(Body::from(body.to_string())).unwrap()
}

fn plain_request(method: &str, uri: &str, token: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(token) = token {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    builder.body(Body::empty()).unwrap()
}

/// Registers an account, answers its code, returns the access token.
async fn register_verified(app: &Router, email: &str) -> String {
    let (status, body) = send(
        app,
        json_request(
            "POST",
            "/api/auth/register",
            json!({"email": email, "password": "correct horse battery staple"}),
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED, "register: {body}");
    let code = body["verification_code"].as_str().unwrap().to_string();

    let (status, body) = send(
        app,
        json_request(
            "POST",
            "/api/auth/verify",
            json!({"email": email, "code": code}),
            None,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "verify: {body}");
    body["access_token"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn connecting_a_key_reports_the_wallet_and_never_echoes_the_key() {
    let (app, credentials) = app_with_keys(true);
    let token = register_verified(&app, "owner@example.com").await;

    let (status, body) = send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            Some(&token),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["wallet_address"], json!(WALLET));
    assert_eq!(body["masked"], json!("••••wxyz"));
    // The response must not carry the key anywhere, under any field name.
    assert!(
        !body.to_string().contains(API_KEY),
        "the API key leaked into the response: {body}"
    );

    // And it reached storage encrypted.
    let stored = credentials.find_any().unwrap();
    assert!(!stored.api_key_encrypted.contains(API_KEY));
}

#[tokio::test]
async fn reading_the_key_back_never_returns_it() {
    let (app, _) = app_with_keys(true);
    let token = register_verified(&app, "owner@example.com").await;
    send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            Some(&token),
        ),
    )
    .await;

    let (status, body) = send(
        &app,
        plain_request("GET", "/api/settings/keeperhub-key", Some(&token)),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["masked"], json!("••••wxyz"));
    assert!(!body.to_string().contains(API_KEY));
}

#[tokio::test]
async fn an_account_without_a_key_reads_as_null() {
    let (app, _) = app_with_keys(true);
    let token = register_verified(&app, "owner@example.com").await;

    let (status, body) = send(
        &app,
        plain_request("GET", "/api/settings/keeperhub-key", Some(&token)),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, Value::Null);
}

#[tokio::test]
async fn one_account_cannot_see_another_accounts_key() {
    // The property that makes storing other people's credentials defensible.
    let (app, _) = app_with_keys(true);
    let owner = register_verified(&app, "owner@example.com").await;
    send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            Some(&owner),
        ),
    )
    .await;

    let stranger = register_verified(&app, "stranger@example.com").await;
    let (status, body) = send(
        &app,
        plain_request("GET", "/api/settings/keeperhub-key", Some(&stranger)),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, Value::Null, "another account's key was visible");
}

#[tokio::test]
async fn the_routes_require_a_session() {
    let (app, _) = app_with_keys(true);

    for request in [
        plain_request("GET", "/api/settings/keeperhub-key", None),
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            None,
        ),
        plain_request("DELETE", "/api/settings/keeperhub-key", None),
    ] {
        let (status, _) = send(&app, request).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
}

#[tokio::test]
async fn a_key_keeperhub_rejects_is_refused() {
    let (app, credentials) = app_with_keys(false);
    let token = register_verified(&app, "owner@example.com").await;

    let (status, body) = send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": "kh_wrong"}),
            Some(&token),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
    assert!(credentials.find_any().is_none());
}

#[tokio::test]
async fn disconnecting_removes_the_key_and_is_idempotent() {
    let (app, _) = app_with_keys(true);
    let token = register_verified(&app, "owner@example.com").await;
    send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            Some(&token),
        ),
    )
    .await;

    let (status, _) = send(
        &app,
        plain_request("DELETE", "/api/settings/keeperhub-key", Some(&token)),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    let (status, body) = send(
        &app,
        plain_request("GET", "/api/settings/keeperhub-key", Some(&token)),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, Value::Null);

    // Asking again is still fine: the account ends up with no key either way.
    let (status, _) = send(
        &app,
        plain_request("DELETE", "/api/settings/keeperhub-key", Some(&token)),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
}

#[tokio::test]
async fn a_deployment_without_an_encryption_key_says_so_instead_of_storing_one() {
    // The safety valve: no CREDENTIAL_ENCRYPTION_KEY must mean no key storage
    // at all, not key storage in the clear.
    let app = app_without_keys();
    let token = register_verified(&app, "owner@example.com").await;

    let (status, _) = send(
        &app,
        json_request(
            "PUT",
            "/api/settings/keeperhub-key",
            json!({"api_key": API_KEY}),
            Some(&token),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_IMPLEMENTED);
}
