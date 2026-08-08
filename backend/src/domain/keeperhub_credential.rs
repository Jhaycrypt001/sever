//! A user's own KeeperHub API key — ADR-076.
//!
//! ADR-065 made Sever refuse to revoke unless the scanned wallet is the wallet
//! the executing API key is delegated to. With a single key in the environment
//! that meant one wallet, ever. A credential stored here is the same rule with
//! the key made per-account: the account's key executes as the account's
//! wallet, so the check passes for that user's own wallet and still refuses for
//! anyone else's. The safety property is unchanged — only its reach widens.
//!
//! The plaintext key never lives on this struct. It is sealed by
//! [`crate::domain::SecretBox`] at the edge and only opened when a revoke is
//! actually dispatched, so the value in memory at rest is a ciphertext.

use chrono::{DateTime, Utc};
use uuid::Uuid;

/// A saved KeeperHub credential, as the application layer handles it.
#[derive(Debug, Clone, PartialEq)]
pub struct KeeperHubCredential {
    pub user_id: Uuid,
    /// base64(nonce || ciphertext). Opaque here on purpose: only the
    /// `SecretBox` that sealed it can read it, and nothing in this struct
    /// should tempt a caller to log the key.
    pub api_key_encrypted: String,
    /// The wallet KeeperHub reports this key executes as, lowercase hex.
    /// `None` when KeeperHub could not be reached to confirm it — the key is
    /// still saved, because a network blip at save time should not cost the
    /// user their input, but the account screen shows it as unconfirmed.
    pub wallet_address: Option<String>,
    /// Last four characters, for display. Never the whole key.
    pub masked: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Whether a key that executes as `delegated` may revoke for `scanned`.
///
/// The same comparison the agent makes before it broadcasts (ADR-065),
/// duplicated here so the console can tell the user *before* a scan that its
/// revoke step will refuse, rather than letting them find out from a
/// `not_attempted` badge afterwards. Case-insensitive: EIP-55 checksummed input
/// and lowercase storage must compare equal.
pub fn can_revoke_for(delegated: Option<&str>, scanned: &str) -> bool {
    match delegated {
        Some(wallet) => wallet.trim().eq_ignore_ascii_case(scanned.trim()),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WALLET: &str = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf";

    #[test]
    fn a_key_may_revoke_for_its_own_delegated_wallet() {
        assert!(can_revoke_for(Some(WALLET), WALLET));
    }

    #[test]
    fn the_comparison_ignores_checksum_casing() {
        // The console sends whatever the user pasted; storage is lowercase.
        // A case-sensitive compare here would refuse a legitimate revoke.
        assert!(can_revoke_for(
            Some(WALLET),
            "0xE13ED979BC6B23D6D9608939051E9488E9F304BF"
        ));
    }

    #[test]
    fn a_key_may_not_revoke_for_another_wallet() {
        assert!(!can_revoke_for(
            Some(WALLET),
            "0x1234567890123456789012345678901234567890"
        ));
    }

    #[test]
    fn an_unconfirmed_wallet_may_not_revoke() {
        // No wallet read back from KeeperHub means no evidence the key executes
        // as the scanned address. ADR-065: never broadcast on a shrug.
        assert!(!can_revoke_for(None, WALLET));
    }
}
