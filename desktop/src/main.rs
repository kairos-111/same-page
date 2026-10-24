#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{io::{Read, Write}, net::TcpStream, path::PathBuf, process::{Child, Command, Stdio}, sync::{Mutex, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent, RunEvent};

struct Engine { child: Mutex<Option<Child>>, exiting: AtomicBool }
fn state() -> Result<serde_json::Value, String> {
    let mut stream = TcpStream::connect_timeout(&"127.0.0.1:8767".parse().unwrap(),Duration::from_secs(2)).map_err(|e|e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).map_err(|e|e.to_string())?;
    stream.write_all(b"GET /api/state HTTP/1.0\r\nHost: 127.0.0.1:8767\r\nConnection: close\r\n\r\n").map_err(|e|e.to_string())?;
    let mut response=String::new(); stream.read_to_string(&mut response).map_err(|e|e.to_string())?;
    let (_, body)=response.split_once("\r\n\r\n").ok_or("Invalid service response")?;
    let value: serde_json::Value=serde_json::from_str(body).map_err(|e|e.to_string())?;
    if value.get("token").is_none() || value.get("segments").is_none() {return Err("8767端口不是听课服务".into())}
    Ok(value)
}
#[tauri::command]
fn desktop_info(engine: tauri::State<Engine>) -> serde_json::Value {
    serde_json::json!({"version":"0.6.0","development":true,"owns_engine":engine.child.lock().unwrap().is_some()})
}
#[tauri::command]
async fn finish_exit(app: tauri::AppHandle) -> Result<(), String> {
    let handle=app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let data=state()?;
        let status=data["session"]["state"].as_str().unwrap_or("");
        if ["loading","running","draining"].contains(&status) {return Err("请先结束听课并等待保存完成".into())}
        let engine=handle.state::<Engine>();
        if let Some(mut child)=engine.child.lock().unwrap().take() {
            let _=Command::new("/bin/kill").args(["-TERM",&child.id().to_string()]).status();
            let deadline=Instant::now()+Duration::from_secs(8);
            while Instant::now()<deadline {
                if child.try_wait().ok().flatten().is_some() {break}
                std::thread::sleep(Duration::from_millis(100));
            }
            // Never force kill an unresponsive engine that may still be saving.
        }
        engine.exiting.store(true,Ordering::SeqCst);
        handle.exit(0); Ok(())
    }).await.map_err(|e|e.to_string())?
}
fn main() {
    let root=std::env::var_os("TINGKE_PROJECT_ROOT").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf());
    let mut owned=None;
    if state().is_err() {
        // Do not replace or attach to an unrelated process using the port.
        if TcpStream::connect("127.0.0.1:8767").is_ok() {eprintln!("8767端口被其他程序占用");return}
        let log=std::fs::OpenOptions::new().create(true).append(true).open(root.join("work/desktop-engine.log")).expect("Unable to open engine log");
        owned=Some(Command::new(root.join(".venv/bin/python")).arg("-u").arg(root.join("scripts/serve.py")).current_dir(&root).stdin(Stdio::null()).stdout(log.try_clone().unwrap()).stderr(log).spawn().expect("Unable to start local engine"));
        let deadline=Instant::now()+Duration::from_secs(30);
        while state().is_err() {
            if Instant::now()>deadline {eprintln!("本地服务启动超时，请查看work/desktop-engine.log");return}
            std::thread::sleep(Duration::from_millis(250));
        }
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app,_,_| {if let Some(w)=app.get_webview_window("main") {let _=w.show();let _=w.set_focus();}}))
        .manage(Engine {child:Mutex::new(owned),exiting:AtomicBool::new(false)})
        .invoke_handler(tauri::generate_handler![desktop_info,finish_exit])
        .setup(|app| {
            let window=WebviewWindowBuilder::new(app,"main",WebviewUrl::External("http://127.0.0.1:8767/".parse().unwrap()))
                .title("听课").inner_size(1200.0,820.0).min_inner_size(960.0,640.0).center().build()?;
            let handle=app.handle().clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested {api,..}=event {
                    if !handle.state::<Engine>().exiting.load(Ordering::SeqCst) {api.prevent_close();let _=handle.emit("desktop-close-requested",());}
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!()).expect("Unable to create desktop application")
        .run(|app,event| {if let RunEvent::ExitRequested {api,..}=event {if !app.state::<Engine>().exiting.load(Ordering::SeqCst) {api.prevent_exit();let _=app.emit("desktop-close-requested",());}}});
}
