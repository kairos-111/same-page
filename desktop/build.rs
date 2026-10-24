fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["desktop_info", "finish_exit"])
    )).expect("Tauri build failed");
}
