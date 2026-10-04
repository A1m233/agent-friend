use crate::install_contract::{self, InstallProfile};

fn script_for_profile(profile: &InstallProfile) -> String {
    let endpoints = serde_json::json!({
        "profileId": profile.profile_id,
        "bridge": format!("http://127.0.0.1:{}", profile.bridge_port),
        "voice": format!("http://127.0.0.1:{}", profile.voice_port),
    });
    format!(
        "Object.defineProperty(window, '__AGENT_FRIEND_LOCAL_SERVICES__', {{ value: Object.freeze({endpoints}), writable: false, configurable: false }});"
    )
}

/// Runs before application modules in every WebView, including later-created windows.
pub fn init_script() -> String {
    script_for_profile(
        &install_contract::embedded_profile().expect("embedded install profile must be valid"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bootstrap_uses_profile_ports_for_both_services() {
        let mut profile = install_contract::production_profile();
        let production = script_for_profile(&profile);
        assert!(production.contains("http://127.0.0.1:18800"));
        assert!(production.contains("http://127.0.0.1:18900"));
        profile.profile_id = "installer-smoke".into();
        profile.bridge_port = 28800;
        profile.voice_port = 28900;
        let smoke = script_for_profile(&profile);
        assert!(smoke.contains("installer-smoke"));
        assert!(smoke.contains("http://127.0.0.1:28800"));
        assert!(smoke.contains("http://127.0.0.1:28900"));
        assert!(!smoke.contains(":18800"));
        assert!(!smoke.contains(":18900"));
    }
}
