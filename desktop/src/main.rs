#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{io::{Read, Write}, net::TcpStream, path::PathBuf, process::{Child, Command, Stdio}, sync::{Mutex, OnceLock, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent, RunEvent};

struct Layout { root:PathBuf, data:PathBuf, python:PathBuf, port:u16, llama:u16, bundled:bool }
static LAYOUT:OnceLock<Layout>=OnceLock::new();
fn layout()-> &'static Layout { LAYOUT.get_or_init(|| {
    let executable=std::env::current_exe().expect("无法定位应用");
    let resources=executable.parent().unwrap().parent().unwrap().join("Resources/engine");
    let bundled=resources.join("bundle.json").is_file();
    let root=if bundled {resources} else {std::env::var_os("TINGKE_PROJECT_ROOT").map(PathBuf::from).unwrap_or_else(||PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf())};
    let data=if bundled {PathBuf::from(std::env::var_os("HOME").expect("无法定位用户目录")).join("Library/Application Support/听课")} else {root.clone()};
    let python=if bundled {root.join("work/runtime/python/bin/python3.12")} else {root.join(".venv/bin/python")};
    Layout{root,data,python,port:if bundled {8877} else {8767},llama:if bundled {8878} else {8768},bundled}
})}
struct Engine { child:Mutex<Option<Child>>, exiting:AtomicBool }
fn state()->Result<serde_json::Value,String> {
    let port=layout().port;
    let mut stream=TcpStream::connect_timeout(&format!("127.0.0.1:{port}").parse().unwrap(),Duration::from_secs(2)).map_err(|e|e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).map_err(|e|e.to_string())?;
    stream.write_all(format!("GET /api/state HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n").as_bytes()).map_err(|e|e.to_string())?;
    let mut response=String::new();stream.read_to_string(&mut response).map_err(|e|e.to_string())?;
    let (_,body)=response.split_once("\r\n\r\n").ok_or("本地引擎响应无效")?;
    let value:serde_json::Value=serde_json::from_str(body).map_err(|e|e.to_string())?;
    if value.get("token").is_none() || value.get("segments").is_none() {return Err("端口不是 Same Page 服务".into())}
    if layout().bundled && value["engine_root"].as_str()!=layout().root.to_str() {return Err("端口已被另一个引擎占用".into())}
    // An engine left over from an older app would serve new pages with old APIs.
    if layout().bundled && value["version"].as_str()!=Some(env!("CARGO_PKG_VERSION")) {
        return Err(format!("端口{}上仍在运行旧版本的本地引擎（{}）。请退出 Same Page，在“活动监视器”中结束残留的 python 进程后重新打开。",port,value["version"].as_str().unwrap_or("未知版本")))
    }
    Ok(value)
}
fn start_engine()->Result<Option<Child>,String> {
    let cfg=layout();
    let existing=state();
    if existing.is_ok(){return Ok(None)}
    if TcpStream::connect(format!("127.0.0.1:{}",cfg.port)).is_ok(){
        let reason=existing.err().unwrap_or_default();
        if reason.contains("旧版本"){return Err(reason)}
        return Err(format!("本地端口{}已被占用。请先关闭另一个 Same Page 实例，再重新打开应用。",cfg.port))
    }
    std::fs::create_dir_all(cfg.data.join("work")).map_err(|e|e.to_string())?;
    let log=std::fs::OpenOptions::new().create(true).append(true).open(cfg.data.join("work/desktop-engine.log")).map_err(|e|e.to_string())?;
    let mut cmd=Command::new(&cfg.python);
    cmd.arg("-I").arg("-B").arg("-u").arg(cfg.root.join("scripts/serve.py")).current_dir(&cfg.data)
        .env("TINGKE_DATA_ROOT",&cfg.data).env("TINGKE_API_PORT",cfg.port.to_string()).env("TINGKE_LLAMA_PORT",cfg.llama.to_string())
        .env("PYTHONNOUSERSITE","1").env("PYTHONDONTWRITEBYTECODE","1")
        .env_remove("PYTHONPATH").env_remove("PYTHONHOME")
        .stdin(Stdio::null()).stdout(log.try_clone().map_err(|e|e.to_string())?).stderr(log);
    let mut child=cmd.spawn().map_err(|e|format!("无法启动内置引擎：{e}"))?;
    let deadline=Instant::now()+Duration::from_secs(45);
    loop {
        if state().is_ok(){return Ok(Some(child))}
        if child.try_wait().map_err(|e|e.to_string())?.is_some(){return Err(format!("引擎启动失败，请查看：{}",cfg.data.join("work/desktop-engine.log").display()))}
        if Instant::now()>deadline {
            // A newly spawned engine has not accepted any classroom; terminate only this child.
            let _=Command::new("/bin/kill").args(["-TERM",&child.id().to_string()]).status();
            return Err(format!("引擎启动超时，请查看：{}",cfg.data.join("work/desktop-engine.log").display()))
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}
#[tauri::command]
fn desktop_info(engine:tauri::State<Engine>)->serde_json::Value {
    serde_json::json!({"version":"0.10.2","development":!layout().bundled,"owns_engine":engine.child.lock().unwrap().is_some()})
}
#[tauri::command]
async fn finish_exit(app:tauri::AppHandle)->Result<(),String> {
    let handle=app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let engine=handle.state::<Engine>();
        match state() {
            Ok(data)=> {if ["loading","running","draining"].contains(&data["session"]["state"].as_str().unwrap_or("")){return Err("请先结束翻译并等待保存完成".into())}},
            Err(_)=> {if engine.child.lock().unwrap().is_some(){return Err("无法核对引擎保存状态，请稍后重试".into())}}
        }
        let owned={engine.child.lock().unwrap().take()};
        if let Some(mut child)=owned {
            let _=Command::new("/bin/kill").args(["-TERM",&child.id().to_string()]).status();
            let deadline=Instant::now()+Duration::from_secs(8);
            loop {
                if child.try_wait().map_err(|e|e.to_string())?.is_some(){break}
                if Instant::now()>deadline {*engine.child.lock().unwrap()=Some(child);return Err("引擎仍在退出，请稍后重试".into())}
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        engine.exiting.store(true,Ordering::SeqCst);handle.exit(0);Ok(())
    }).await.map_err(|e|e.to_string())?
}
fn main() {
    let _=layout();
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app,_,_| {if let Some(w)=app.get_webview_window("main"){let _=w.show();let _=w.set_focus();}}))
        .manage(Engine{child:Mutex::new(None),exiting:AtomicBool::new(false)})
        .invoke_handler(tauri::generate_handler![desktop_info,finish_exit])
        .setup(|app| {
            let startup=start_engine();
            let error=match startup {Ok(child)=>{*app.state::<Engine>().child.lock().unwrap()=child;None},Err(error)=>Some(error)};
            let url=if error.is_some(){WebviewUrl::App("startup-error.html".into())}else{WebviewUrl::External(format!("http://127.0.0.1:{}/?v={}",layout().port,env!("CARGO_PKG_VERSION")).parse().unwrap())};
            let page_error=error.clone();
            let window=WebviewWindowBuilder::new(app,"main",url).disable_drag_drop_handler()
                .on_page_load(move |window,payload| {if let tauri::webview::PageLoadEvent::Finished=payload.event() {if let Some(ref message)=page_error {let _=window.eval(format!("window.startupError={};if(window.showStartupError)window.showStartupError();",serde_json::to_string(message).unwrap()));}}})
                .on_navigation(|url| (url.host_str()==Some("127.0.0.1") && url.port_or_known_default()==Some(layout().port)) || matches!(url.scheme(),"tauri"|"asset") || url.host_str()==Some("tauri.localhost"))
                .title("Same Page").inner_size(1200.0,820.0).min_inner_size(960.0,640.0).center().build()?;
            if let Some(error)=error {let _=window.eval(format!("window.startupError={};if(window.showStartupError)window.showStartupError();",serde_json::to_string(&error).unwrap()));}
            let handle=app.handle().clone();
            window.on_window_event(move |event| {if let WindowEvent::CloseRequested{api,..}=event {if !handle.state::<Engine>().exiting.load(Ordering::SeqCst){api.prevent_close();if state().is_err() && handle.state::<Engine>().child.lock().unwrap().is_none(){handle.state::<Engine>().exiting.store(true,Ordering::SeqCst);handle.exit(0)}else{let _=handle.emit("desktop-close-requested",());}}}});
            Ok(())
        })
        .build(tauri::generate_context!()).expect("Unable to create desktop application")
        .run(|app,event| {if let RunEvent::ExitRequested{api,..}=event {if !app.state::<Engine>().exiting.load(Ordering::SeqCst){api.prevent_exit();if state().is_err() && app.state::<Engine>().child.lock().unwrap().is_none(){app.state::<Engine>().exiting.store(true,Ordering::SeqCst);app.exit(0)}else{let _=app.emit("desktop-close-requested",());}}}});
}
