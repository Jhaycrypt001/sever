//! End-to-end test of the HTTP API: register -> login -> launch a search ->
//! agent callback with results -> read results sorted by publication date.

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use backend::adapters::auth::{Argon2PasswordHasher, JwtTokenService};
use backend::adapters::dispatch::NoopJobDispatcher;
use backend::adapters::email::DevEmailSender;
use backend::adapters::http::rate_limit::Limiter;
use backend::adapters::http::{
    router_with_limits, AppState, EmailVerificationSetup, RateLimitConfig,
};
use backend::adapters::persistence::in_memory::{
    InMemoryEmailVerificationRepository, InMemoryJobRepository, InMemoryRecurringSearchRepository,
    InMemoryRefreshTokenRepository, InMemorySecurityAudit, InMemoryUserRepository,
};
use backend::domain::ports::SecurityAudit;
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tower::ServiceExt;

const INTERNAL_TOKEN: &str = "test-internal-token";
const ADDR: &str = "0x1234567890123456789012345678901234567890";

fn app() -> Router {
    app_with(RateLimitConfig::default(), 100)
}

fn app_with(limits: RateLimitConfig, daily_quota: u32) -> Router {
    app_with_audit(limits, daily_quota).0
}

/// Same wiring as `app_with`, but also hands back the in-memory audit log so a
/// test can assert what was recorded (ADR-057). The per-account login throttle
/// is built from `limits.login_per_minute`.
fn app_with_audit(
    limits: RateLimitConfig,
    daily_quota: u32,
) -> (Router, Arc<InMemorySecurityAudit>) {
    let audit = Arc::new(InMemorySecurityAudit::default());
    let login_throttle = Limiter::per_minute(limits.login_per_minute, "login", None);
    let state = AppState::new(
        Arc::new(InMemoryUserRepository::default()),
        Arc::new(InMemoryJobRepository::default()),
        Arc::new(InMemoryRefreshTokenRepository::default()),
        Arc::new(InMemoryRecurringSearchRepository::default()),
        Arc::new(NoopJobDispatcher),
        Arc::new(backend::adapters::digest::NoopDigestSender),
        Arc::new(Argon2PasswordHasher),
        Arc::new(JwtTokenService::new("test-secret", 15)),
        audit.clone(),
        // ADR-062: the tests run the development mail configuration, so the
        // code comes back in the response instead of needing a mailbox.
        EmailVerificationSetup {
            verifications: Arc::new(InMemoryEmailVerificationRepository::default()),
            mailer: Arc::new(DevEmailSender::default()),
            ttl_minutes: 10,
            expose_codes: true,
            // Its own budget, so a test that throttles login still gets to
            // verify an account first.
            throttle: Limiter::per_minute(limits.login_per_minute, "verify", None),
        },
        login_throttle,
        INTERNAL_TOKEN.into(),
        daily_quota,
        30,
    );
    (router_with_limits(state, limits), audit)
}

/// Answers a code and returns the access token of the session it opened.
async fn answer_code(app: &Router, email: &str, code: &str) -> String {
    let (status, body) = send(
        app,
        post_json(
            "/api/auth/verify",
            json!({"email": email, "code": code}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "verify: {body}");
    body["access_token"].as_str().unwrap().to_string()
}

/// Registers an account and answers its code (ADR-062), leaving it signed in.
/// Returns the access token.
async fn register_verified(app: &Router, email: &str, password: &str) -> String {
    let (status, body) = send(
        app,
        post_json(
            "/api/auth/register",
            json!({"email": email, "password": password}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED, "register: {body}");
    assert_eq!(body["verification_required"], json!(true));
    let code = body["verification_code"]
        .as_str()
        .expect("the development configuration returns the code")
        .to_string();

    answer_code(app, email, &code).await
}

/// The full two-factor sign-in of ADR-063: password, then the emailed code.
async fn sign_in(app: &Router, email: &str, password: &str) -> String {
    let (status, body) = send(
        app,
        post_json(
            "/api/auth/login",
            json!({"email": email, "password": password}),
            &[],
        ),
    )
    .await;
    // 202, not 200: the password alone does not open a session.
    assert_eq!(status, StatusCode::ACCEPTED, "login: {body}");
    let code = body["verification_code"]
        .as_str()
        .expect("the development configuration returns the code")
        .to_string();

    answer_code(app, email, &code).await
}

/// Extracts the `refresh_token` cookie value from a `set-cookie` response header.
fn refresh_cookie_value(response: &axum::response::Response) -> Option<String> {
    let header = response.headers().get("set-cookie")?.to_str().ok()?;
    assert!(
        header.contains("HttpOnly"),
        "cookie must be HttpOnly: {header}"
    );
    assert!(
        header.contains("SameSite=Strict"),
        "cookie must be SameSite=Strict: {header}"
    );
    header
        .split(';')
        .next()?
        .strip_prefix("refresh_token=")
        .map(str::to_string)
        .filter(|v| !v.is_empty())
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

fn post_json(uri: &str, body: Value, extra_headers: &[(&str, &str)]) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json");
    for (name, value) in extra_headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::from(body.to_string())).unwrap()
}

fn get(uri: &str, extra_headers: &[(&str, &str)]) -> Request<Body> {
    let mut builder = Request::builder().method("GET").uri(uri);
    for (name, value) in extra_headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::empty()).unwrap()
}

#[tokio::test]
async fn full_search_lifecycle() {
    let app = app();

    // Register, answer the emailed code (ADR-062), and land signed in
    let token = register_verified(&app, "alice@example.com", "s3cret-password").await;

    // Sign in again: password, then a second code (ADR-063).
    assert!(!sign_in(&app, "alice@example.com", "s3cret-password")
        .await
        .is_empty());
    let auth = format!("Bearer {token}");

    // Launch a search
    let (status, body) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "create search: {body}");
    let job_id = body["job_id"].as_str().unwrap().to_string();

    // Worker picked the job up: status becomes running (ADR-016)
    let (status, _) = send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/started"),
            json!({}),
            &[("x-internal-token", INTERNAL_TOKEN)],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    let (status, body) = send(
        &app,
        get(
            &format!("/api/searches/{job_id}"),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "running");

    // Agent callback: findings arrive mixed-tier, backend must sort most-dangerous-first
    let (status, _) = send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/results"),
            json!({"results": [
                {"chain_id": "1", "token_address": "0xa", "token_symbol": "SAFE", "spender_address": "0xsafe", "approved_amount": "10", "tier": "safe"},
                {"chain_id": "1", "token_address": "0xb", "token_symbol": "WATCH", "spender_address": "0xwatch", "approved_amount": "100", "tier": "watch"},
                {"chain_id": "1", "token_address": "0xc", "token_symbol": "BAD", "spender_address": "0xbad", "approved_amount": "Unlimited", "tier": "dangerous"}
            ]}),
            &[("x-internal-token", INTERNAL_TOKEN)],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    // Read back: completed, findings most-dangerous-first
    let (status, body) = send(
        &app,
        get(
            &format!("/api/searches/{job_id}"),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "get search: {body}");
    assert_eq!(body["status"], "completed");
    let symbols: Vec<&str> = body["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["token_symbol"].as_str().unwrap())
        .collect();
    assert_eq!(symbols, vec!["BAD", "WATCH", "SAFE"]);
}

#[tokio::test]
async fn searches_require_authentication() {
    let app = app();
    let (status, _) = send(
        &app,
        post_json("/api/searches", json!({"wallet_address": ADDR}), &[]),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn refresh_token_rotation_and_logout() {
    let app = app();

    // Register + verify: answering the code is what sets the refresh cookie
    // (ADR-063 — login itself only sends the code).
    let (_, registered) = send(
        &app,
        post_json(
            "/api/auth/register",
            json!({"email": "carol@example.com", "password": "s3cret-password"}),
            &[],
        ),
    )
    .await;
    let code = registered["verification_code"].as_str().unwrap();
    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/verify",
            json!({"email": "carol@example.com", "code": code}),
            &[],
        ))
        .await
        .unwrap();
    let first_refresh = refresh_cookie_value(&response).expect("login must set the refresh cookie");

    // Refresh rotates: new access token + new cookie.
    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/refresh",
            json!({}),
            &[("cookie", &format!("refresh_token={first_refresh}"))],
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let second_refresh = refresh_cookie_value(&response).expect("refresh must rotate the cookie");
    assert_ne!(first_refresh, second_refresh);
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body: Value = serde_json::from_slice(&bytes).unwrap();
    assert!(body["access_token"].as_str().is_some());

    // Replaying the consumed token is rejected (single use).
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/refresh",
            json!({}),
            &[("cookie", &format!("refresh_token={first_refresh}"))],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    // Logout revokes the current token and clears the cookie.
    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/logout",
            json!({}),
            &[("cookie", &format!("refresh_token={second_refresh}"))],
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NO_CONTENT);
    assert!(
        refresh_cookie_value(&response).is_none(),
        "cookie must be cleared"
    );

    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/refresh",
            json!({}),
            &[("cookie", &format!("refresh_token={second_refresh}"))],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn refresh_without_cookie_is_rejected() {
    let app = app();
    let (status, _) = send(&app, post_json("/api/auth/refresh", json!({}), &[])).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn auth_endpoints_are_rate_limited_per_ip() {
    let app = app_with(
        RateLimitConfig {
            auth_per_minute: 2,
            api_per_minute: 100,
            login_per_minute: 1000,
            redis_url: None,
        },
        100,
    );
    let attempt = |ip: &'static str| {
        post_json(
            "/api/auth/login",
            json!({"email": "a@b.c", "password": "wrong-password"}),
            &[("x-forwarded-for", ip)],
        )
    };

    for _ in 0..2 {
        let (status, _) = send(&app, attempt("1.2.3.4")).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED); // wrong creds, but allowed through
    }
    let (status, body) = send(&app, attempt("1.2.3.4")).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS, "{body}");

    // Another client is unaffected.
    let (status, _) = send(&app, attempt("9.9.9.9")).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn search_creation_is_capped_by_the_daily_quota() {
    let app = app_with(RateLimitConfig::default(), 1);

    let auth = format!(
        "Bearer {}",
        register_verified(&app, "bob@example.com", "s3cret-password").await
    );

    let (status, _) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);

    let (status, body) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert!(body["error"].as_str().unwrap().contains("quota"));
}

#[tokio::test]
async fn every_response_carries_a_request_id() {
    let app = app();

    // Generated when absent.
    let response = app.clone().oneshot(get("/healthz", &[])).await.unwrap();
    assert!(response.headers().get("x-request-id").is_some());

    // Echoed when provided (proxy or retrying client sets it).
    let response = app
        .clone()
        .oneshot(get("/healthz", &[("x-request-id", "corr-42")]))
        .await
        .unwrap();
    assert_eq!(response.headers().get("x-request-id").unwrap(), "corr-42");
}

#[tokio::test]
async fn sse_streams_job_updates_until_terminal() {
    use futures_util::StreamExt;

    let app = app();

    // Register + verify + launch a job.
    let auth = format!(
        "Bearer {}",
        register_verified(&app, "sse@example.com", "s3cret-password").await
    );
    let (_, body) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    let job_id = body["job_id"].as_str().unwrap().to_string();

    // Unknown job -> 404, no stream.
    let (status, _) = send(
        &app,
        get(
            &format!("/api/searches/{}/events", uuid::Uuid::new_v4()),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    // Open the stream: the first frame carries the current (pending) state.
    let response = app
        .clone()
        .oneshot(get(
            &format!("/api/searches/{job_id}/events"),
            &[("authorization", auth.as_str())],
        ))
        .await
        .unwrap();
    assert_eq!(
        response.headers().get("content-type").unwrap(),
        "text/event-stream"
    );
    let mut frames = response.into_body().into_data_stream();
    let mut received = String::new();

    let first = tokio::time::timeout(std::time::Duration::from_secs(5), frames.next())
        .await
        .expect("first SSE frame")
        .unwrap()
        .unwrap();
    received.push_str(std::str::from_utf8(&first).unwrap());
    assert!(received.contains("event: update"), "{received}");
    assert!(received.contains(r#""status":"pending""#), "{received}");

    // Worker delivers results: the stream must emit the completed state, then end.
    send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/results"),
            json!({"results": [
                {"chain_id": "1", "token_address": "0xa", "token_symbol": "R", "spender_address": "0xr", "approved_amount": "1", "tier": "safe"}
            ]}),
            &[("x-internal-token", INTERNAL_TOKEN)],
        ),
    )
    .await;

    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(10);
    while !received.contains(r#""status":"completed""#) {
        let frame = tokio::time::timeout_at(deadline, frames.next())
            .await
            .expect("completed update before timeout")
            .expect("stream ended before the completed update")
            .unwrap();
        received.push_str(std::str::from_utf8(&frame).unwrap());
    }

    // Terminal state emitted -> the stream closes.
    let end = tokio::time::timeout(std::time::Duration::from_secs(5), frames.next())
        .await
        .expect("stream should close after the terminal update");
    assert!(end.is_none(), "stream must end after a terminal status");
}

#[tokio::test]
async fn internal_endpoints_require_the_internal_token() {
    let app = app();
    let (status, _) = send(
        &app,
        post_json(
            &format!("/internal/jobs/{}/results", uuid::Uuid::new_v4()),
            json!({"results": []}),
            &[("x-internal-token", "wrong-token")],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

/// Agent mode (ADR-030): mode round-trips, the journal is recorded through the
/// internal endpoint (idempotently) and served in the detail payload.
#[tokio::test]
async fn agent_mode_lifecycle_with_journal() {
    let app = app();
    let auth = format!(
        "Bearer {}",
        register_verified(&app, "agent@example.com", "s3cret-password").await
    );

    let (status, body) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR, "mode": "agent"}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "create agent search: {body}");
    let job_id = body["job_id"].as_str().unwrap().to_string();

    let step = |seq: i32, kind: &str| json!({"seq": seq, "kind": kind, "detail": "1", "reason": "because", "new_hits": 2});
    for payload in [step(1, "scan"), step(1, "scan"), step(2, "finish")] {
        let (status, _) = send(
            &app,
            post_json(
                &format!("/internal/jobs/{job_id}/steps"),
                payload,
                &[("x-internal-token", INTERNAL_TOKEN)],
            ),
        )
        .await;
        assert_eq!(status, StatusCode::NO_CONTENT);
    }

    let (_, detail) = send(
        &app,
        get(
            &format!("/api/searches/{job_id}"),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(detail["mode"], "agent");
    let steps = detail["steps"].as_array().unwrap();
    // The duplicated seq 1 (Celery retry) was ignored: idempotence (ADR-016).
    assert_eq!(steps.len(), 2);
    assert_eq!(steps[0]["kind"], "scan");
    assert_eq!(steps[1]["kind"], "finish");

    // A workflow search keeps the default mode and an empty journal.
    let (_, body) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    let workflow_id = body["job_id"].as_str().unwrap().to_string();
    let (_, detail) = send(
        &app,
        get(
            &format!("/api/searches/{workflow_id}"),
            &[("authorization", auth.as_str())],
        ),
    )
    .await;
    assert_eq!(detail["mode"], "workflow");
    assert_eq!(detail["steps"].as_array().unwrap().len(), 0);
}

/// Human-in-the-loop lifecycle (ADR-032): the agent asks, the job pauses, the
/// user answers, the job is re-dispatched with a fresh journal.
#[tokio::test]
async fn clarification_lifecycle() {
    let app = app();
    let bearer = format!(
        "Bearer {}",
        register_verified(&app, "hitl@test.dev", "s3cret-password").await
    );

    let (_, launched) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR, "mode": "agent"}),
            &[("authorization", &bearer)],
        ),
    )
    .await;
    let job_id = launched["job_id"].as_str().unwrap().to_string();

    // Worker starts, records a step, then asks for clarification.
    let internal = [("x-internal-token", INTERNAL_TOKEN)];
    send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/started"),
            json!({}),
            &internal,
        ),
    )
    .await;
    send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/steps"),
            json!({"seq": 1, "kind": "scan", "detail": "1", "reason": "r", "new_hits": 2}),
            &internal,
        ),
    )
    .await;
    let (status, _) = send(
        &app,
        post_json(
            &format!("/internal/jobs/{job_id}/question"),
            json!({"question": "The animal or the car?"}),
            &internal,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    let (_, detail) = send(
        &app,
        get(
            &format!("/api/searches/{job_id}"),
            &[("authorization", &bearer)],
        ),
    )
    .await;
    assert_eq!(detail["status"], "awaiting_input");
    assert_eq!(detail["question"], "The animal or the car?");

    // Answering an already-answered / non-awaiting job later conflicts; the
    // happy path requeues and clears the journal (replace semantics).
    let (status, _) = send(
        &app,
        post_json(
            &format!("/api/searches/{job_id}/answer"),
            json!({"answer": "the car"}),
            &[("authorization", &bearer)],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    let (_, detail) = send(
        &app,
        get(
            &format!("/api/searches/{job_id}"),
            &[("authorization", &bearer)],
        ),
    )
    .await;
    assert_eq!(detail["status"], "pending");
    assert_eq!(detail["answer"], "the car");
    assert_eq!(detail["question"], "The animal or the car?");
    assert!(detail["steps"].as_array().unwrap().is_empty());

    let (status, _) = send(
        &app,
        post_json(
            &format!("/api/searches/{job_id}/answer"),
            json!({"answer": "again"}),
            &[("authorization", &bearer)],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
}

/// Recurring searches CRUD (ADR-033) over the public API.
#[tokio::test]
async fn recurring_search_crud() {
    let app = app();
    let bearer = format!(
        "Bearer {}",
        register_verified(&app, "recurring@test.dev", "s3cret-password").await
    );
    let auth = [("authorization", bearer.as_str())];

    let (status, created) = send(
        &app,
        post_json(
            "/api/recurring",
            json!({"wallet_address": ADDR, "mode": "agent", "interval_minutes": 60}),
            &auth,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(created["mode"], "agent");
    assert!(created["last_run_at"].is_null());
    let id = created["id"].as_str().unwrap().to_string();

    let (_, listed) = send(&app, get("/api/recurring", &auth)).await;
    assert_eq!(listed.as_array().unwrap().len(), 1);

    // Interval validation.
    let (status, _) = send(
        &app,
        post_json(
            "/api/recurring",
            json!({"wallet_address": ADDR, "interval_minutes": 0}),
            &auth,
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);

    let delete = |uri: String| {
        Request::builder()
            .method("DELETE")
            .uri(uri)
            .header("authorization", &bearer)
            .body(Body::empty())
            .unwrap()
    };
    let (status, _) = send(&app, delete(format!("/api/recurring/{id}"))).await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    let (status, _) = send(&app, delete(format!("/api/recurring/{id}"))).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (_, listed) = send(&app, get("/api/recurring", &auth)).await;
    assert!(listed.as_array().unwrap().is_empty());
}

// ---------------------------------------------------------------- security audit + per-account throttle (ADR-057)

#[tokio::test]
async fn login_is_throttled_per_account_and_audited() {
    // Cap login at 2 attempts/account/window; keep the per-IP limiter generous
    // so we exercise the per-account throttle, not the IP one.
    let (app, audit) = app_with_audit(
        RateLimitConfig {
            auth_per_minute: 100,
            api_per_minute: 100,
            login_per_minute: 2,
            redis_url: None,
        },
        100,
    );
    register_verified(&app, "victim@b.com", "correct-horse").await;

    let bad = json!({"email": "victim@b.com", "password": "wrong"});
    let ip = &[("x-forwarded-for", "9.9.9.9")];
    // Two failed attempts stay under the cap: 401 each.
    for _ in 0..2 {
        let (status, _) = send(&app, post_json("/api/auth/login", bad.clone(), ip)).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    // Third attempt is throttled regardless of the password.
    let (status, body) = send(&app, post_json("/api/auth/login", bad.clone(), ip)).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS, "{body}");
    // Even the correct password is refused while the account is throttled.
    let good = json!({"email": "victim@b.com", "password": "correct-horse"});
    let (status, _) = send(&app, post_json("/api/auth/login", good, ip)).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);

    // The audit log recorded the failures and the throttling, with the IP.
    let events = audit.list_recent(10).await.unwrap();
    let kinds: Vec<&str> = events.iter().map(|e| e.kind.as_str()).collect();
    assert!(kinds.contains(&"login_failed"), "kinds: {kinds:?}");
    assert!(kinds.contains(&"login_throttled"), "kinds: {kinds:?}");
    assert!(events
        .iter()
        .any(|e| e.client_ip.as_deref() == Some("9.9.9.9")));
}

#[tokio::test]
async fn exceeding_the_daily_quota_is_audited() {
    let (app, audit) = app_with_audit(RateLimitConfig::default(), 0); // quota 0: first search denied
    let auth = format!(
        "Bearer {}",
        register_verified(&app, "q@b.com", "s3cret-password").await
    );

    let (status, _) = send(
        &app,
        post_json(
            "/api/searches",
            json!({"wallet_address": ADDR}),
            &[
                ("authorization", auth.as_str()),
                ("x-forwarded-for", "7.7.7.7"),
            ],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);

    let events = audit.list_recent(10).await.unwrap();
    assert!(events
        .iter()
        .any(|e| e.kind == "quota_exceeded" && e.user_id.is_some()));
}

// ---------------------------------------------------------------- email verification (ADR-062)

#[tokio::test]
async fn registering_does_not_sign_you_in_until_the_code_is_answered() {
    let app = app();
    let creds = json!({"email": "new@b.com", "password": "s3cret-password"});

    let (status, body) = send(&app, post_json("/api/auth/register", creds.clone(), &[])).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    assert_eq!(body["verification_required"], json!(true));
    // Registration issues no session: no token, no refresh cookie.
    assert!(body["access_token"].is_null());
    let code = body["verification_code"].as_str().unwrap().to_string();

    // The correct password gets a code, not a session (ADR-063).
    let (status, body) = send(&app, post_json("/api/auth/login", creds.clone(), &[])).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{body}");
    assert!(body["access_token"].is_null());

    // That login superseded the registration code, and saying so is the whole
    // point: "invalid" would send someone hunting for a typo.
    let resent = body["verification_code"].as_str().unwrap().to_string();
    assert_ne!(resent, code);
    let (status, body) = send(
        &app,
        post_json(
            "/api/auth/verify",
            json!({"email": "new@b.com", "code": code}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "superseded code: {body}");
    assert!(
        body["error"].as_str().unwrap().contains("replaced"),
        "a stale code must say it was replaced: {body}"
    );

    // The newest code works, and opens a full session (ADR-008).
    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/verify",
            json!({"email": "new@b.com", "code": resent}),
            &[],
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(refresh_cookie_value(&response).is_some());

    // Every later sign-in takes a code too — the password never suffices.
    let (status, body) = send(&app, post_json("/api/auth/login", creds, &[])).await;
    assert_eq!(status, StatusCode::ACCEPTED);
    assert!(body["access_token"].is_null());
}

#[tokio::test]
async fn a_stale_code_does_not_spend_an_attempt_on_the_live_one() {
    // ADR-063. Someone with two emails open reaches for the older one; five of
    // those must not lock them out of an account they can otherwise open.
    //
    // Limits are loosened because this test deliberately makes more than ten
    // auth calls: the subject is how attempts are *accounted*, and the per-IP
    // throttle (ADR-017) would otherwise mask it with a 429.
    let app = app_with(
        RateLimitConfig {
            auth_per_minute: 1000,
            api_per_minute: 100,
            login_per_minute: 1000,
            redis_url: None,
        },
        100,
    );
    let creds = json!({"email": "stale@b.com", "password": "s3cret-password"});
    let (_, registered) = send(&app, post_json("/api/auth/register", creds.clone(), &[])).await;
    let first = registered["verification_code"]
        .as_str()
        .unwrap()
        .to_string();

    let (_, logged_in) = send(&app, post_json("/api/auth/login", creds, &[])).await;
    let current = logged_in["verification_code"].as_str().unwrap().to_string();

    for _ in 0..8 {
        let (status, body) = send(
            &app,
            post_json(
                "/api/auth/verify",
                json!({"email": "stale@b.com", "code": first}),
                &[],
            ),
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert!(body["error"].as_str().unwrap().contains("replaced"));
    }

    // The live code is untouched by all that.
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/verify",
            json!({"email": "stale@b.com", "code": current}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn a_forgotten_password_can_be_reset_and_signs_you_in() {
    let app = app();
    register_verified(&app, "forgetful@b.com", "the-old-password").await;

    let (status, body) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "forgetful@b.com"}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED, "{body}");
    let code = body["verification_code"].as_str().unwrap().to_string();

    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/password/reset",
            json!({
                "email": "forgetful@b.com",
                "code": code,
                "password": "a-brand-new-password",
            }),
            &[],
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    // Recovery ends signed in: both factors were just proved.
    assert!(refresh_cookie_value(&response).is_some());

    // The new password works and the old one does not.
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/login",
            json!({"email": "forgetful@b.com", "password": "a-brand-new-password"}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/login",
            json!({"email": "forgetful@b.com", "password": "the-old-password"}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_reset_code_cannot_be_used_to_sign_in() {
    // ADR-063: the purposes are not interchangeable. Otherwise a reset code
    // would be a session key that skips the new-password step entirely.
    let app = app();
    register_verified(&app, "purpose@b.com", "s3cret-password").await;

    let (_, body) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "purpose@b.com"}),
            &[],
        ),
    )
    .await;
    let reset_code = body["verification_code"].as_str().unwrap().to_string();

    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/verify",
            json!({"email": "purpose@b.com", "code": reset_code}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn forgot_password_reveals_nothing_about_which_addresses_exist() {
    let app = app();
    register_verified(&app, "real@b.com", "s3cret-password").await;

    let (unknown_status, unknown) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "ghost@b.com"}),
            &[],
        ),
    )
    .await;
    let (known_status, _) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "real@b.com"}),
            &[],
        ),
    )
    .await;

    assert_eq!(unknown_status, known_status);
    assert_eq!(unknown_status, StatusCode::ACCEPTED);
    // Only the registered address actually got a code.
    assert!(unknown["verification_code"].is_null());
}

#[tokio::test]
async fn a_reset_revokes_sessions_opened_with_the_old_password() {
    // The reason most people reach for this form is "someone else is in my
    // account". A reset that leaves the intruder's refresh cookie alive fails
    // at the one job it was reached for.
    let app = app();
    let (_, registered) = send(
        &app,
        post_json(
            "/api/auth/register",
            json!({"email": "compromised@b.com", "password": "leaked-password"}),
            &[],
        ),
    )
    .await;
    let code = registered["verification_code"].as_str().unwrap();
    let response = app
        .clone()
        .oneshot(post_json(
            "/api/auth/verify",
            json!({"email": "compromised@b.com", "code": code}),
            &[],
        ))
        .await
        .unwrap();
    let intruder_cookie = refresh_cookie_value(&response).unwrap();

    let (_, forgot) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "compromised@b.com"}),
            &[],
        ),
    )
    .await;
    let reset_code = forgot["verification_code"].as_str().unwrap();
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/password/reset",
            json!({
                "email": "compromised@b.com",
                "code": reset_code,
                "password": "a-brand-new-password",
            }),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/refresh",
            json!({}),
            &[("cookie", &format!("refresh_token={intruder_cookie}"))],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "the old session must die");
}

#[tokio::test]
async fn a_reset_refuses_a_password_that_is_too_short() {
    let app = app();
    register_verified(&app, "shorty@b.com", "s3cret-password").await;
    let (_, body) = send(
        &app,
        post_json(
            "/api/auth/password/forgot",
            json!({"email": "shorty@b.com"}),
            &[],
        ),
    )
    .await;
    let code = body["verification_code"].as_str().unwrap().to_string();

    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/password/reset",
            json!({"email": "shorty@b.com", "code": code, "password": "short"}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);

    // And the code survived, so the retry does not need a fresh email.
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/password/reset",
            json!({
                "email": "shorty@b.com",
                "code": code,
                "password": "long-enough-password",
            }),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn a_wrong_code_is_refused_and_guessing_burns_the_code() {
    let app = app();
    let (_, body) = send(
        &app,
        post_json(
            "/api/auth/register",
            json!({"email": "guess@b.com", "password": "s3cret-password"}),
            &[],
        ),
    )
    .await;
    let code = body["verification_code"].as_str().unwrap().to_string();
    let wrong = if code == "000000" { "111111" } else { "000000" };
    let attempt = |code: &str| {
        post_json(
            "/api/auth/verify",
            json!({"email": "guess@b.com", "code": code}),
            &[],
        )
    };

    for _ in 0..5 {
        let (status, _) = send(&app, attempt(wrong)).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    // Past the cap the correct code no longer works: brute force is not
    // eventually rewarded, it is locked out.
    let (status, body) = send(&app, attempt(&code)).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS, "{body}");
}

#[tokio::test]
async fn resend_reveals_nothing_about_which_addresses_exist() {
    let app = app();
    register_verified(&app, "known@b.com", "s3cret-password").await;

    // Unregistered, and registered-but-already-verified, must be answered
    // exactly like a real send, or this endpoint becomes a way to enumerate
    // accounts.
    for email in ["nobody@b.com", "known@b.com"] {
        let (status, body) = send(
            &app,
            post_json("/api/auth/verify/resend", json!({"email": email}), &[]),
        )
        .await;
        assert_eq!(status, StatusCode::ACCEPTED, "{email}: {body}");
        assert!(body["verification_code"].is_null(), "{email}: {body}");
    }

    // A code for a verified account is never accepted either.
    let (status, _) = send(
        &app,
        post_json(
            "/api/auth/verify",
            json!({"email": "known@b.com", "code": "123456"}),
            &[],
        ),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}
