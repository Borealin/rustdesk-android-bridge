//! Local scrcpy backend behind the existing RustDesk authenticated, encrypted session.
//! Never call set_raw() on the network stream.
use hbb_common::{bail, message_proto::*, protobuf::Message as _, tcp::FramedStream, ResultType};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, time::Duration};

#[derive(Deserialize)]
struct Settings {
    port: u16,
    password_file: PathBuf,
    frontend_password_file: PathBuf,
    #[serde(default)]
    frontend_options: std::collections::BTreeMap<String, String>,
}

pub fn enabled() -> bool {
    std::env::var_os("RUSTDESK_ANDROID_BACKEND_CONFIG").is_some()
}

pub fn isolate_config() {
    if enabled() {
        *hbb_common::config::APP_NAME.write().unwrap() = "RustDeskAndroidBridge".into();
        if let Err(err) = bootstrap() {
            eprintln!("Android host configuration failed: {err}");
            std::process::exit(2);
        }
    }
}

fn bootstrap() -> ResultType<()> {
    use hbb_common::config::Config;
    let path = std::env::var_os("RUSTDESK_ANDROID_BACKEND_CONFIG")
        .ok_or_else(|| hbb_common::anyhow::anyhow!("backend config missing"))?;
    let settings: Settings = serde_json::from_slice(&std::fs::read(path)?)?;
    if Config::get_option("android-backend-initialized") == "Y" { return Ok(()); }
    // Default desktop IDs may derive from the MAC address; the phone host
    // must not reuse the installed desktop host identity.
    Config::set_id(&(1_000_000_000u32 + hbb_common::rand::random::<u32>() % 1_000_000_000).to_string());
    for (key, value) in settings.frontend_options {
        if matches!(key.as_str(), "custom-rendezvous-server" | "relay-server" | "api-server" | "key") {
            Config::set_option(key, value);
        }
    }
    let value = read_password(&settings.frontend_password_file)?;
    if !Config::has_permanent_password() && !Config::set_permanent_password(&value) {
        bail!("could not initialize Android host password");
    }
    Config::set_option("approve-mode".into(), "password".into());
    Config::set_option("verification-method".into(), "use-permanent-password".into());
    Config::set_option("direct-server".into(), "N".into());
    for key in ["enable-clipboard", "enable-file-transfer", "enable-audio", "enable-tunnel",
        "enable-camera", "enable-terminal", "enable-remote-restart", "enable-block-input", "enable-privacy-mode"] {
        Config::set_option(key.into(), "N".into());
    }
    Config::set_option("android-backend-initialized".into(), "Y".into());
    Ok(())
}

fn read_password(path: &PathBuf) -> ResultType<String> {
    let meta = std::fs::metadata(path)?;
    if !meta.is_file() { bail!("backend password is not a regular file"); }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o077 != 0 {
            bail!("backend password file permissions must be owner-only");
        }
    }
    let text = std::fs::read_to_string(path)?;
    let text = text.trim().to_owned();
    if !(24..=256).contains(&text.len()) { bail!("invalid backend password length"); }
    Ok(text)
}

pub async fn connect(login: &LoginRequest) -> ResultType<(FramedStream, Message)> {
    let path = std::env::var_os("RUSTDESK_ANDROID_BACKEND_CONFIG")
        .ok_or_else(|| hbb_common::anyhow::anyhow!("backend config missing"))?;
    let settings: Settings = serde_json::from_slice(&std::fs::read(path)?)?;
    let password = read_password(&settings.password_file)?;
    let mut stream = FramedStream::new(format!("127.0.0.1:{}", settings.port), None, 3000).await?;
    let bytes = stream.next_timeout(5000).await
        .ok_or_else(|| hbb_common::anyhow::anyhow!("backend challenge timeout"))??;
    let challenge = Message::parse_from_bytes(&bytes)?;
    let Some(message::Union::Hash(hash)) = challenge.union else { bail!("invalid backend challenge"); };
    let mut first = Sha256::new();
    first.update(password.as_bytes());
    first.update(hash.salt.as_bytes());
    let mut second = Sha256::new();
    second.update(first.finalize());
    second.update(hash.challenge.as_bytes());
    let mut local_login = login.clone();
    local_login.password = second.finalize().to_vec().into();
    // Do not disclose the remote peer's identity to the local media worker.
    local_login.my_id = "native-host".into();
    local_login.my_name = "native-host".into();
    let mut request = Message::new();
    request.set_login_request(local_login);
    stream.send(&request).await?;
    let bytes = stream.next_timeout(30000).await
        .ok_or_else(|| hbb_common::anyhow::anyhow!("backend startup timeout"))??;
    let response = Message::parse_from_bytes(&bytes)?;
    match &response.union {
        Some(message::Union::LoginResponse(lr)) if matches!(&lr.union, Some(login_response::Union::PeerInfo(_))) => {},
        _ => bail!("backend did not accept desktop session"),
    }
    Ok((stream, response))
}

pub async fn receive(backend: &mut Option<FramedStream>) -> ResultType<Message> {
    match backend {
        Some(stream) => {
            let bytes = stream.next().await
                .ok_or_else(|| hbb_common::anyhow::anyhow!("backend disconnected"))??;
            let msg = Message::parse_from_bytes(&bytes)?;
            match &msg.union {
                Some(message::Union::VideoFrame(_)) | Some(message::Union::TestDelay(_)) => Ok(msg),
                Some(message::Union::Misc(m)) if matches!(&m.union, Some(misc::Union::SwitchDisplay(_))) => Ok(msg),
                _ => bail!("unexpected backend message"),
            }
        }
        None => std::future::pending().await,
    }
}

pub async fn forward(backend: &mut FramedStream, msg: &Message) -> ResultType<()> {
    hbb_common::tokio::time::timeout(Duration::from_secs(5), backend.send(msg)).await??;
    Ok(())
}
