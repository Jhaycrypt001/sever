//! Outbound adapters implementing `JobDispatcher` (ADR-005).

use async_trait::async_trait;
use serde::Serialize;

use crate::domain::ports::{DispatchContext, JobDispatcher, PortError};
use crate::domain::ScanJob;

/// Dispatches jobs to the FastAPI micro-API, which enqueues them via Celery.
pub struct HttpJobDispatcher {
    client: reqwest::Client,
    base_url: String,
    internal_token: String,
}

#[derive(Serialize)]
struct TaskRequest<'a> {
    job_id: uuid::Uuid,
    wallet_address: &'a str,
    /// The user's answer to the agent's clarification question (ADR-032);
    /// null on the first dispatch.
    clarification: Option<&'a str>,
    mode: crate::domain::JobMode,
    /// Recurring-scan run (ADR-033): when true the agent flags the delta
    /// against `seen_approval_keys` (empty on the first run) and journals a
    /// report.
    recurring: bool,
    /// Approval keys (chain:token:spender) delivered by previous runs of the
    /// recurring scan.
    seen_approval_keys: &'a [String],
    /// The account's own KeeperHub key (ADR-076), so the worker executes as
    /// *that user's* delegated wallet. `None` when the account has not
    /// connected one, and the worker falls back to its environment key.
    ///
    /// This is the one place a plaintext key crosses a process boundary. The
    /// hop is backend -> agent API over the private network (ADR-006), the same
    /// channel already carrying `X-Internal-Token`.
    #[serde(skip_serializing_if = "Option::is_none")]
    keeperhub_api_key: Option<&'a str>,
}

impl HttpJobDispatcher {
    pub fn new(base_url: String, internal_token: String) -> Self {
        Self {
            client: reqwest::Client::new(),
            base_url: base_url.trim_end_matches('/').to_string(),
            internal_token,
        }
    }
}

#[async_trait]
impl JobDispatcher for HttpJobDispatcher {
    async fn dispatch(&self, job: &ScanJob, context: DispatchContext<'_>) -> Result<(), PortError> {
        // Distributed tracing (ADR-029): carry the W3C trace context so the
        // agent joins the same trace. Empty when telemetry is disabled.
        let mut trace_headers = reqwest::header::HeaderMap::new();
        crate::telemetry::inject_trace_context(&mut trace_headers);
        let response = self
            .client
            .post(format!("{}/tasks", self.base_url))
            .headers(trace_headers)
            .header("X-Internal-Token", &self.internal_token)
            // Correlation (ADR-018): the job id follows the work through
            // FastAPI, Celery, and the worker's callbacks.
            .header("X-Request-Id", job.id.to_string())
            .json(&TaskRequest {
                job_id: job.id,
                wallet_address: &job.wallet_address,
                clarification: job.answer.as_deref(),
                mode: job.mode,
                recurring: job.recurring_search_id.is_some(),
                seen_approval_keys: context.seen_approval_keys,
                keeperhub_api_key: context.keeperhub_api_key,
            })
            .send()
            .await
            .map_err(|e| PortError(format!("agent API unreachable: {e}")))?;

        if !response.status().is_success() {
            return Err(PortError(format!(
                "agent API returned {}",
                response.status()
            )));
        }
        Ok(())
    }
}

/// No-op dispatcher for local development without the agent stack
/// (`AGENT_API_URL` unset) and for tests. Jobs stay `pending` forever.
#[derive(Default)]
pub struct NoopJobDispatcher;

#[async_trait]
impl JobDispatcher for NoopJobDispatcher {
    async fn dispatch(
        &self,
        job: &ScanJob,
        _context: DispatchContext<'_>,
    ) -> Result<(), PortError> {
        tracing::warn!(job_id = %job.id, "NoopJobDispatcher: job not sent to any agent");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::extract::State;
    use axum::http::HeaderMap;
    use axum::routing::post;
    use axum::Router;
    use std::sync::{Arc, Mutex};
    use uuid::Uuid;

    const ADDR: &str = "0x1234567890123456789012345678901234567890";

    type SeenHeaders = Arc<Mutex<Vec<(Option<String>, Option<String>, Option<String>)>>>;

    /// Spawns a stub agent API capturing the auth, correlation and trace headers.
    async fn spawn_stub() -> (String, SeenHeaders) {
        let seen: SeenHeaders = Arc::new(Mutex::new(vec![]));
        let app = Router::new()
            .route(
                "/tasks",
                post(
                    |State(seen): State<SeenHeaders>, headers: HeaderMap| async move {
                        let get = |name: &str| {
                            headers
                                .get(name)
                                .and_then(|v| v.to_str().ok())
                                .map(str::to_string)
                        };
                        seen.lock().unwrap().push((
                            get("x-internal-token"),
                            get("x-request-id"),
                            get("traceparent"),
                        ));
                        "queued"
                    },
                ),
            )
            .with_state(seen.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (format!("http://{addr}"), seen)
    }

    #[tokio::test]
    async fn dispatch_sends_internal_token_and_correlation_id() {
        let (base_url, seen) = spawn_stub().await;
        let dispatcher = HttpJobDispatcher::new(base_url, "secret".into());
        let job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();

        dispatcher
            .dispatch(&job, DispatchContext::default())
            .await
            .unwrap();

        let calls = seen.lock().unwrap();
        // No telemetry configured (ADR-029): no traceparent leaks out.
        assert_eq!(
            calls.as_slice(),
            &[(Some("secret".into()), Some(job.id.to_string()), None)]
        );
    }

    #[tokio::test]
    async fn the_owners_keeperhub_key_travels_with_the_job() {
        // ADR-076: without this the worker executes as its environment wallet,
        // and the user's own delegated wallet can never be revoked for.
        let (base_url, bodies) = spawn_body_stub().await;
        let dispatcher = HttpJobDispatcher::new(base_url, "secret".into());
        let job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();

        dispatcher
            .dispatch(
                &job,
                DispatchContext {
                    seen_approval_keys: &[],
                    keeperhub_api_key: Some("kh_owner"),
                },
            )
            .await
            .unwrap();

        assert_eq!(
            bodies.lock().unwrap()[0]["keeperhub_api_key"],
            serde_json::json!("kh_owner")
        );
    }

    #[tokio::test]
    async fn an_account_without_a_key_sends_no_key_field() {
        // Absent rather than null: the agent's request model treats a missing
        // field as "use the environment key", which is what pre-ADR-076
        // deployments and unconnected accounts both need.
        let (base_url, bodies) = spawn_body_stub().await;
        let dispatcher = HttpJobDispatcher::new(base_url, "secret".into());
        let job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();

        dispatcher
            .dispatch(&job, DispatchContext::default())
            .await
            .unwrap();

        assert!(bodies.lock().unwrap()[0].get("keeperhub_api_key").is_none());
    }

    /// Stub agent API capturing the JSON body of each dispatch.
    async fn spawn_body_stub() -> (String, Arc<Mutex<Vec<serde_json::Value>>>) {
        let bodies: Arc<Mutex<Vec<serde_json::Value>>> = Arc::new(Mutex::new(vec![]));
        let captured = bodies.clone();
        let app = Router::new().route(
            "/tasks",
            post(move |axum::Json(body): axum::Json<serde_json::Value>| {
                let captured = captured.clone();
                async move {
                    captured.lock().unwrap().push(body);
                    "queued"
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (format!("http://{addr}"), bodies)
    }

    #[tokio::test]
    async fn dispatch_propagates_the_trace_context_when_telemetry_is_active() {
        use opentelemetry::trace::TracerProvider as _;
        use tracing::instrument::WithSubscriber;
        use tracing::Instrument;
        use tracing_subscriber::layer::SubscriberExt;

        let (base_url, seen) = spawn_stub().await;
        // Same shape as telemetry::layer(), minus the OTLP exporter.
        opentelemetry::global::set_text_map_propagator(
            opentelemetry_sdk::propagation::TraceContextPropagator::new(),
        );
        let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder().build();
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(provider.tracer("test")));

        async {
            let dispatcher = HttpJobDispatcher::new(base_url, "secret".into());
            let job = ScanJob::new(Uuid::new_v4(), ADDR).unwrap();
            let span = tracing::info_span!("http_request");
            dispatcher
                .dispatch(&job, DispatchContext::default())
                .instrument(span)
                .await
                .unwrap();
        }
        .with_subscriber(subscriber)
        .await;

        let calls = seen.lock().unwrap();
        let traceparent = calls[0].2.as_deref().expect("traceparent propagated");
        assert_eq!(traceparent.split('-').count(), 4);
    }
}
