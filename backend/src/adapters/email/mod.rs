//! Outbound email adapters (ADR-062).
//!
//! Two implementations of `EmailSender`:
//!
//! - [`ResendEmailSender`] posts to Resend's HTTP API. Chosen over SMTP purely
//!   to avoid a new dependency — `reqwest` is already here for the agent
//!   dispatcher and the digest webhook, whereas an SMTP client would pull in a
//!   TLS stack and a MIME builder for one six-digit message. Another provider,
//!   or `lettre` over SMTP, is one more implementation of the same port.
//! - [`DevEmailSender`] sends nothing. It logs the code and keeps it in memory
//!   so development and the test suite do not need a mailbox. It must never be
//!   reachable in production — `AppConfig` refuses to start there without a
//!   real provider configured, the same rule ADR-059 applies to simulated
//!   revocations.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use crate::domain::ports::{EmailSender, PortError};
use crate::domain::CodePurpose;

/// Product name as it appears in the From header and the message body.
const PRODUCT: &str = "Approval Firewall";

/// What the code is called, and what to say to someone who did not ask for it.
///
/// The warning line matters more for a reset: an unexpected sign-in code means
/// somebody has the password, and an unexpected reset code means somebody is
/// trying to take the account. Both are worth acting on, and neither is
/// conveyed by a generic "ignore this message".
fn wording(purpose: CodePurpose) -> (&'static str, &'static str) {
    match purpose {
        CodePurpose::Verify => (
            "sign-in code",
            "If you did not try to sign in, someone may have your password — \
             change it, and do not enter this code.",
        ),
        CodePurpose::Reset => (
            "password reset code",
            "If you did not ask to reset your password, ignore this message. \
             Your password has not changed and this code alone cannot change it.",
        ),
    }
}

fn subject(code: &str, purpose: CodePurpose) -> String {
    // The code goes in the subject as well as the body: most people can read
    // it off the notification without opening anything.
    let (label, _) = wording(purpose);
    format!("{code} is your {PRODUCT} {label}")
}

fn text_body(code: &str, ttl_minutes: i64, purpose: CodePurpose) -> String {
    let (label, warning) = wording(purpose);
    format!(
        "Your {PRODUCT} {label} is:\n\n    {code}\n\n\
         It expires in {ttl_minutes} minutes and can be used once.\n\n\
         {warning}\n"
    )
}

fn html_body(code: &str, ttl_minutes: i64, purpose: CodePurpose) -> String {
    let (label, warning) = wording(purpose);
    // Deliberately plain: inlined styles only, no images, no tracking pixel,
    // no external stylesheet. Anything else is what spam filters look for.
    format!(
        "<div style=\"font-family:ui-sans-serif,system-ui,sans-serif;\
         background:#0a0a0a;color:#fafafa;padding:40px 24px\">\
         <p style=\"font-size:11px;letter-spacing:.12em;text-transform:uppercase;\
         color:#8a8a8a;margin:0 0 24px\">{PRODUCT}</p>\
         <p style=\"margin:0 0 16px\">Your {label} is:</p>\
         <p style=\"font-family:ui-monospace,monospace;font-size:34px;\
         letter-spacing:.35em;margin:0 0 24px\">{code}</p>\
         <p style=\"color:#8a8a8a;font-size:13px;margin:0 0 8px\">\
         It expires in {ttl_minutes} minutes and can be used once.</p>\
         <p style=\"color:#8a8a8a;font-size:13px;margin:0\">{warning}</p></div>"
    )
}

// ---------------------------------------------------------------- Resend

pub struct ResendEmailSender {
    client: reqwest::Client,
    api_key: String,
    /// The verified sender, e.g. `Approval Firewall <no-reply@example.com>`.
    from: String,
}

impl ResendEmailSender {
    pub fn new(api_key: String, from: String) -> Self {
        Self {
            client: reqwest::Client::new(),
            api_key,
            from,
        }
    }
}

#[async_trait]
impl EmailSender for ResendEmailSender {
    async fn send_code(
        &self,
        to: &str,
        code: &str,
        ttl_minutes: i64,
        purpose: CodePurpose,
    ) -> Result<(), PortError> {
        let response = self
            .client
            .post("https://api.resend.com/emails")
            .bearer_auth(&self.api_key)
            .json(&serde_json::json!({
                "from": self.from,
                "to": [to],
                "subject": subject(code, purpose),
                "text": text_body(code, ttl_minutes, purpose),
                "html": html_body(code, ttl_minutes, purpose),
            }))
            .send()
            .await
            .map_err(|e| PortError(format!("email provider unreachable: {e}")))?;

        let status = response.status();
        if status.is_success() {
            return Ok(());
        }
        // The provider's message is the only way to tell "unverified sending
        // domain" from "rate limited", and both are operator problems. The
        // code is never in the response, so this is safe to log in full.
        let detail = response.text().await.unwrap_or_default();
        Err(PortError(format!(
            "email provider rejected the message ({status}): {detail}"
        )))
    }
}

// ---------------------------------------------------------------- development

/// Sends nothing; records what it would have sent.
///
/// Used when no email provider is configured, which is every developer machine
/// and the whole test suite. The code is logged at WARN so it is impossible to
/// mistake a machine that is not really sending mail for one that is.
#[derive(Default)]
pub struct DevEmailSender {
    sent: Mutex<HashMap<String, Vec<String>>>,
}

impl DevEmailSender {
    /// Every code sent to `address`, oldest first.
    pub fn codes_for(&self, address: &str) -> Vec<String> {
        self.sent
            .lock()
            .unwrap()
            .get(&address.to_lowercase())
            .cloned()
            .unwrap_or_default()
    }
}

#[async_trait]
impl EmailSender for DevEmailSender {
    async fn send_code(
        &self,
        to: &str,
        code: &str,
        ttl_minutes: i64,
        purpose: CodePurpose,
    ) -> Result<(), PortError> {
        tracing::warn!(
            recipient = %to,
            code = %code,
            ttl_minutes,
            purpose = purpose.as_str(),
            "no email provider configured — code logged instead of sent"
        );
        self.sent
            .lock()
            .unwrap()
            .entry(to.to_lowercase())
            .or_default()
            .push(code.to_string());
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn the_dev_sender_records_every_code_it_was_asked_to_send() {
        let sender = DevEmailSender::default();
        sender
            .send_code("Alice@Example.com", "123456", 10, CodePurpose::Verify)
            .await
            .unwrap();
        sender
            .send_code("alice@example.com", "654321", 10, CodePurpose::Reset)
            .await
            .unwrap();

        // Addresses are matched case-insensitively, as they are everywhere else.
        assert_eq!(
            sender.codes_for("alice@example.com"),
            vec!["123456".to_string(), "654321".to_string()]
        );
        assert!(sender.codes_for("bob@example.com").is_empty());
    }

    #[test]
    fn the_message_carries_the_code_and_its_lifetime() {
        for purpose in [CodePurpose::Verify, CodePurpose::Reset] {
            let text = text_body("424242", 10, purpose);
            assert!(text.contains("424242"));
            assert!(text.contains("10 minutes"));
            assert!(subject("424242", purpose).starts_with("424242"));

            // No remote asset may appear in the HTML: a mail client that blocks
            // images would otherwise hide the code itself.
            let html = html_body("424242", 10, purpose);
            assert!(html.contains("424242"));
            assert!(!html.contains("<img"));
            assert!(!html.contains("http://"));
            assert!(!html.contains("https://"));
        }
    }

    #[test]
    fn a_reset_and_a_sign_in_code_do_not_read_the_same() {
        // Someone receiving one they did not ask for should be told which
        // thing just happened; the two warnings call for different action.
        let sign_in = text_body("111111", 10, CodePurpose::Verify);
        let reset = text_body("111111", 10, CodePurpose::Reset);
        assert_ne!(sign_in, reset);
        assert!(sign_in.contains("someone may have your password"));
        assert!(reset.contains("Your password has not changed"));
    }
}
