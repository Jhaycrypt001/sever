//! Passkey (WebAuthn) credentials — ADR-072.
//!
//! The domain deliberately knows almost nothing about WebAuthn. Everything
//! cryptographic — challenge generation, signature verification, the signature
//! counter — belongs to the `webauthn_rp` library and to the adapter that wraps
//! it. What lives here is the part the rest of the application reasons about: a
//! credential belongs to a user, it has a name a person chose, and it was last
//! used at some point.
//!
//! `credential` is an opaque blob for exactly that reason. Parsing it here
//! would pull a library type into the domain and pin the schema to its version,
//! which is the coupling ADR-002 exists to avoid.

use chrono::{DateTime, Utc};
use serde_json::Value;
use uuid::Uuid;

/// A registered authenticator: a phone, a laptop's fingerprint reader, a
/// hardware key.
#[derive(Debug, Clone, PartialEq)]
pub struct PasskeyCredential {
    pub id: Uuid,
    pub user_id: Uuid,
    /// Base64url credential ID, as the browser reports it.
    pub credential_id: String,
    /// The serialised library credential. Opaque to the domain.
    pub credential: Value,
    /// What the person calls this device.
    pub label: String,
    pub created_at: DateTime<Utc>,
    pub last_used_at: Option<DateTime<Utc>>,
}

/// How long a half-finished ceremony stays valid.
///
/// WebAuthn is two round trips and the gap between them is a human one —
/// unlocking a phone, reaching for a security key. Long enough not to punish
/// someone who fumbles; short enough that an abandoned challenge is not left
/// lying around to be replayed.
pub const CEREMONY_TTL_SECONDS: i64 = 300;

/// Whether an in-flight ceremony is registering a new key or signing in.
///
/// Kept apart so a challenge issued for one can never be completed as the
/// other: a registration ceremony finished as an authentication would be a
/// sign-in nobody proved anything for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CeremonyPurpose {
    Register,
    Authenticate,
}

impl CeremonyPurpose {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Register => "register",
            Self::Authenticate => "authenticate",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "register" => Some(Self::Register),
            "authenticate" => Some(Self::Authenticate),
            _ => None,
        }
    }
}

/// Server-held state for a ceremony awaiting the browser's answer.
#[derive(Debug, Clone)]
pub struct WebauthnCeremony {
    pub id: Uuid,
    /// None for a login: a discoverable credential names the account only once
    /// the browser has answered.
    pub user_id: Option<Uuid>,
    pub purpose: CeremonyPurpose,
    pub state: Value,
    pub expires_at: DateTime<Utc>,
}

impl WebauthnCeremony {
    pub fn new(
        user_id: Option<Uuid>,
        purpose: CeremonyPurpose,
        state: Value,
        now: DateTime<Utc>,
    ) -> Self {
        Self {
            id: Uuid::new_v4(),
            user_id,
            purpose,
            state,
            expires_at: now + chrono::Duration::seconds(CEREMONY_TTL_SECONDS),
        }
    }

    pub fn is_expired(&self, now: DateTime<Utc>) -> bool {
        now >= self.expires_at
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_purpose_round_trips_through_its_stored_form() {
        for purpose in [CeremonyPurpose::Register, CeremonyPurpose::Authenticate] {
            assert_eq!(CeremonyPurpose::parse(purpose.as_str()), Some(purpose));
        }
        // The column has a CHECK constraint with exactly these two values;
        // anything else arriving from the database is a bug, not a variant.
        assert_eq!(CeremonyPurpose::parse("sign-in"), None);
    }

    #[test]
    fn a_ceremony_expires_after_its_ttl_and_not_before() {
        let now = Utc::now();
        let ceremony = WebauthnCeremony::new(None, CeremonyPurpose::Authenticate, Value::Null, now);

        assert!(!ceremony.is_expired(now));
        assert!(!ceremony.is_expired(now + chrono::Duration::seconds(CEREMONY_TTL_SECONDS - 1)));
        // Exactly at the boundary counts as expired: a challenge that is still
        // answerable "at" its deadline has a deadline that means nothing.
        assert!(ceremony.is_expired(now + chrono::Duration::seconds(CEREMONY_TTL_SECONDS)));
    }

    #[test]
    fn a_login_ceremony_belongs_to_nobody_until_it_is_answered() {
        // The account is discovered from the credential the browser returns.
        // Binding a login ceremony to a user up front would mean asking for the
        // e-mail first, which is what leaks who has an account.
        let ceremony =
            WebauthnCeremony::new(None, CeremonyPurpose::Authenticate, Value::Null, Utc::now());
        assert!(ceremony.user_id.is_none());
    }
}
