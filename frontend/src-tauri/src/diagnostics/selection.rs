//! Defaults are independent scope choices, never a bundled export mode.
use std::collections::BTreeMap;
pub(super) const FAMILY_BYTES: u64 = 5 * 1024 * 1024;
pub(super) const TOTAL_BYTES: u64 = 32 * 1024 * 1024;

pub(super) fn family(name: &str) -> Option<&str> {
    if name.ends_with(".writer-lock") {
        return None;
    }
    [
        "tauri.log",
        "agent_bridge.log",
        "voice_bridge.log",
        "memory.log",
        "runtime-bridge.log",
        "runtime-voice.log",
        "dev-bridge.log",
        "dev-voice.log",
    ]
    .into_iter()
    .find(|prefix| {
        name == *prefix
            || name
                .strip_prefix(prefix)
                .is_some_and(|suffix| suffix.starts_with('.'))
    })
}

pub(super) struct Budget {
    all: bool,
    total: u64,
    families: BTreeMap<String, u64>,
}
impl Budget {
    pub fn new(all: bool) -> Self {
        Self {
            all,
            total: 0,
            families: BTreeMap::new(),
        }
    }
    pub fn allocate(&mut self, family: &str, size: u64) -> u64 {
        if self.all {
            return size;
        }
        let used = self.families.entry(family.into()).or_default();
        let amount = size
            .min(FAMILY_BYTES.saturating_sub(*used))
            .min(TOTAL_BYTES.saturating_sub(self.total));
        *used += amount;
        self.total += amount;
        amount
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rotations_share_budget_and_full_scope_has_no_default_limit() {
        let mut budget = Budget::new(false);
        assert_eq!(family("runtime-bridge.log.1"), Some("runtime-bridge.log"));
        assert_eq!(
            budget.allocate("runtime-bridge.log", FAMILY_BYTES - 10),
            FAMILY_BYTES - 10
        );
        assert_eq!(budget.allocate("runtime-bridge.log", FAMILY_BYTES), 10);
        assert_eq!(budget.allocate("runtime-bridge.log", 1), 0);
        for n in 0..20 {
            budget.allocate(&format!("{n}"), FAMILY_BYTES);
        }
        assert_eq!(budget.total, TOTAL_BYTES);
        assert_eq!(
            Budget::new(true).allocate("x", TOTAL_BYTES * 3),
            TOTAL_BYTES * 3
        );
    }
}
