//! Encryption at rest for third-party credentials (ADR-076).
//!
//! A KeeperHub API key is not a password: the system must be able to *use* it,
//! so it cannot be hashed like `users.password_hash` (argon2, one-way). It has
//! to come back out in plaintext to be sent to KeeperHub. That makes it the
//! first value in this system needing reversible encryption.
//!
//! XChaCha20-Poly1305, because:
//!
//! - It is authenticated (AEAD). A ciphertext edited in the database fails to
//!   decrypt rather than decrypting to something else, so a tampered row cannot
//!   redirect a revoke to an attacker's KeeperHub account.
//! - Its 192-bit nonce is large enough to be drawn at random for every
//!   encryption without tracking a counter. AES-GCM's 96-bit nonce is not, and
//!   a repeated nonce there is catastrophic. Removing the chance to get nonce
//!   management wrong is worth more here than hardware acceleration.
//!
//! The key itself lives in `CREDENTIAL_ENCRYPTION_KEY` and never in Postgres,
//! so a database dump on its own decrypts nothing.

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use chacha20poly1305::aead::{Aead, KeyInit, OsRng};
use chacha20poly1305::{AeadCore, Key, XChaCha20Poly1305, XNonce};

/// Bytes of key material required. Anything else is rejected at construction
/// rather than silently padded or truncated.
pub const KEY_BYTES: usize = 32;

const NONCE_BYTES: usize = 24;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum SecretBoxError {
    #[error("the encryption key must be {KEY_BYTES} bytes, base64-encoded")]
    BadKey,
    #[error("the stored secret could not be decrypted")]
    Undecryptable,
}

/// Seals and opens third-party credentials. Cloneable and cheap to share: the
/// cipher holds only the key schedule.
#[derive(Clone)]
pub struct SecretBox {
    cipher: XChaCha20Poly1305,
}

impl SecretBox {
    /// Builds from a base64-encoded 32-byte key, the form the deployment
    /// environment carries it in.
    ///
    /// Generate one with:
    /// `openssl rand -base64 32`
    pub fn from_base64(encoded: &str) -> Result<Self, SecretBoxError> {
        let bytes = BASE64
            .decode(encoded.trim())
            .map_err(|_| SecretBoxError::BadKey)?;
        if bytes.len() != KEY_BYTES {
            return Err(SecretBoxError::BadKey);
        }
        Ok(Self {
            cipher: XChaCha20Poly1305::new(Key::from_slice(&bytes)),
        })
    }

    /// Encrypts, returning `base64(nonce || ciphertext)` — one opaque column
    /// value, so the nonce can never be separated from what it belongs to.
    pub fn seal(&self, plaintext: &str) -> String {
        let nonce = XChaCha20Poly1305::generate_nonce(&mut OsRng);
        // Only fails on arithmetic overflow of the plaintext length, which an
        // API key cannot reach.
        let ciphertext = self
            .cipher
            .encrypt(&nonce, plaintext.as_bytes())
            .expect("XChaCha20-Poly1305 encryption of a short string cannot fail");
        let mut envelope = nonce.to_vec();
        envelope.extend_from_slice(&ciphertext);
        BASE64.encode(envelope)
    }

    /// Reverses [`seal`](Self::seal). Every failure — wrong key, truncated
    /// value, tampered ciphertext, garbage — collapses to one error, because
    /// telling the caller *which* is a decryption oracle.
    pub fn open(&self, envelope: &str) -> Result<String, SecretBoxError> {
        let raw = BASE64
            .decode(envelope)
            .map_err(|_| SecretBoxError::Undecryptable)?;
        if raw.len() <= NONCE_BYTES {
            return Err(SecretBoxError::Undecryptable);
        }
        let (nonce, ciphertext) = raw.split_at(NONCE_BYTES);
        let plaintext = self
            .cipher
            .decrypt(XNonce::from_slice(nonce), ciphertext)
            .map_err(|_| SecretBoxError::Undecryptable)?;
        String::from_utf8(plaintext).map_err(|_| SecretBoxError::Undecryptable)
    }
}

/// What the account screen may show: enough of the key to recognise which one
/// is connected, never enough to use it. The tail rather than the head, because
/// every KeeperHub key starts `kh_` and the prefix identifies nothing.
pub fn mask(api_key: &str) -> String {
    const VISIBLE: usize = 4;
    let key = api_key.trim();
    match key.char_indices().nth_back(VISIBLE.saturating_sub(1)) {
        Some((at, _)) if key.chars().count() > VISIBLE => format!("••••{}", &key[at..]),
        // Too short to mask without revealing most of it. Not a real key, but
        // it must not fall through to printing the value.
        _ => "••••".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 32 bytes, base64. Test-only; the real one comes from the environment.
    const KEY: &str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=";
    const OTHER_KEY: &str = "ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA=";

    fn secret_box() -> SecretBox {
        SecretBox::from_base64(KEY).unwrap()
    }

    #[test]
    fn a_sealed_secret_comes_back_intact() {
        let sealed = secret_box().seal("kh_0000000000000000000000");
        assert_eq!(
            secret_box().open(&sealed).unwrap(),
            "kh_0000000000000000000000"
        );
    }

    #[test]
    fn the_ciphertext_does_not_contain_the_plaintext() {
        // The whole point of the column: a database dump must not read as the
        // key. Guards against someone "simplifying" seal to base64 alone.
        let sealed = secret_box().seal("kh_supersecret");
        assert!(!sealed.contains("kh_supersecret"));
        assert!(!BASE64
            .decode(&sealed)
            .unwrap()
            .windows(14)
            .any(|w| w == b"kh_supersecret"));
    }

    #[test]
    fn sealing_the_same_secret_twice_gives_different_ciphertexts() {
        // A fresh nonce each time. Without this, equal ciphertexts would reveal
        // that two accounts share a key.
        let boxed = secret_box();
        assert_ne!(boxed.seal("kh_same"), boxed.seal("kh_same"));
    }

    #[test]
    fn another_key_cannot_open_it() {
        let sealed = secret_box().seal("kh_secret");
        let attacker = SecretBox::from_base64(OTHER_KEY).unwrap();
        assert_eq!(attacker.open(&sealed), Err(SecretBoxError::Undecryptable));
    }

    #[test]
    fn a_tampered_ciphertext_is_rejected_rather_than_decrypted() {
        // The AEAD tag doing its job: an edited row fails loudly instead of
        // yielding an attacker-chosen key.
        let sealed = secret_box().seal("kh_secret");
        let mut raw = BASE64.decode(&sealed).unwrap();
        let last = raw.len() - 1;
        raw[last] ^= 0x01;
        let tampered = BASE64.encode(raw);

        assert_eq!(
            secret_box().open(&tampered),
            Err(SecretBoxError::Undecryptable)
        );
    }

    #[test]
    fn garbage_and_truncation_are_rejected_without_panicking() {
        let boxed = secret_box();
        assert_eq!(
            boxed.open("not base64 at all !!"),
            Err(SecretBoxError::Undecryptable)
        );
        assert_eq!(boxed.open(""), Err(SecretBoxError::Undecryptable));
        // Valid base64, but shorter than the nonce: must not slice out of bounds.
        assert_eq!(
            boxed.open(&BASE64.encode([0u8; 8])),
            Err(SecretBoxError::Undecryptable)
        );
    }

    #[test]
    fn a_key_of_the_wrong_length_is_refused() {
        // `SecretBox` deliberately has no `Debug`, so these match rather than
        // `unwrap_err` — a derived `Debug` on a type holding a key schedule is
        // exactly the kind of thing that ends up in a log line.
        for bad in ["c2hvcnQ=", "!!!", ""] {
            assert!(matches!(
                SecretBox::from_base64(bad),
                Err(SecretBoxError::BadKey)
            ));
        }
    }

    #[test]
    fn the_mask_shows_only_the_tail() {
        assert_eq!(mask("kh_000000000000000000000000000wxyz"), "••••wxyz");
        // Short inputs are not real keys, but must still not leak.
        assert_eq!(mask("kh_a"), "••••");
        assert_eq!(mask(""), "••••");
    }
}
