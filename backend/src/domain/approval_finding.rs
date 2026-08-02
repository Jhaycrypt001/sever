use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

/// How dangerous an outstanding approval is judged to be (ADR-058).
///
/// Deliberately a closed, deterministic vocabulary rather than a free-form
/// LLM score: the agent's `classify_risk` (not an LLM) assigns the tier from
/// verified threat-intel signals, and the tier alone decides whether an
/// approval is auto-revoked or left for the user. The backend never assigns
/// or changes a tier — it only stores and re-serves what the agent decided.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum RiskTier {
    Safe,
    Watch,
    Dangerous,
}

/// What happened when a DANGEROUS-tier approval was sent for revocation
/// (ADR-058).
///
/// `Revoked` means a transaction was broadcast and confirmed onchain, with
/// `revocation_tx_hash` to prove it. A dry run is `Simulated`, never
/// `Revoked` — the UI must never show a still-live approval as neutralized.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum RevocationStatus {
    #[default]
    NotAttempted,
    Pending,
    Simulated,
    Revoked,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, ToSchema)]
pub struct ApprovalFinding {
    pub chain_id: String,
    pub token_address: String,
    pub token_symbol: String,
    pub spender_address: String,
    #[serde(default)]
    pub spender_name: Option<String>,
    pub approved_amount: String,
    pub tier: RiskTier,
    #[serde(default)]
    pub malicious_behavior: Vec<String>,
    #[serde(default)]
    pub explanation: Option<String>,
    /// False when a recurring run already saw this exact (chain, token,
    /// spender) approval (ADR-033); one-shot scans leave every finding new.
    /// Default keeps older payloads working.
    #[serde(default = "default_true")]
    pub is_new: bool,
    #[serde(default)]
    pub revocation_status: RevocationStatus,
    #[serde(default)]
    pub revocation_tx_hash: Option<String>,
    #[serde(default)]
    pub raw: serde_json::Value,
}

fn default_true() -> bool {
    true
}

impl ApprovalFinding {
    /// Identity for dedup/memory (ADR-033/034 equivalent): one wallet can
    /// only hold one live approval per (chain, token, spender). Lowercased so
    /// address casing never causes a false "new" alert.
    pub fn approval_key(&self) -> String {
        format!(
            "{}:{}:{}",
            self.chain_id, self.token_address, self.spender_address
        )
        .to_lowercase()
    }
}

fn tier_rank(tier: RiskTier) -> u8 {
    match tier {
        RiskTier::Dangerous => 0,
        RiskTier::Watch => 1,
        RiskTier::Safe => 2,
    }
}

/// Sorts findings most-dangerous-first (the ADR-011 sort-by-date equivalent).
pub fn sort_by_risk(findings: &mut [ApprovalFinding]) {
    findings.sort_by_key(|f| tier_rank(f.tier));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn finding(spender: &str, tier: RiskTier) -> ApprovalFinding {
        ApprovalFinding {
            chain_id: "1".into(),
            token_address: "0xtoken".into(),
            token_symbol: "TKN".into(),
            spender_address: spender.into(),
            spender_name: None,
            approved_amount: "Unlimited".into(),
            tier,
            malicious_behavior: vec![],
            explanation: None,
            is_new: true,
            revocation_status: RevocationStatus::default(),
            revocation_tx_hash: None,
            raw: serde_json::Value::Null,
        }
    }

    #[test]
    fn older_payloads_without_new_fields_still_deserialize() {
        // Pre-ADR-058 wire shape had none of these fields on the old
        // SearchResult; the new required fields (chain_id, token_address,
        // spender_address, approved_amount, tier) are what a producer must
        // now send, but the optional ones still default cleanly.
        let json = r#"{"chain_id":"1","token_address":"0xt","token_symbol":"T",
            "spender_address":"0xs","approved_amount":"1","tier":"safe"}"#;
        let parsed: ApprovalFinding = serde_json::from_str(json).unwrap();
        assert_eq!(parsed.revocation_status, RevocationStatus::NotAttempted);
        assert!(parsed.is_new);
        assert_eq!(parsed.malicious_behavior, Vec::<String>::new());
    }

    #[test]
    fn sorts_most_dangerous_first() {
        let mut findings = vec![
            finding("safe", RiskTier::Safe),
            finding("dangerous", RiskTier::Dangerous),
            finding("watch", RiskTier::Watch),
        ];

        sort_by_risk(&mut findings);

        let spenders: Vec<&str> = findings
            .iter()
            .map(|f| f.spender_address.as_str())
            .collect();
        assert_eq!(spenders, vec!["dangerous", "watch", "safe"]);
    }

    #[test]
    fn approval_key_is_lowercased_and_stable() {
        let a = finding("0xAbC", RiskTier::Safe);
        let mut b = finding("0xabc", RiskTier::Safe);
        b.tier = RiskTier::Dangerous; // tier does not affect identity
        assert_eq!(a.approval_key(), b.approval_key());
        assert_eq!(a.approval_key(), "1:0xtoken:0xabc");
    }
}
