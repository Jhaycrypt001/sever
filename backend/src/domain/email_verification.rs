//! Email verification codes (ADR-062).
//!
//! Registration proves someone knows a password. It proves nothing about the
//! address they typed — which is the address every digest, and every future
//! account-recovery path, is sent to. A short numeric code delivered to that
//! inbox is the cheapest proof that the two belong to the same person.
//!
//! Like refresh tokens (ADR-008) the plaintext never reaches the database: the
//! row holds a SHA-256 hash, so a leaked table hands an attacker nothing that
//! can be replayed. Unlike a refresh token, the secret is only six digits, so
//! entropy cannot carry the security on its own. Three things do instead:
//!
//! - a short TTL (10 minutes by default),
//! - a hard cap on attempts per code, after which the code is dead and a new
//!   one must be requested,
//! - the existing per-IP throttle on `/api/auth/*`.
//!
//! Six digits with five attempts is a 1-in-200,000 chance per issued code; the
//! attacker's only way to improve it is to request more codes, which the same
//! throttle bounds.

use chrono::{DateTime, Duration, Utc};
use rand_core::{OsRng, RngCore};
use sha2::{Digest, Sha256};
use uuid::Uuid;

/// How many wrong guesses a single code tolerates before it is burned.
pub const MAX_ATTEMPTS: i32 = 5;

/// Number of decimal digits in a code. Six is the length people expect from an
/// emailed code and can retype without copy-paste; the attempt cap, not the
/// length, is what makes guessing hopeless.
const CODE_DIGITS: u32 = 6;

/// What a code entitles its holder to do (ADR-063).
///
/// The two are never interchangeable. A sign-in code must not set a new
/// password, and a reset code must not open a session on its own — so every
/// lookup filters on the purpose rather than trusting the caller to check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodePurpose {
    /// Confirms the address, and is the second factor of every sign-in.
    Verify,
    /// Authorises setting a new password.
    Reset,
}

impl CodePurpose {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Verify => "verify",
            Self::Reset => "reset",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "verify" => Some(Self::Verify),
            "reset" => Some(Self::Reset),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct EmailVerification {
    pub id: Uuid,
    pub user_id: Uuid,
    pub purpose: CodePurpose,
    pub code_hash: String,
    pub expires_at: DateTime<Utc>,
    /// Wrong guesses so far. At `MAX_ATTEMPTS` the code stops being usable
    /// even while it is otherwise unexpired.
    pub attempts: i32,
    /// Set the moment a correct code is accepted, making it single-use rather
    /// than replayable for the rest of its TTL.
    pub consumed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
}

impl EmailVerification {
    /// Issues a code: the record to persist, and the plaintext to mail (never
    /// stored).
    pub fn issue(user_id: Uuid, purpose: CodePurpose, ttl_minutes: i64) -> (Self, String) {
        let plaintext = generate_code();
        let now = super::now_utc();
        let record = Self {
            id: Uuid::new_v4(),
            user_id,
            purpose,
            code_hash: Self::hash(&plaintext),
            expires_at: now + Duration::minutes(ttl_minutes),
            attempts: 0,
            consumed_at: None,
            created_at: now,
        };
        (record, plaintext)
    }

    pub fn hash(plaintext: &str) -> String {
        Sha256::digest(plaintext.trim().as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    pub fn is_expired(&self, now: DateTime<Utc>) -> bool {
        self.expires_at <= now
    }

    pub fn is_consumed(&self) -> bool {
        self.consumed_at.is_some()
    }

    pub fn is_exhausted(&self) -> bool {
        self.attempts >= MAX_ATTEMPTS
    }

    /// True only when this code can still be presented at `now`.
    pub fn is_usable(&self, now: DateTime<Utc>) -> bool {
        !self.is_consumed() && !self.is_expired(now) && !self.is_exhausted()
    }

    pub fn matches(&self, presented: &str) -> bool {
        self.code_hash == Self::hash(presented)
    }
}

/// A uniformly distributed `CODE_DIGITS`-digit code, zero-padded.
///
/// Rejection sampling rather than a plain modulo: `u32::MAX` is not a multiple
/// of 1,000,000, so `next_u32() % 1_000_000` would make the low codes very
/// slightly likelier. The bias is tiny and arguably harmless here, but a
/// biased secret generator is the kind of thing that gets copied into a place
/// where it does matter.
fn generate_code() -> String {
    let modulus = 10u32.pow(CODE_DIGITS);
    let limit = u32::MAX - (u32::MAX % modulus) - (modulus - 1);
    let value = loop {
        let candidate = OsRng.next_u32();
        if candidate <= limit {
            break candidate % modulus;
        }
    };
    format!("{value:0width$}", width = CODE_DIGITS as usize)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn issue_returns_a_hashed_record_and_the_plaintext() {
        let user_id = Uuid::new_v4();
        let (record, code) = EmailVerification::issue(user_id, CodePurpose::Verify, 10);

        assert_eq!(record.user_id, user_id);
        assert_ne!(record.code_hash, code, "plaintext must not be stored");
        assert_eq!(record.code_hash, EmailVerification::hash(&code));
        assert!(record.is_usable(Utc::now()));
    }

    #[test]
    fn codes_are_six_digits_and_vary() {
        let mut seen = std::collections::HashSet::new();
        for _ in 0..200 {
            let (_, code) = EmailVerification::issue(Uuid::new_v4(), CodePurpose::Verify, 10);
            assert_eq!(code.len(), 6, "{code} is not six characters");
            assert!(code.chars().all(|c| c.is_ascii_digit()), "{code}");
            seen.insert(code);
        }
        // A generator stuck on one value would be catastrophic and silent.
        assert!(
            seen.len() > 150,
            "codes are not varying: {} unique",
            seen.len()
        );
    }

    #[test]
    fn a_purpose_round_trips_through_its_stored_form() {
        // The DB column holds these strings; an unknown one must not silently
        // decay into a valid purpose.
        for purpose in [CodePurpose::Verify, CodePurpose::Reset] {
            assert_eq!(CodePurpose::parse(purpose.as_str()), Some(purpose));
        }
        assert_eq!(CodePurpose::parse("admin"), None);
    }

    #[test]
    fn a_code_matches_itself_and_nothing_else() {
        let (record, code) = EmailVerification::issue(Uuid::new_v4(), CodePurpose::Verify, 10);
        assert!(record.matches(&code));
        assert!(!record.matches("000000"), "unless it really was 000000");
        // Retyped from an email client, a code often arrives padded.
        assert!(record.matches(&format!("  {code} ")));
    }

    #[test]
    fn expiry_is_relative_to_the_ttl() {
        let (record, _) = EmailVerification::issue(Uuid::new_v4(), CodePurpose::Verify, 10);
        assert!(!record.is_expired(Utc::now() + Duration::minutes(9)));
        assert!(record.is_expired(Utc::now() + Duration::minutes(11)));
        assert!(!record.is_usable(Utc::now() + Duration::minutes(11)));
    }

    #[test]
    fn a_code_dies_after_the_attempt_cap() {
        let (mut record, code) = EmailVerification::issue(Uuid::new_v4(), CodePurpose::Verify, 10);
        record.attempts = MAX_ATTEMPTS;

        // The right code no longer helps: brute force must not be rewarded by
        // eventually stumbling onto the answer.
        assert!(record.is_exhausted());
        assert!(!record.is_usable(Utc::now()));
        assert!(record.matches(&code), "matching is separate from usability");
    }

    #[test]
    fn a_consumed_code_is_not_usable_again() {
        let (mut record, _) = EmailVerification::issue(Uuid::new_v4(), CodePurpose::Verify, 10);
        record.consumed_at = Some(Utc::now());
        assert!(!record.is_usable(Utc::now()));
    }
}
