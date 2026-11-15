//! Desktop-owned onboarding actions. Both the WebView and the local command use
//! the same catalog, readiness checks and settings writer.
pub(crate) mod defaults;
pub(crate) mod window;

use crate::{
    model_connections::ModelConnectionCoordinator,
    runtime_config::RuntimeConfigGroup,
    runtime_credentials::RuntimeCredentialCoordinator,
    runtime_services::RuntimeServiceKind,
    settings::{self, ModelProvider, ModelSelection, SetPayload},
};
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream},
    process::{Command, Stdio},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};

#[derive(Deserialize)]
struct Catalog {
    providers: Vec<Provider>,
}
#[derive(Deserialize)]
struct Provider {
    id: ModelProvider,
    models: Vec<CatalogModel>,
}
#[derive(Deserialize)]
struct CatalogModel {
    id: String,
    label: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AvailableModel {
    provider: ModelProvider,
    model: String,
    label: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupStatus {
    models: Vec<AvailableModel>,
    chat_model: ModelSelection,
    memory_model: ModelSelection,
    chat_ready: bool,
    memory_ready: bool,
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|_| "本机服务暂不可用".into())
}

#[tauri::command]
pub async fn get_onboarding_status(app: AppHandle) -> Result<SetupStatus, String> {
    let settings = settings::migration_snapshot(&app.state())?;
    let models = available_models(&app, &settings).await?;
    let ready = |selection: &ModelSelection| {
        models
            .iter()
            .any(|m| m.provider == selection.provider && m.model == selection.model)
    };
    Ok(SetupStatus {
        chat_ready: ready(&settings.chat_model),
        memory_ready: ready(&settings.memory_model),
        chat_model: settings.chat_model,
        memory_model: settings.memory_model,
        models,
    })
}

async fn available_models(
    app: &AppHandle,
    settings: &settings::Settings,
) -> Result<Vec<AvailableModel>, String> {
    let response = client()?
        .get(format!(
            "{}/v1/models/catalog",
            RuntimeServiceKind::Bridge.base_url()
        ))
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|_| "模型服务尚未就绪，请稍后重试")?;
    let catalog: Catalog = response.json().await.map_err(|_| "模型目录读取失败")?;
    let (deepseek, kimi, custom) = ready_providers(app, settings).await?;
    let mut models = Vec::new();
    for provider in catalog.providers {
        let builtin = match provider.id {
            ModelProvider::Deepseek => deepseek,
            ModelProvider::Kimi => kimi,
            ModelProvider::Openrouter => continue,
            ModelProvider::Custom => false,
        };
        for entry in provider.models {
            if builtin
                || (provider.id == ModelProvider::Custom
                    && entry
                        .id
                        .split('/')
                        .nth(1)
                        .is_some_and(|id| custom.iter().any(|c| c == id)))
            {
                models.push(AvailableModel {
                    provider: provider.id,
                    model: entry.id,
                    label: entry.label,
                });
            }
        }
    }
    Ok(models)
}

async fn ready_providers(
    app: &AppHandle,
    settings: &settings::Settings,
) -> Result<(bool, bool, Vec<String>), String> {
    let custom = app
        .state::<ModelConnectionCoordinator>()
        .ready_connections(settings)
        .await?;
    let groups = app.state::<std::sync::Arc<RuntimeCredentialCoordinator>>();
    let deepseek = groups
        .usable_for_onboarding(RuntimeConfigGroup::Deepseek)
        .await?;
    let kimi = groups
        .usable_for_onboarding(RuntimeConfigGroup::Kimi)
        .await?;
    Ok((deepseek, kimi, custom))
}

// The local CLI selects by id; the desktop form can also choose a reasoning preset.
#[derive(Deserialize)]
#[serde(untagged)]
pub enum RequestedModel {
    Id(String),
    Selection(ModelSelection),
}

#[tauri::command]
pub async fn select_onboarding_model(
    app: AppHandle,
    purpose: String,
    model: RequestedModel,
) -> Result<(), String> {
    let status = get_onboarding_status(app.clone()).await?;
    let (key, current) = match purpose.as_str() {
        "chat" => ("chatModel", status.chat_model),
        "memory" => ("memoryModel", status.memory_model),
        _ => return Err("请选择对话或记忆用途".into()),
    };
    let id = match &model {
        RequestedModel::Id(id) => id,
        RequestedModel::Selection(selection) => &selection.model,
    };
    let selected = status
        .models
        .iter()
        .find(|entry| entry.model == *id)
        .ok_or("模型没有可用连接，请先配置凭据")?;
    let selection = match model {
        RequestedModel::Selection(selection) if selection.provider == selected.provider => {
            selection
        }
        RequestedModel::Selection(_) => return Err("模型与调用渠道不匹配".into()),
        RequestedModel::Id(id) if current.model == id && current.provider == selected.provider => {
            current
        }
        RequestedModel::Id(model) => ModelSelection {
            provider: selected.provider,
            model,
            reasoning_preset: None,
        },
    };
    client()?
        .post(format!(
            "{}/v1/models/validate",
            RuntimeServiceKind::Bridge.base_url()
        ))
        .json(&selection)
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|_| "模型配置未通过检查，请刷新模型目录并重试")?;
    settings::set_setting(
        app.clone(),
        app.state(),
        SetPayload {
            key: key.into(),
            value: json!(selection),
        },
    )
}

fn valid_action(args: &[String]) -> bool {
    match args {
        [a] => a == "open" || a == "status",
        [a, step] => {
            a == "open"
                && matches!(
                    step.as_str(),
                    "connect" | "chat" | "memory" | "voice" | "persona" | "companionship"
                )
        }
        [a, purpose, model] => {
            a == "select"
                && matches!(purpose.as_str(), "chat" | "memory")
                && !model.is_empty()
                && model.len() <= 512
                && !model.contains('|')
                && !model.chars().any(char::is_control)
        }
        _ => false,
    }
}

// The CLI uses the normal desktop single-instance handoff. A one-shot loopback
// reply channel avoids publishing a permanent writable HTTP API or a reply path.
const HANDOFF: &str = "--af-onboarding-request";
const TIMEOUT: Duration = Duration::from_secs(40);

pub fn run_cli() -> Option<i32> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) != Some("--af-setup") {
        return None;
    }
    let result = if valid_action(&args[1..]) {
        request(&args[1..])
    } else {
        Err("usage: --af-setup status | open | select chat|memory MODEL_ID".into())
    };
    let response = result.unwrap_or_else(|error| json!({"ok":false,"error":error}));
    println!("{response}");
    Some(if response["ok"] == true { 0 } else { 1 })
}

fn request(action: &[String]) -> Result<Value, String> {
    exchange(|port, ticket| {
        Command::new(std::env::current_exe()?)
            .arg(HANDOFF)
            .arg(port.to_string())
            .arg(ticket)
            .args(action)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        Ok(())
    })
}

fn exchange(launch: impl FnOnce(u16, &str) -> std::io::Result<()>) -> Result<Value, String> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).map_err(|e| e.to_string())?;
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let mut token = [0u8; 24];
    OsRng.fill_bytes(&mut token);
    let ticket: String = token.iter().map(|byte| format!("{byte:02x}")).collect();
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    launch(port, &ticket).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + TIMEOUT;
    while Instant::now() < deadline {
        match listener.accept() {
            Ok((mut stream, _)) => {
                // Winsock can pass the listener's nonblocking mode to accepted sockets.
                // The handshake and JSON reader wait for the desktop's bounded reply.
                stream.set_nonblocking(false).map_err(|e| e.to_string())?;
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .map_err(|e| e.to_string())?;
                // Mutual challenge: the requester and the desktop both received the ticket.
                stream
                    .write_all(ticket[..24].as_bytes())
                    .map_err(|e| e.to_string())?;
                let mut challenge = [0; 24];
                if stream.read_exact(&mut challenge).is_err()
                    || challenge != ticket.as_bytes()[24..]
                {
                    continue;
                }
                stream
                    .set_read_timeout(Some(TIMEOUT))
                    .map_err(|e| e.to_string())?;
                return serde_json::from_reader(stream.take(2 * 1024 * 1024))
                    .map_err(|_| "invalid desktop response".into());
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50))
            }
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("AF 未响应。请打开对应版本的桌面应用后重试。".into())
}

fn open_reply_channel(port: u16, ticket: &str) -> std::io::Result<TcpStream> {
    let mut stream = TcpStream::connect_timeout(
        &SocketAddrV4::new(Ipv4Addr::LOCALHOST, port).into(),
        Duration::from_secs(2),
    )?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let mut challenge = [0; 24];
    stream.read_exact(&mut challenge)?;
    if challenge != ticket.as_bytes()[..24] {
        return Err(std::io::ErrorKind::PermissionDenied.into());
    }
    stream.write_all(ticket[24..].as_bytes())?;
    Ok(stream)
}

pub(crate) fn activate(app: &AppHandle, args: &[String]) -> bool {
    if args.get(1).map(String::as_str) != Some(HANDOFF) {
        return false;
    }
    let [port, ticket, action @ ..] = &args[2..] else {
        return true;
    };
    let Ok(port) = port.parse::<u16>() else {
        return true;
    };
    if ticket.len() != 48 || !ticket.bytes().all(|b| b.is_ascii_hexdigit()) || !valid_action(action)
    {
        return true;
    }
    let action = action.to_vec();
    let ticket = ticket.clone();
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let stream =
            tauri::async_runtime::spawn_blocking(move || open_reply_channel(port, &ticket)).await;
        let Ok(Ok(stream)) = stream else {
            return;
        };
        let result: Result<Value, String> = match action.as_slice() {
            [a] if a == "open" => window::show(&app, None)
                .await
                .map(|_| json!({"opened":true})),
            [_, step] => window::show(&app, Some(step.clone()))
                .await
                .map(|_| json!({"opened":true})),
            [_] => get_onboarding_status(app).await.map(|s| json!(s)),
            [_, purpose, model] => {
                select_onboarding_model(app, purpose.clone(), RequestedModel::Id(model.clone()))
                    .await
                    .map(|_| json!({"saved":true}))
            }
            _ => Err("unknown action".into()),
        };
        let response = match result {
            Ok(value) => json!({"ok":true,"result":value}),
            Err(error) => json!({"ok":false,"error":error}),
        };
        let _ =
            tauri::async_runtime::spawn_blocking(move || serde_json::to_writer(stream, &response))
                .await;
    });
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn command_receives_only_the_owners_json_on_its_one_shot_channel() {
        let reply = exchange(|port, ticket| {
            let ticket = ticket.to_owned();
            std::thread::spawn(move || {
                let stream = open_reply_channel(port, &ticket).unwrap();
                std::thread::sleep(Duration::from_millis(75));
                serde_json::to_writer(stream, &json!({"ok":true,"result":{"opened":true}}))
                    .unwrap();
            });
            Ok(())
        })
        .unwrap();
        assert_eq!(reply["result"]["opened"], true);
    }

    #[test]
    fn accepts_only_bounded_non_secret_setup_actions() {
        for (args, allowed) in [
            ("open", true),
            ("open memory", true),
            ("open bogus", false),
            ("status", true),
            ("select memory custom/c/m", true),
            ("select chat deepseek/m", true),
            ("select other m", false),
            ("set-key x", false),
            ("status extra", false),
        ] {
            assert_eq!(
                valid_action(
                    &args
                        .split_whitespace()
                        .map(String::from)
                        .collect::<Vec<_>>()
                ),
                allowed
            );
        }
    }
}
