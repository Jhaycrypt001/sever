//! Inbound HTTP adapter (Axum): routes, DTOs, auth extractor, rate limiting,
//! request correlation, SSE job updates.

pub mod rate_limit;
pub mod request_id;
pub mod security_headers;
pub mod sse;

use std::sync::Arc;

use axum::extract::{FromRequestParts, Path, State};
use axum::http::request::Parts;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::json;
use utoipa::{OpenApi, ToSchema};
use utoipa_swagger_ui::SwaggerUi;
use uuid::Uuid;

use crate::application::answer_clarification::AnswerError;
use crate::application::ingest_results::IngestError;
use crate::application::keeperhub_key::KeeperHubKeyError;
use crate::application::launch_search::LaunchError;
use crate::application::login_user::LoginError;
use crate::application::password_reset::{RequestResetError, ResetPasswordError};
use crate::application::recurring_searches::RecurringError;
use crate::application::refresh_session::RefreshError;
use crate::application::register_user::RegisterError;
use crate::application::verify_email::{ConfirmVerificationError, RequestVerificationError};
use crate::application::{
    AnswerClarification, ConfirmEmailVerification, IngestResults, KeeperHubKeys, LaunchSearch,
    LoginUser, RecurringSearches, RefreshSession, RegisterUser, RequestEmailVerification,
    RequestPasswordReset, ResetPassword, SearchQueries, SessionIssuer, SessionTokens,
};
use crate::domain::ports::{
    DigestSender, EmailSender, EmailVerificationRepository, JobDispatcher, JobRepository,
    KeeperHubCredentialRepository, KeeperHubDirectory, PasswordHasher, RecurringSearchRepository,
    RefreshTokenRepository, SecurityAudit, TokenService, UserRepository,
};
use crate::domain::{
    AgentStep, ApprovalFinding, JobMode, JobStatus, JobUsage, RecurringSearch, RevocationStatus,
    RiskTier, ScanJob, SecretBox, SecurityEvent, SecurityEventKind,
};

/// Name of the HttpOnly cookie carrying the refresh token (ADR-008).
const REFRESH_COOKIE: &str = "refresh_token";

#[derive(Clone)]
pub struct AppState {
    register: Arc<RegisterUser>,
    login: Arc<LoginUser>,
    /// Issue/resend a verification code (ADR-062).
    request_verification: Arc<RequestEmailVerification>,
    /// Accept a code and open a session (ADR-062). The only route to a
    /// session there is — password login stops one step short (ADR-063).
    confirm_verification: Arc<ConfirmEmailVerification>,
    /// Mail a password-reset code (ADR-063).
    request_reset: Arc<RequestPasswordReset>,
    /// Set a new password from a reset code, and sign in (ADR-063).
    reset_password: Arc<ResetPassword>,
    /// Whether the API may echo verification codes back to the client.
    /// Development only — see `AppConfig::expose_verification_codes`.
    expose_verification_codes: bool,
    /// Per-account throttle on verification attempts (ADR-057/062).
    verify_throttle: rate_limit::Limiter,
    refresh: Arc<RefreshSession>,
    launch: Arc<LaunchSearch>,
    answer: Arc<AnswerClarification>,
    recurring: Arc<RecurringSearches>,
    ingest: Arc<IngestResults>,
    queries: Arc<SearchQueries>,
    tokens: Arc<dyn TokenService>,
    /// Security audit log (ADR-057): failed/throttled logins, quota hits.
    audit: Arc<dyn SecurityAudit>,
    /// Per-account login throttle (ADR-057), keyed by email — independent of
    /// the per-IP limiter, so credential-stuffing across many IPs is capped too.
    login_throttle: rate_limit::Limiter,
    internal_token: String,
    refresh_ttl_days: i64,
    /// Per-user KeeperHub keys (ADR-076). `None` when the deployment has no
    /// `CREDENTIAL_ENCRYPTION_KEY` — the feature is then simply absent rather
    /// than storing keys unencrypted.
    keeperhub_keys: Option<Arc<KeeperHubKeys>>,
    /// Ingredients of the two dispatching use cases, kept so enabling ADR-076
    /// can rebuild them with the key store attached. Cheap `Arc` clones —
    /// the alternative is an `Option` inside the use cases mutated after
    /// construction, which is harder to reason about than rebuilding.
    dispatch_parts: (Arc<dyn JobRepository>, Arc<dyn JobDispatcher>, u32),
}

/// HTTP throttling knobs (ADR-017). Internal routes are never rate limited.
#[derive(Clone, Debug)]
pub struct RateLimitConfig {
    /// Per-IP limit on `/api/auth/*` (brute-force protection).
    pub auth_per_minute: u32,
    /// Per-IP limit on the rest of `/api/*`.
    pub api_per_minute: u32,
    /// Per-**account** limit on login attempts (ADR-057): email-keyed, so a
    /// credential-stuffing run spread over many IPs is throttled per target.
    pub login_per_minute: u32,
    /// Redis-backed distributed limiting (ADR-037): set when the backend
    /// scales horizontally; None keeps the in-memory limiter.
    pub redis_url: Option<String>,
}

impl Default for RateLimitConfig {
    fn default() -> Self {
        Self {
            auth_per_minute: 10,
            api_per_minute: 120,
            login_per_minute: 10,
            redis_url: None,
        }
    }
}

/// Everything email verification needs (ADR-062), grouped so the wiring
/// point does not grow four more positional arguments — two of which would be
/// an `i64` and a `bool` sitting next to each other.
pub struct EmailVerificationSetup {
    pub verifications: Arc<dyn EmailVerificationRepository>,
    pub mailer: Arc<dyn EmailSender>,
    pub ttl_minutes: i64,
    /// Return the code in the API response instead of relying on the mailbox.
    /// `AppConfig` forces this off in production.
    pub expose_codes: bool,
    /// Per-account throttle on code attempts. Sized like the login throttle
    /// but a **separate** budget on a separate key namespace: the two guess at
    /// different secrets, and sharing one meant a person fumbling their code
    /// spent the attempts they would need to sign in afterwards.
    pub throttle: rate_limit::Limiter,
}

/// Everything per-user KeeperHub keys need (ADR-076).
///
/// Optional as a whole: without `CREDENTIAL_ENCRYPTION_KEY` there is nowhere
/// safe to put a key, so the feature is off and its routes answer 501 rather
/// than storing anything in the clear. Grouped for the same reason as
/// [`EmailVerificationSetup`] — three more positional arguments on a wiring
/// point that already carries fourteen.
pub struct KeeperHubSetup {
    pub credentials: Arc<dyn KeeperHubCredentialRepository>,
    pub directory: Arc<dyn KeeperHubDirectory>,
    pub secrets: SecretBox,
}

impl AppState {
    #[allow(clippy::too_many_arguments)] // boilerplate wiring point, one call site per binary
    pub fn new(
        users: Arc<dyn UserRepository>,
        jobs: Arc<dyn JobRepository>,
        refresh_tokens: Arc<dyn RefreshTokenRepository>,
        recurring: Arc<dyn RecurringSearchRepository>,
        dispatcher: Arc<dyn JobDispatcher>,
        digests: Arc<dyn DigestSender>,
        hasher: Arc<dyn PasswordHasher>,
        tokens: Arc<dyn TokenService>,
        audit: Arc<dyn SecurityAudit>,
        email: EmailVerificationSetup,
        login_throttle: rate_limit::Limiter,
        internal_token: String,
        daily_search_quota: u32,
        refresh_ttl_days: i64,
    ) -> Self {
        // One issuer, so a password login and an answered code open the same
        // kind of session (ADR-062).
        let sessions = Arc::new(SessionIssuer::new(
            tokens.clone(),
            refresh_tokens.clone(),
            refresh_ttl_days,
        ));
        Self {
            register: Arc::new(RegisterUser::new(users.clone(), hasher.clone())),
            login: Arc::new(LoginUser::new(users.clone(), hasher.clone())),
            request_verification: Arc::new(RequestEmailVerification::new(
                users.clone(),
                email.verifications.clone(),
                email.mailer.clone(),
                email.ttl_minutes,
            )),
            confirm_verification: Arc::new(ConfirmEmailVerification::new(
                users.clone(),
                email.verifications.clone(),
                sessions.clone(),
            )),
            request_reset: Arc::new(RequestPasswordReset::new(
                users.clone(),
                email.verifications.clone(),
                email.mailer,
                email.ttl_minutes,
            )),
            reset_password: Arc::new(ResetPassword::new(
                users,
                email.verifications,
                hasher,
                refresh_tokens.clone(),
                sessions,
            )),
            expose_verification_codes: email.expose_codes,
            verify_throttle: email.throttle,
            refresh: Arc::new(RefreshSession::new(
                refresh_tokens,
                tokens.clone(),
                audit.clone(),
                refresh_ttl_days,
            )),
            launch: Arc::new(LaunchSearch::new(
                jobs.clone(),
                dispatcher.clone(),
                daily_search_quota,
            )),
            answer: Arc::new(AnswerClarification::new(jobs.clone(), dispatcher.clone())),
            dispatch_parts: (jobs.clone(), dispatcher, daily_search_quota),
            recurring: Arc::new(RecurringSearches::new(recurring.clone())),
            ingest: Arc::new(IngestResults::new(jobs.clone(), recurring, digests)),
            queries: Arc::new(SearchQueries::new(jobs)),
            tokens,
            audit,
            login_throttle,
            internal_token,
            refresh_ttl_days,
            keeperhub_keys: None,
        }
    }

    /// Enables per-user KeeperHub keys (ADR-076). Without this the settings
    /// routes answer 501 and every scan uses the worker's environment key.
    #[must_use]
    pub fn with_keeperhub_keys(self, setup: KeeperHubSetup) -> Self {
        let keys = Arc::new(KeeperHubKeys::new(
            setup.credentials,
            setup.directory,
            self.audit.clone(),
            setup.secrets,
        ));
        self.with_shared_keeperhub_keys(Some(keys))
    }

    /// Same, from an already-built store, so `main` can hand the *same*
    /// instance to the background scheduler (ADR-033). `None` leaves the
    /// feature off: the settings routes answer 501 and scans fall back to the
    /// worker's environment key.
    #[must_use]
    pub fn with_shared_keeperhub_keys(mut self, keys: Option<Arc<KeeperHubKeys>>) -> Self {
        let (jobs, dispatcher, quota) = self.dispatch_parts.clone();
        // Rebuilt, not mutated: both dispatch paths must carry the owner's key
        // or a connected account silently keeps revoking as the environment
        // wallet — the exact failure ADR-076 exists to remove.
        self.launch = Arc::new(
            LaunchSearch::new(jobs.clone(), dispatcher.clone(), quota)
                .with_keeperhub_keys(keys.clone()),
        );
        self.answer =
            Arc::new(AnswerClarification::new(jobs, dispatcher).with_keeperhub_keys(keys.clone()));
        self.keeperhub_keys = keys;
        self
    }

    /// The verification code, but only where showing it is allowed.
    ///
    /// The gate lives here rather than in the use case so there is exactly one
    /// place to audit: if `expose_verification_codes` is false — which
    /// `AppConfig` guarantees in production — no response can carry a code,
    /// whatever a handler passes in.
    fn exposed_code(&self, code: Option<String>) -> Option<String> {
        code.filter(|_| self.expose_verification_codes)
    }
}

// ---------------------------------------------------------------- OpenAPI (ADR-049 amendment)

/// `{ "job_id": "…" }` — a launched search.
#[derive(Serialize, ToSchema)]
#[allow(dead_code)]
struct JobCreatedResponse {
    job_id: Uuid,
}

/// `{ "access_token": "…" }` — the short-lived bearer; the refresh token is set
/// as an HttpOnly cookie (ADR-008), not in the body.
#[derive(Serialize, ToSchema)]
#[allow(dead_code)]
struct AccessTokenResponse {
    access_token: String,
}

/// A created account (ADR-062). `verification_required` is always true: the
/// account exists but cannot be signed into until the emailed code is entered,
/// and the client uses this to move straight to the code screen.
///
/// `verification_code` is present **only** in a development configuration with
/// no email provider, so the console can be driven without a mailbox. It is
/// absent in production, where `AppConfig` refuses to expose it.
#[derive(Serialize, ToSchema)]
#[allow(dead_code)]
struct RegistrationResponse {
    id: Uuid,
    email: String,
    verification_required: bool,
    verification_code: Option<String>,
}

/// The answer to "send me a code".
///
/// Carries no claim that anything was sent — it cannot, because the request is
/// answered identically for an unregistered address and an already-verified
/// one, and saying `sent: true` there would be a field that lies. The client's
/// wording is conditional to match ("if that address needs a code…").
#[derive(Serialize, ToSchema)]
#[allow(dead_code)]
struct VerificationSentResponse {
    verification_code: Option<String>,
}

/// `{ "error": "…" }` — the uniform error body (ADR-018).
#[derive(Serialize, ToSchema)]
#[allow(dead_code)]
struct ErrorResponse {
    error: String,
}

/// Declares the `bearer` (JWT) auth scheme referenced by the protected paths.
struct SecurityAddon;

impl utoipa::Modify for SecurityAddon {
    fn modify(&self, openapi: &mut utoipa::openapi::OpenApi) {
        use utoipa::openapi::security::{HttpAuthScheme, HttpBuilder, SecurityScheme};
        if let Some(components) = openapi.components.as_mut() {
            components.add_security_scheme(
                "bearer",
                SecurityScheme::Http(
                    HttpBuilder::new()
                        .scheme(HttpAuthScheme::Bearer)
                        .bearer_format("JWT")
                        .build(),
                ),
            );
        }
    }
}

/// The public HTTP API (ADR-049 amendment). This documents only the public
/// surface (Vue → Rust); the `/internal/*` worker callbacks (ADR-006) are pinned
/// by the contract fixtures instead. The contract's source of truth stays the
/// zod schemas + fixtures (ADR-049) — this is browsable documentation, served
/// self-hosted at `/api/docs`, with the raw spec at `/api/openapi.json`.
#[derive(OpenApi)]
#[openapi(
    modifiers(&SecurityAddon),
    info(
        title = "AI agent boilerplate — public API",
        description = "The browser-facing API (auth, searches, recurring searches). \
                       Contract pinned by fixtures + zod (ADR-049).",
        version = env!("CARGO_PKG_VERSION"),
        license(name = "MIT"),
    ),
    paths(
        register,
        login,
        verify_email,
        resend_verification,
        forgot_password,
        reset_password,
        refresh,
        logout,
        create_search,
        list_searches,
        get_search,
        answer_search,
        create_recurring,
        list_recurring,
        delete_recurring,
        get_keeperhub_key,
        put_keeperhub_key,
        delete_keeperhub_key,
    ),
    components(schemas(
        CredentialsRequest,
        VerifyEmailRequest,
        ResendVerificationRequest,
        ResetPasswordRequest,
        RegistrationResponse,
        VerificationSentResponse,
        CreateSearchRequest,
        CreateRecurringRequest,
        AnswerRequest,
        ConnectKeeperHubRequest,
        KeeperHubKeyView,
        JobView,
        JobDetailView,
        RecurringView,
        JobCreatedResponse,
        AccessTokenResponse,
        ErrorResponse,
        ApprovalFinding,
        AgentStep,
        JobUsage,
        JobStatus,
        JobMode,
        RiskTier,
        RevocationStatus,
    )),
    tags(
        (name = "auth", description = "Registration, login, session refresh (ADR-008)"),
        (name = "searches", description = "Launch and read approval scans (ADR-030/032/058)"),
        (name = "recurring", description = "Scheduled recurring scans (ADR-033)"),
    ),
)]
pub struct ApiDoc;

pub fn router(state: AppState) -> Router {
    router_with_limits(state, RateLimitConfig::default())
}

pub fn router_with_limits(state: AppState, limits: RateLimitConfig) -> Router {
    let redis_url = limits.redis_url.as_deref();
    let auth_limiter = rate_limit::Limiter::per_minute(limits.auth_per_minute, "auth", redis_url);
    let api_limiter = rate_limit::Limiter::per_minute(limits.api_per_minute, "api", redis_url);

    let auth_routes = Router::new()
        .route("/api/auth/register", post(register))
        .route("/api/auth/login", post(login))
        .route("/api/auth/verify", post(verify_email))
        .route("/api/auth/verify/resend", post(resend_verification))
        .route("/api/auth/password/forgot", post(forgot_password))
        .route("/api/auth/password/reset", post(reset_password))
        .route("/api/auth/refresh", post(refresh))
        .route("/api/auth/logout", post(logout))
        .layer(axum::middleware::from_fn_with_state(
            auth_limiter,
            rate_limit::rate_limit,
        ));

    let api_routes = Router::new()
        .route("/api/searches", post(create_search).get(list_searches))
        .route("/api/searches/{id}", get(get_search))
        .route("/api/searches/{id}/answer", post(answer_search))
        .route("/api/searches/{id}/events", get(search_events))
        .route("/api/recurring", post(create_recurring).get(list_recurring))
        .route(
            "/api/recurring/{id}",
            axum::routing::delete(delete_recurring),
        )
        .route(
            "/api/settings/keeperhub-key",
            get(get_keeperhub_key)
                .put(put_keeperhub_key)
                .delete(delete_keeperhub_key),
        )
        .layer(axum::middleware::from_fn_with_state(
            api_limiter,
            rate_limit::rate_limit,
        ));

    Router::new()
        .route("/healthz", get(|| async { "ok" }))
        // Interactive API docs (ADR-049 amendment): Swagger UI at /api/docs,
        // raw spec at /api/openapi.json. Assets are vendored (self-hosted).
        .merge(SwaggerUi::new("/api/docs").url("/api/openapi.json", ApiDoc::openapi()))
        .merge(auth_routes)
        .merge(api_routes)
        .route("/internal/jobs/{id}/started", post(internal_started))
        .route("/internal/jobs/{id}/results", post(internal_results))
        .route("/internal/jobs/{id}/steps", post(internal_step))
        .route("/internal/jobs/{id}/question", post(internal_question))
        .route("/internal/jobs/{id}/usage", post(internal_usage))
        .route("/internal/jobs/{id}/failure", post(internal_failure))
        // Correlation span (ADR-018) around every request.
        .layer(axum::middleware::from_fn(request_id::request_id))
        // Outermost: security headers on every response, errors included (ADR-054).
        .layer(axum::middleware::from_fn(
            security_headers::security_headers,
        ))
        .with_state(state)
}

fn error_body(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({ "error": message }))).into_response()
}

/// Best-effort client IP for the audit log (ADR-057): the first
/// `X-Forwarded-For` entry set by the trusted proxy (ADR-014/015), like the
/// rate limiter keys on. `None` when the header is absent (e.g. tests).
fn client_ip(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(',').next())
        .map(|ip| ip.trim().to_string())
        .filter(|ip| !ip.is_empty())
}

/// Records a security event (ADR-057). Always logs a structured line so it
/// surfaces in the observability pillars (ADR-018/050) even if the DB write
/// fails; the persistence itself is best-effort and never breaks the request.
async fn record_security_event(state: &AppState, event: SecurityEvent) {
    tracing::warn!(
        security_event = %event.kind,
        user_id = ?event.user_id,
        client_ip = ?event.client_ip,
        detail = %event.detail,
        "security event"
    );
    if let Err(e) = state.audit.record(&event).await {
        tracing::error!(error = %e, "failed to record security event");
    }
}

// ---------------------------------------------------------------- auth extractor

/// Extracts the authenticated user id from the `Authorization: Bearer <jwt>` header.
pub struct AuthUser(pub Uuid);

impl FromRequestParts<AppState> for AuthUser {
    type Rejection = Response;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let user_id = parts
            .headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .and_then(|token| state.tokens.verify(token))
            .ok_or_else(|| {
                error_body(StatusCode::UNAUTHORIZED, "invalid or missing access token")
            })?;
        Ok(AuthUser(user_id))
    }
}

// ---------------------------------------------------------------- refresh cookie helpers

/// `Set-Cookie` value for the refresh token: HttpOnly + Secure + SameSite=Strict,
/// scoped to the auth endpoints only (ADR-008). Browsers exempt localhost from
/// the Secure requirement, so development over http keeps working.
fn refresh_cookie(value: &str, max_age_seconds: i64) -> String {
    format!(
        "{REFRESH_COOKIE}={value}; HttpOnly; Secure; SameSite=Strict; Path=/api/auth; Max-Age={max_age_seconds}"
    )
}

fn clear_refresh_cookie() -> String {
    refresh_cookie("", 0)
}

/// Extracts the refresh token from the `Cookie` header.
fn read_refresh_cookie(headers: &HeaderMap) -> Option<String> {
    headers
        .get("cookie")
        .and_then(|v| v.to_str().ok())?
        .split(';')
        .filter_map(|pair| pair.trim().split_once('='))
        .find(|(name, _)| *name == REFRESH_COOKIE)
        .map(|(_, value)| value.to_string())
        .filter(|v| !v.is_empty())
}

fn session_response(state: &AppState, tokens: SessionTokens) -> Response {
    let cookie = refresh_cookie(&tokens.refresh_token, state.refresh_ttl_days * 86_400);
    (
        [("set-cookie", cookie)],
        Json(json!({ "access_token": tokens.access_token, "token_type": "Bearer" })),
    )
        .into_response()
}

/// Returns a rejection response when the internal token is missing or wrong.
/// Constant-time byte comparison (ADR-055): the loop runs over the whole input
/// regardless of where a mismatch is, so the compare time does not leak how much
/// of a guessed token was correct. The length is allowed to short-circuit (it is
/// not the secret). Mirrors the constant-time check the HMAC path already uses.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b) {
        diff |= x ^ y;
    }
    diff == 0
}

fn check_internal_token(state: &AppState, headers: &HeaderMap) -> Option<Response> {
    let matches = headers
        .get("x-internal-token")
        .map(|v| constant_time_eq(v.as_bytes(), state.internal_token.as_bytes()))
        .unwrap_or(false);
    if !matches {
        return Some(error_body(
            StatusCode::UNAUTHORIZED,
            "invalid or missing internal token",
        ));
    }
    None
}

// ---------------------------------------------------------------- DTOs

#[derive(Deserialize, ToSchema)]
struct CredentialsRequest {
    email: String,
    password: String,
}

#[derive(Deserialize, ToSchema)]
struct CreateSearchRequest {
    wallet_address: String,
    // Workflow (fixed pipeline) or agent (decision loop, ADR-030); defaulted
    // so pre-ADR-030 clients keep working.
    #[serde(default)]
    mode: JobMode,
}

#[derive(Serialize, ToSchema)]
struct JobView {
    id: Uuid,
    wallet_address: String,
    mode: JobMode,
    status: JobStatus,
    error: Option<String>,
    // Clarification dialog (ADR-032), null until the agent asks / the user answers.
    question: Option<String>,
    answer: Option<String>,
    // Set on scheduler-launched runs (ADR-033).
    recurring_search_id: Option<Uuid>,
    // Accumulated API spend (ADR-038).
    usage: JobUsage,
    created_at: DateTime<Utc>,
    completed_at: Option<DateTime<Utc>>,
}

impl From<&ScanJob> for JobView {
    fn from(job: &ScanJob) -> Self {
        Self {
            id: job.id,
            wallet_address: job.wallet_address.clone(),
            mode: job.mode,
            status: job.status,
            error: job.error.clone(),
            question: job.question.clone(),
            answer: job.answer.clone(),
            recurring_search_id: job.recurring_search_id,
            usage: job.usage,
            created_at: job.created_at,
            completed_at: job.completed_at,
        }
    }
}

#[derive(Deserialize, ToSchema)]
struct CreateRecurringRequest {
    wallet_address: String,
    #[serde(default)]
    mode: JobMode,
    interval_minutes: u32,
    /// Digest target (ADR-036): notified when a run finds new results.
    #[serde(default)]
    webhook_url: Option<String>,
}

#[derive(Serialize, ToSchema)]
struct RecurringView {
    id: Uuid,
    wallet_address: String,
    mode: JobMode,
    interval_minutes: u32,
    webhook_url: Option<String>,
    created_at: DateTime<Utc>,
    last_run_at: Option<DateTime<Utc>>,
}

impl From<&RecurringSearch> for RecurringView {
    fn from(search: &RecurringSearch) -> Self {
        Self {
            id: search.id,
            wallet_address: search.wallet_address.clone(),
            mode: search.mode,
            interval_minutes: search.interval_minutes,
            webhook_url: search.webhook_url.clone(),
            created_at: search.created_at,
            last_run_at: search.last_run_at,
        }
    }
}

#[derive(Deserialize)]
struct ResultsRequest {
    results: Vec<ApprovalFinding>,
}

#[derive(Deserialize)]
struct FailureRequest {
    error: String,
}

#[derive(Deserialize)]
struct QuestionRequest {
    question: String,
}

#[derive(Deserialize, ToSchema)]
struct AnswerRequest {
    answer: String,
}

/// `{ "email": "…", "code": "123456" }` — answering a verification code.
#[derive(Deserialize, ToSchema)]
struct VerifyEmailRequest {
    email: String,
    code: String,
}

/// `{ "email": "…" }` — "send me another code", and "I forgot my password".
#[derive(Deserialize, ToSchema)]
struct ResendVerificationRequest {
    email: String,
}

/// `{ "email": "…", "code": "123456", "password": "…" }` — account recovery.
#[derive(Deserialize, ToSchema)]
struct ResetPasswordRequest {
    email: String,
    code: String,
    password: String,
}

// ---------------------------------------------------------------- public handlers

/// Creates the account and mails it a verification code (ADR-062). No session
/// is issued here — `POST /api/auth/verify` does that, once the address has
/// answered.
#[utoipa::path(post, path = "/api/auth/register", tag = "auth",
    request_body = CredentialsRequest,
    responses(
        (status = 201, description = "Account created, verification code sent", body = RegistrationResponse),
        (status = 409, description = "Email already registered", body = ErrorResponse),
        (status = 422, description = "Invalid credentials", body = ErrorResponse),
        (status = 502, description = "The verification email could not be sent", body = ErrorResponse)))]
async fn register(State(state): State<AppState>, Json(body): Json<CredentialsRequest>) -> Response {
    let user = match state.register.execute(&body.email, &body.password).await {
        Ok(user) => user,
        Err(RegisterError::EmailTaken) => {
            return error_body(StatusCode::CONFLICT, "email already registered")
        }
        Err(e @ (RegisterError::InvalidEmail | RegisterError::PasswordTooShort)) => {
            return error_body(StatusCode::UNPROCESSABLE_ENTITY, &e.to_string())
        }
        Err(RegisterError::Infrastructure(e)) => {
            tracing::error!(error = %e, "register failed");
            return error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error");
        }
    };

    let code = match state.request_verification.execute(&user.email).await {
        Ok(code) => code,
        Err(RequestVerificationError::Delivery(e)) => {
            // The account exists but its code never left the building. Saying
            // so is the only honest answer: the alternative parks someone in
            // front of a code box that nothing can satisfy. `resend` is the
            // recovery path once the provider is back.
            tracing::error!(error = %e, "verification email delivery failed");
            return error_body(
                StatusCode::BAD_GATEWAY,
                "account created, but the verification email could not be sent — try resending in a moment",
            );
        }
        Err(RequestVerificationError::Infrastructure(e)) => {
            tracing::error!(error = %e, "verification code could not be issued");
            return error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error");
        }
    };

    (
        StatusCode::CREATED,
        Json(json!({
            "id": user.id,
            "email": user.email,
            "verification_required": true,
            "verification_code": state.exposed_code(code),
        })),
    )
        .into_response()
}

/// Accepts a verification code and signs the account in (ADR-062).
#[utoipa::path(post, path = "/api/auth/verify", tag = "auth",
    request_body = VerifyEmailRequest,
    responses(
        (status = 200, description = "Address verified; access token in body, refresh token as an HttpOnly cookie", body = AccessTokenResponse),
        (status = 401, description = "Wrong, expired, or already-used code", body = ErrorResponse),
        (status = 429, description = "Too many incorrect attempts on this code", body = ErrorResponse)))]
async fn verify_email(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<VerifyEmailRequest>,
) -> Response {
    // Same per-account throttle as login (ADR-057): a six-digit code is only
    // as strong as the number of guesses allowed per minute.
    let email_key = body.email.trim().to_lowercase();
    let ip = client_ip(&headers);
    if !state.verify_throttle.allow(&email_key).await {
        record_security_event(
            &state,
            SecurityEvent::new(SecurityEventKind::LoginThrottled, None, ip, email_key),
        )
        .await;
        return error_body(
            StatusCode::TOO_MANY_REQUESTS,
            "too many attempts, slow down",
        );
    }

    match state
        .confirm_verification
        .execute(&body.email, &body.code)
        .await
    {
        Ok(tokens) => session_response(&state, tokens),
        Err(
            e @ (ConfirmVerificationError::InvalidCode
            | ConfirmVerificationError::Superseded
            | ConfirmVerificationError::Expired),
        ) => {
            record_security_event(
                &state,
                SecurityEvent::new(SecurityEventKind::LoginFailed, None, ip, email_key),
            )
            .await;
            error_body(StatusCode::UNAUTHORIZED, &e.to_string())
        }
        Err(e @ ConfirmVerificationError::TooManyAttempts) => {
            error_body(StatusCode::TOO_MANY_REQUESTS, &e.to_string())
        }
        Err(ConfirmVerificationError::Infrastructure(e)) => {
            tracing::error!(error = %e, "verification failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Issues a fresh code, superseding any outstanding one (ADR-062).
///
/// Answers 202 whether or not the address is registered or already verified:
/// a distinguishable response would make this an account-enumeration oracle.
#[utoipa::path(post, path = "/api/auth/verify/resend", tag = "auth",
    request_body = ResendVerificationRequest,
    responses(
        (status = 202, description = "A code was sent if the address needed one", body = VerificationSentResponse),
        (status = 502, description = "The verification email could not be sent", body = ErrorResponse)))]
async fn resend_verification(
    State(state): State<AppState>,
    Json(body): Json<ResendVerificationRequest>,
) -> Response {
    match state.request_verification.execute(&body.email).await {
        Ok(code) => (
            StatusCode::ACCEPTED,
            Json(json!({ "verification_code": state.exposed_code(code) })),
        )
            .into_response(),
        Err(RequestVerificationError::Delivery(e)) => {
            tracing::error!(error = %e, "verification email delivery failed");
            error_body(
                StatusCode::BAD_GATEWAY,
                "the verification email could not be sent — try again in a moment",
            )
        }
        Err(RequestVerificationError::Infrastructure(e)) => {
            tracing::error!(error = %e, "verification code could not be issued");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Checks the password and mails a sign-in code (ADR-063).
///
/// Returns **202, not a session**: every sign-in takes two factors, and this
/// endpoint owns only the first. `POST /api/auth/verify` finishes the job.
#[utoipa::path(post, path = "/api/auth/login", tag = "auth",
    request_body = CredentialsRequest,
    responses(
        (status = 202, description = "Password accepted, sign-in code sent — finish at /api/auth/verify (ADR-063)", body = VerificationSentResponse),
        (status = 401, description = "Invalid email or password", body = ErrorResponse),
        (status = 502, description = "The sign-in code could not be sent", body = ErrorResponse)))]
async fn login(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CredentialsRequest>,
) -> Response {
    // Per-account throttle (ADR-057): keyed by the normalized email so an
    // attacker cannot dodge it with IP rotation or case tricks. Checked before
    // the (deliberately costly) argon2 verify, so it also sheds that load.
    let email_key = body.email.trim().to_lowercase();
    let ip = client_ip(&headers);
    if !state.login_throttle.allow(&email_key).await {
        record_security_event(
            &state,
            SecurityEvent::new(SecurityEventKind::LoginThrottled, None, ip, email_key),
        )
        .await;
        return error_body(
            StatusCode::TOO_MANY_REQUESTS,
            "too many login attempts, slow down",
        );
    }

    let user = match state.login.execute(&body.email, &body.password).await {
        Ok(user) => user,
        Err(LoginError::InvalidCredentials) => {
            record_security_event(
                &state,
                SecurityEvent::new(SecurityEventKind::LoginFailed, None, ip, email_key),
            )
            .await;
            return error_body(StatusCode::UNAUTHORIZED, "invalid credentials");
        }
        Err(LoginError::Infrastructure(e)) => {
            tracing::error!(error = %e, "login failed");
            return error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error");
        }
    };

    // Password accepted — now the second factor. Issued here rather than in
    // the use case so that the only thing holding a `SessionIssuer` is the
    // code-confirming path.
    match state.request_verification.issue_for(&user).await {
        Ok(code) => (
            StatusCode::ACCEPTED,
            Json(json!({ "verification_code": state.exposed_code(Some(code)) })),
        )
            .into_response(),
        Err(RequestVerificationError::Delivery(e)) => {
            // Surfaced, not swallowed: without the code nobody can finish this
            // sign-in, so silence would look like the password was wrong.
            tracing::error!(error = %e, "sign-in code delivery failed");
            error_body(
                StatusCode::BAD_GATEWAY,
                "your password was accepted, but the sign-in code could not be sent — try again in a moment",
            )
        }
        Err(RequestVerificationError::Infrastructure(e)) => {
            tracing::error!(error = %e, "sign-in code could not be issued");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Mails a password-reset code (ADR-063).
///
/// Always 202. "No account with that address" is exactly the sentence that
/// turns a forgot-password form into an account enumeration tool.
#[utoipa::path(post, path = "/api/auth/password/forgot", tag = "auth",
    request_body = ResendVerificationRequest,
    responses(
        (status = 202, description = "A reset code was sent if the address has an account", body = VerificationSentResponse),
        (status = 502, description = "The reset email could not be sent", body = ErrorResponse)))]
async fn forgot_password(
    State(state): State<AppState>,
    Json(body): Json<ResendVerificationRequest>,
) -> Response {
    // Same per-account budget as the code screen: this endpoint sends mail to
    // an address chosen by an unauthenticated caller, so it is the one most
    // worth throttling.
    let email_key = body.email.trim().to_lowercase();
    if !state.verify_throttle.allow(&email_key).await {
        return error_body(
            StatusCode::TOO_MANY_REQUESTS,
            "too many attempts, slow down",
        );
    }

    match state.request_reset.execute(&body.email).await {
        Ok(code) => (
            StatusCode::ACCEPTED,
            Json(json!({ "verification_code": state.exposed_code(code) })),
        )
            .into_response(),
        Err(RequestResetError::Delivery(e)) => {
            tracing::error!(error = %e, "password reset email delivery failed");
            error_body(
                StatusCode::BAD_GATEWAY,
                "the reset email could not be sent — try again in a moment",
            )
        }
        Err(RequestResetError::Infrastructure(e)) => {
            tracing::error!(error = %e, "reset code could not be issued");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Sets a new password from a reset code and signs in (ADR-063).
#[utoipa::path(post, path = "/api/auth/password/reset", tag = "auth",
    request_body = ResetPasswordRequest,
    responses(
        (status = 200, description = "Password changed; every other session revoked; access token in body", body = AccessTokenResponse),
        (status = 401, description = "Wrong, stale, or expired reset code", body = ErrorResponse),
        (status = 422, description = "New password too short", body = ErrorResponse),
        (status = 429, description = "Too many incorrect attempts on this code", body = ErrorResponse)))]
async fn reset_password(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ResetPasswordRequest>,
) -> Response {
    let email_key = body.email.trim().to_lowercase();
    let ip = client_ip(&headers);
    if !state.verify_throttle.allow(&email_key).await {
        return error_body(
            StatusCode::TOO_MANY_REQUESTS,
            "too many attempts, slow down",
        );
    }

    match state
        .reset_password
        .execute(&body.email, &body.code, &body.password)
        .await
    {
        Ok(tokens) => session_response(&state, tokens),
        Err(
            e @ (ResetPasswordError::InvalidCode
            | ResetPasswordError::Superseded
            | ResetPasswordError::Expired),
        ) => {
            record_security_event(
                &state,
                SecurityEvent::new(SecurityEventKind::LoginFailed, None, ip, email_key),
            )
            .await;
            error_body(StatusCode::UNAUTHORIZED, &e.to_string())
        }
        Err(e @ ResetPasswordError::TooManyAttempts) => {
            error_body(StatusCode::TOO_MANY_REQUESTS, &e.to_string())
        }
        Err(e @ ResetPasswordError::PasswordTooShort) => {
            error_body(StatusCode::UNPROCESSABLE_ENTITY, &e.to_string())
        }
        Err(ResetPasswordError::Infrastructure(e)) => {
            tracing::error!(error = %e, "password reset failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Rotates the refresh cookie and returns a fresh access token (ADR-008).
#[utoipa::path(post, path = "/api/auth/refresh", tag = "auth",
    responses(
        (status = 200, description = "Rotated refresh cookie, new access token", body = AccessTokenResponse),
        (status = 401, description = "Missing or invalid refresh cookie", body = ErrorResponse)))]
async fn refresh(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let Some(presented) = read_refresh_cookie(&headers) else {
        return error_body(StatusCode::UNAUTHORIZED, "missing refresh token");
    };
    match state.refresh.rotate(&presented).await {
        Ok(tokens) => session_response(&state, tokens),
        Err(RefreshError::InvalidToken) => (
            [("set-cookie", clear_refresh_cookie())],
            error_body(StatusCode::UNAUTHORIZED, "invalid or expired refresh token"),
        )
            .into_response(),
        Err(RefreshError::Infrastructure(e)) => {
            tracing::error!(error = %e, "refresh failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Revokes the refresh token and clears the cookie. Always succeeds (idempotent).
#[utoipa::path(post, path = "/api/auth/logout", tag = "auth",
    responses((status = 204, description = "Refresh token revoked, cookie cleared")))]
async fn logout(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Some(presented) = read_refresh_cookie(&headers) {
        if let Err(e) = state.refresh.revoke(&presented).await {
            tracing::error!(error = %e, "logout revocation failed");
        }
    }
    (
        StatusCode::NO_CONTENT,
        [("set-cookie", clear_refresh_cookie())],
    )
        .into_response()
}

#[utoipa::path(post, path = "/api/searches", tag = "searches",
    security(("bearer" = [])),
    request_body = CreateSearchRequest,
    responses(
        (status = 202, description = "Scan launched", body = JobCreatedResponse),
        (status = 422, description = "Invalid wallet address", body = ErrorResponse),
        (status = 429, description = "Daily quota exceeded (ADR-017)", body = ErrorResponse)))]
async fn create_search(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    headers: HeaderMap,
    Json(body): Json<CreateSearchRequest>,
) -> Response {
    match state
        .launch
        .execute(user_id, &body.wallet_address, body.mode)
        .await
    {
        Ok(job) => (StatusCode::ACCEPTED, Json(json!({ "job_id": job.id }))).into_response(),
        Err(LaunchError::InvalidJob(e)) => {
            error_body(StatusCode::UNPROCESSABLE_ENTITY, &e.to_string())
        }
        Err(e @ LaunchError::QuotaExceeded(_)) => {
            record_security_event(
                &state,
                SecurityEvent::new(
                    SecurityEventKind::QuotaExceeded,
                    Some(user_id),
                    client_ip(&headers),
                    "daily search quota",
                ),
            )
            .await;
            error_body(StatusCode::TOO_MANY_REQUESTS, &e.to_string())
        }
        Err(LaunchError::DispatchFailed) => {
            error_body(StatusCode::BAD_GATEWAY, "failed to reach the agent")
        }
        Err(LaunchError::Infrastructure(e)) => {
            tracing::error!(error = %e, "launch failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[utoipa::path(get, path = "/api/searches", tag = "searches",
    security(("bearer" = [])),
    responses((status = 200, description = "The user's searches, newest first", body = [JobView])))]
async fn list_searches(State(state): State<AppState>, AuthUser(user_id): AuthUser) -> Response {
    match state.queries.list(user_id).await {
        Ok(jobs) => Json(jobs.iter().map(JobView::from).collect::<Vec<_>>()).into_response(),
        Err(e) => {
            tracing::error!(error = %e, "list searches failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

// ------------------------------------------- per-user KeeperHub key (ADR-076)

#[derive(Deserialize, ToSchema)]
struct ConnectKeeperHubRequest {
    /// The account's own KeeperHub API key. Validated against KeeperHub before
    /// it is stored, and encrypted at rest.
    api_key: String,
}

/// What the settings panel is told. Deliberately has no field that could ever
/// hold the key: the type itself is the guarantee it cannot be echoed back.
#[derive(Serialize, ToSchema)]
struct KeeperHubKeyView {
    /// The wallet this key executes as — the only wallet Sever can revoke for
    /// on this account (ADR-065).
    wallet_address: Option<String>,
    /// `••••` and the last four characters, so a person can tell which key is
    /// connected without the value being recoverable.
    masked: String,
}

impl From<crate::application::ConnectedKey> for KeeperHubKeyView {
    fn from(key: crate::application::ConnectedKey) -> Self {
        Self {
            wallet_address: key.wallet_address,
            masked: key.masked,
        }
    }
}

/// The response for a deployment with no encryption key: the feature is
/// absent, not broken, so 501 rather than a 4xx blaming the caller.
fn keeperhub_not_enabled() -> Response {
    error_body(
        StatusCode::NOT_IMPLEMENTED,
        "per-user KeeperHub keys are not enabled on this deployment",
    )
}

#[utoipa::path(get, path = "/api/settings/keeperhub-key", tag = "settings",
    security(("bearer" = [])),
    responses(
        (status = 200, description = "The connected key, or null", body = Option<KeeperHubKeyView>),
        (status = 501, description = "Not enabled on this deployment", body = ErrorResponse)))]
async fn get_keeperhub_key(State(state): State<AppState>, AuthUser(user_id): AuthUser) -> Response {
    let Some(keys) = state.keeperhub_keys.clone() else {
        return keeperhub_not_enabled();
    };
    match keys.status(user_id).await {
        Ok(status) => Json(status.map(KeeperHubKeyView::from)).into_response(),
        Err(e) => {
            tracing::error!(error = %e, "reading the KeeperHub key failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[utoipa::path(put, path = "/api/settings/keeperhub-key", tag = "settings",
    security(("bearer" = [])),
    request_body = ConnectKeeperHubRequest,
    responses(
        (status = 200, description = "Key stored", body = KeeperHubKeyView),
        (status = 422, description = "Empty or rejected key", body = ErrorResponse),
        (status = 502, description = "KeeperHub unreachable", body = ErrorResponse),
        (status = 501, description = "Not enabled on this deployment", body = ErrorResponse)))]
async fn put_keeperhub_key(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Json(body): Json<ConnectKeeperHubRequest>,
) -> Response {
    let Some(keys) = state.keeperhub_keys.clone() else {
        return keeperhub_not_enabled();
    };
    match keys.connect(user_id, &body.api_key).await {
        Ok(connected) => Json(KeeperHubKeyView::from(connected)).into_response(),
        Err(KeeperHubKeyError::Empty) => {
            error_body(StatusCode::UNPROCESSABLE_ENTITY, "the API key is empty")
        }
        Err(KeeperHubKeyError::Rejected) => error_body(
            StatusCode::UNPROCESSABLE_ENTITY,
            "KeeperHub does not recognise this API key",
        ),
        // 502, not 422: nothing is wrong with what the user typed, so the
        // message must not send them off to re-check a key that may be fine.
        Err(KeeperHubKeyError::DirectoryUnreachable) => error_body(
            StatusCode::BAD_GATEWAY,
            "KeeperHub could not be reached to verify the key - try again shortly",
        ),
        Err(e) => {
            // Logged without the key: `KeeperHubKeyError` carries none, and
            // nothing here interpolates the request body.
            tracing::error!(error = %e, "storing the KeeperHub key failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[utoipa::path(delete, path = "/api/settings/keeperhub-key", tag = "settings",
    security(("bearer" = [])),
    responses(
        (status = 204, description = "Key removed, or there was none"),
        (status = 501, description = "Not enabled on this deployment", body = ErrorResponse)))]
async fn delete_keeperhub_key(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
) -> Response {
    let Some(keys) = state.keeperhub_keys.clone() else {
        return keeperhub_not_enabled();
    };
    match keys.disconnect(user_id).await {
        // 204 whether or not there was a key: the caller asked for the account
        // to end up with none, and it does.
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!(error = %e, "removing the KeeperHub key failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

// ---------------------------------------------------------------- recurring searches (ADR-033)

#[utoipa::path(post, path = "/api/recurring", tag = "recurring",
    security(("bearer" = [])),
    request_body = CreateRecurringRequest,
    responses(
        (status = 201, description = "Recurring scan created", body = RecurringView),
        (status = 422, description = "Invalid wallet address or interval", body = ErrorResponse),
        (status = 429, description = "Too many recurring scans", body = ErrorResponse)))]
async fn create_recurring(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Json(body): Json<CreateRecurringRequest>,
) -> Response {
    match state
        .recurring
        .create(
            user_id,
            &body.wallet_address,
            body.mode,
            body.interval_minutes,
            body.webhook_url.as_deref(),
        )
        .await
    {
        Ok(search) => (StatusCode::CREATED, Json(recurring_search_json(&search))).into_response(),
        Err(e @ RecurringError::Invalid(_)) => {
            error_body(StatusCode::UNPROCESSABLE_ENTITY, &e.to_string())
        }
        Err(e @ RecurringError::TooMany(_)) => {
            error_body(StatusCode::TOO_MANY_REQUESTS, &e.to_string())
        }
        Err(RecurringError::NotFound) => error_body(StatusCode::NOT_FOUND, "not found"),
        Err(RecurringError::Infrastructure(e)) => {
            tracing::error!(error = %e, "create recurring search failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[utoipa::path(get, path = "/api/recurring", tag = "recurring",
    security(("bearer" = [])),
    responses((status = 200, description = "The user's recurring searches", body = [RecurringView])))]
async fn list_recurring(State(state): State<AppState>, AuthUser(user_id): AuthUser) -> Response {
    match state.recurring.list(user_id).await {
        Ok(searches) => Json(
            searches
                .iter()
                .map(recurring_search_json)
                .collect::<Vec<_>>(),
        )
        .into_response(),
        Err(e) => {
            tracing::error!(error = %e, "list recurring searches failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[utoipa::path(delete, path = "/api/recurring/{id}", tag = "recurring",
    security(("bearer" = [])),
    params(("id" = Uuid, Path, description = "Recurring search id")),
    responses(
        (status = 204, description = "Deleted"),
        (status = 404, description = "Not found", body = ErrorResponse)))]
async fn delete_recurring(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Path(id): Path<Uuid>,
) -> Response {
    match state.recurring.delete(user_id, id).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(RecurringError::NotFound) => {
            error_body(StatusCode::NOT_FOUND, "recurring search not found")
        }
        Err(e) => {
            tracing::error!(error = %e, "delete recurring search failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// The user answers the agent's clarification question (ADR-032).
#[utoipa::path(post, path = "/api/searches/{id}/answer", tag = "searches",
    security(("bearer" = [])),
    params(("id" = Uuid, Path, description = "Job id")),
    request_body = AnswerRequest,
    responses(
        (status = 204, description = "Answer stored; the job resumes (ADR-032)"),
        (status = 404, description = "Not found", body = ErrorResponse),
        (status = 409, description = "Job is not awaiting input", body = ErrorResponse)))]
async fn answer_search(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Path(job_id): Path<Uuid>,
    Json(body): Json<AnswerRequest>,
) -> Response {
    match state.answer.execute(user_id, job_id, &body.answer).await {
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(AnswerError::NotFound) => error_body(StatusCode::NOT_FOUND, "search not found"),
        Err(AnswerError::InvalidAnswer(crate::domain::job::JobError::NotAwaitingInput)) => {
            error_body(StatusCode::CONFLICT, "search is not awaiting an answer")
        }
        Err(AnswerError::InvalidAnswer(e)) => {
            error_body(StatusCode::UNPROCESSABLE_ENTITY, &e.to_string())
        }
        Err(AnswerError::DispatchFailed) => {
            error_body(StatusCode::BAD_GATEWAY, "failed to reach the agent")
        }
        Err(AnswerError::Infrastructure(e)) => {
            tracing::error!(error = %e, "answer clarification failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Full job detail: the job fields, its results, and the agent journal (ADR-030,
/// empty in workflow mode). `#[serde(flatten)]` keeps the job fields at the top
/// level, so the wire shape is identical to serving `JobView` plus the two
/// arrays — pinned by the contract fixture (ADR-049).
#[derive(Serialize, ToSchema)]
struct JobDetailView {
    #[serde(flatten)]
    job: JobView,
    results: Vec<ApprovalFinding>,
    steps: Vec<AgentStep>,
}

/// The job detail payload, shared by `GET /api/searches/{id}` and the SSE
/// stream (ADR-026) so both surfaces always carry the same shape. Public so the
/// cross-language contract test (ADR-049) can pin its exact wire shape.
pub fn job_detail_json(
    job: &ScanJob,
    results: &[ApprovalFinding],
    steps: &[AgentStep],
) -> serde_json::Value {
    serde_json::to_value(JobDetailView {
        job: JobView::from(job),
        results: results.to_vec(),
        steps: steps.to_vec(),
    })
    .expect("serializable job detail")
}

/// The recurring-search payload served by `POST`/`GET /api/recurring`. Single
/// serialization path (used by the handlers below) so its shape is pinned once
/// by the contract test (ADR-049).
pub fn recurring_search_json(search: &RecurringSearch) -> serde_json::Value {
    serde_json::to_value(RecurringView::from(search)).expect("serializable recurring view")
}

/// The connected-key payload served by the `/api/settings/keeperhub-key`
/// routes (ADR-076), pinned by the contract test (ADR-049) so a field that
/// could carry the key cannot be added without the test noticing.
pub fn keeperhub_key_json(key: crate::application::ConnectedKey) -> serde_json::Value {
    serde_json::to_value(KeeperHubKeyView::from(key)).expect("serializable keeperhub key view")
}

#[utoipa::path(get, path = "/api/searches/{id}", tag = "searches",
    security(("bearer" = [])),
    params(("id" = Uuid, Path, description = "Job id")),
    responses(
        (status = 200, description = "Job detail: fields, results and the agent journal", body = JobDetailView),
        (status = 404, description = "Not found", body = ErrorResponse)))]
async fn get_search(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Path(job_id): Path<Uuid>,
) -> Response {
    match state.queries.get(user_id, job_id).await {
        Ok(Some((job, results, steps))) => {
            Json(job_detail_json(&job, &results, &steps)).into_response()
        }
        Ok(None) => error_body(StatusCode::NOT_FOUND, "search not found"),
        Err(e) => {
            tracing::error!(error = %e, "get search failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// SSE stream of job updates (ADR-026): an `update` event per change, closed
/// after the terminal status. The client keeps polling as a fallback.
async fn search_events(
    State(state): State<AppState>,
    AuthUser(user_id): AuthUser,
    Path(job_id): Path<Uuid>,
) -> Response {
    // Reject unknown/foreign jobs with a proper 404 before streaming.
    match state.queries.get(user_id, job_id).await {
        Ok(Some(_)) => {}
        Ok(None) => return error_body(StatusCode::NOT_FOUND, "search not found"),
        Err(e) => {
            tracing::error!(error = %e, "search events failed");
            return error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error");
        }
    }
    let stream = sse::job_updates(state.queries.clone(), user_id, job_id);
    axum::response::sse::Sse::new(stream)
        .keep_alive(axum::response::sse::KeepAlive::default())
        .into_response()
}

// ---------------------------------------------------------------- internal handlers (worker -> backend, ADR-006)

async fn internal_started(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.start(job_id).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "mark job started failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

async fn internal_results(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
    Json(body): Json<ResultsRequest>,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.complete(job_id, &body.results).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "ingest results failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Records one agent-loop decision for the live journal (ADR-030).
async fn internal_step(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
    Json(step): Json<AgentStep>,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.record_step(job_id, &step).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "record agent step failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// The agent asked the user a clarification question (ADR-032).
async fn internal_question(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
    Json(body): Json<QuestionRequest>,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.request_input(job_id, &body.question).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "record clarification question failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

/// Accumulates one task attempt's API spend (ADR-038).
async fn internal_usage(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
    Json(usage): Json<JobUsage>,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.record_usage(job_id, &usage).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "record usage failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

async fn internal_failure(
    State(state): State<AppState>,
    Path(job_id): Path<Uuid>,
    headers: HeaderMap,
    Json(body): Json<FailureRequest>,
) -> Response {
    if let Some(rejection) = check_internal_token(&state, &headers) {
        return rejection;
    }
    match state.ingest.fail(job_id, body.error).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(IngestError::JobNotFound) => error_body(StatusCode::NOT_FOUND, "job not found"),
        Err(IngestError::Infrastructure(e)) => {
            tracing::error!(error = %e, "ingest failure failed");
            error_body(StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::constant_time_eq;

    #[test]
    fn constant_time_eq_matches_only_identical_bytes() {
        assert!(constant_time_eq(b"same-token", b"same-token"));
        assert!(!constant_time_eq(b"same-token", b"diff-token"));
        assert!(!constant_time_eq(b"short", b"longer-token")); // length differs
        assert!(!constant_time_eq(b"", b"x"));
        assert!(constant_time_eq(b"", b""));
    }
}
