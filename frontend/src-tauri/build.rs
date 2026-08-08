fn main() {
    println!("cargo:rerun-if-env-changed=AGENT_FRIEND_BUILD_IDENTITY_JSON");
    println!("cargo:rerun-if-env-changed=AGENT_FRIEND_INSTALL_PROFILE_JSON");
    println!("cargo:rerun-if-env-changed=AGENT_FRIEND_PRODUCT_VERSION");
    let product_version = std::env::var("AGENT_FRIEND_PRODUCT_VERSION")
        .unwrap_or_else(|_| env!("CARGO_PKG_VERSION").to_string());
    assert!(
        !product_version.contains('\r') && !product_version.contains('\n'),
        "AGENT_FRIEND_PRODUCT_VERSION must be a single line"
    );
    println!("cargo:rustc-env=AGENT_FRIEND_PRODUCT_VERSION={product_version}");
    tauri_build::build()
}
