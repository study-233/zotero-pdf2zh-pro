use reqwest::{header, Client, Url};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs::{self, File},
    io::{self, Cursor, Read, Write},
    path::{Component, Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter};
use zip::ZipArchive;

const MANIFEST_URL: &str =
    "https://github.com/study-233/zotero-pdf2zh-pro/releases/latest/download/windows-update.json";
const RELEASE_PREFIX: &str = "/study-233/zotero-pdf2zh-pro/releases/download/";
const ASSET_NAME: &str = "zotero-pdf2zh-pro-windows-x64.zip";
const MAX_PACKAGE_SIZE: u64 = 256 * 1024 * 1024;
const MAX_EXTRACTED_SIZE: u64 = 512 * 1024 * 1024;
const REQUIRED_FILES: &[&str] = &[
    "zotero-pdf2zh-pro.exe",
    "apply-update.ps1",
    "common.ps1",
    "install.ps1",
    "start-server.ps1",
    "stop-server.ps1",
    "uninstall.ps1",
];

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WindowsUpdateManifest {
    schema_version: u32,
    pub version: String,
    pub url: String,
    size: u64,
    sha256: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateCheck {
    pub available: bool,
    pub current_version: String,
    pub latest_version: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateProgress {
    phase: &'static str,
    downloaded: u64,
    total: u64,
}

pub struct StagedUpdate {
    pub directory: PathBuf,
    pub apply_script: PathBuf,
    pub version: String,
}

fn http_client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .user_agent("zotero-pdf2zh-pro-windows-updater")
        .build()
        .map_err(|error| format!("无法创建更新客户端：{error}"))
}

fn parse_stable_version(value: &str) -> Result<Version, String> {
    let version = Version::parse(value).map_err(|_| format!("更新版本格式无效：{value}"))?;
    if !version.pre.is_empty() || !version.build.is_empty() {
        return Err(format!("更新清单只能发布稳定版本：{value}"));
    }
    Ok(version)
}

fn validate_manifest(manifest: &WindowsUpdateManifest) -> Result<Version, String> {
    if manifest.schema_version != 1 {
        return Err(format!(
            "不支持 Windows 更新清单版本 {}。",
            manifest.schema_version
        ));
    }
    let version = parse_stable_version(&manifest.version)?;
    if manifest.size == 0 || manifest.size > MAX_PACKAGE_SIZE {
        return Err("更新包大小超出允许范围。".to_owned());
    }
    if manifest.sha256.len() != 64
        || !manifest
            .sha256
            .bytes()
            .all(|value| value.is_ascii_hexdigit())
    {
        return Err("更新包 SHA-256 格式无效。".to_owned());
    }
    let url = Url::parse(&manifest.url).map_err(|_| "更新包 URL 无效。".to_owned())?;
    let expected_path = format!("{RELEASE_PREFIX}v{}/{ASSET_NAME}", manifest.version);
    if url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || url.path() != expected_path
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("更新包不是官方 GitHub Release 资源。".to_owned());
    }
    Ok(version)
}

async fn fetch_manifest() -> Result<(WindowsUpdateManifest, Version), String> {
    let response = http_client()?
        .get(MANIFEST_URL)
        .header(header::ACCEPT, "application/json")
        .header(header::CACHE_CONTROL, "no-cache")
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .map_err(|error| format!("检查更新失败：{error}"))?
        .error_for_status()
        .map_err(|error| format!("更新服务器返回错误：{error}"))?;
    let manifest = response
        .json::<WindowsUpdateManifest>()
        .await
        .map_err(|error| format!("无法读取 Windows 更新清单：{error}"))?;
    let version = validate_manifest(&manifest)?;
    Ok((manifest, version))
}

pub async fn check(current: &str) -> Result<UpdateCheck, String> {
    let current_version = parse_stable_version(current)?;
    let (manifest, latest_version) = fetch_manifest().await?;
    Ok(UpdateCheck {
        available: update_available(&current_version, &latest_version),
        current_version: current.to_owned(),
        latest_version: manifest.version,
    })
}

fn update_available(current: &Version, latest: &Version) -> bool {
    latest > current
}

fn emit_progress(app: &AppHandle, phase: &'static str, downloaded: u64, total: u64) {
    let _ = app.emit(
        "update-progress",
        UpdateProgress {
            phase,
            downloaded,
            total,
        },
    );
}

fn verify_payload(payload: &[u8], manifest: &WindowsUpdateManifest) -> Result<(), String> {
    if payload.len() as u64 != manifest.size {
        return Err(format!(
            "更新包大小不匹配：预期 {} 字节，实际 {} 字节。",
            manifest.size,
            payload.len()
        ));
    }
    let digest = format!("{:x}", Sha256::digest(payload));
    if !digest.eq_ignore_ascii_case(&manifest.sha256) {
        return Err("更新包 SHA-256 校验失败，已取消安装。".to_owned());
    }
    Ok(())
}

fn safe_archive_path(name: &str) -> Option<PathBuf> {
    let path = Path::new(name);
    if path.is_absolute() || name.contains('\\') {
        return None;
    }
    let mut safe = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => safe.push(value),
            _ => return None,
        }
    }
    (!safe.as_os_str().is_empty()).then_some(safe)
}

fn package_version(common_script: &Path) -> Result<String, String> {
    let source = fs::read_to_string(common_script)
        .map_err(|error| format!("无法读取更新包版本：{error}"))?;
    let prefix = "$PackageVersion = \"";
    source
        .lines()
        .find_map(|line| {
            line.strip_prefix(prefix)
                .and_then(|rest| rest.split_once('"'))
                .map(|(value, _)| value.to_owned())
        })
        .ok_or_else(|| "更新包缺少版本标记。".to_owned())
}

fn extract_package(payload: Vec<u8>, destination: &Path, version: &str) -> Result<(), String> {
    let mut archive = ZipArchive::new(Cursor::new(payload))
        .map_err(|error| format!("无法打开 Windows 更新包：{error}"))?;
    let mut files = HashSet::new();
    let mut extracted_size = 0u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| format!("无法读取更新包条目：{error}"))?;
        let safe = safe_archive_path(entry.name())
            .ok_or_else(|| format!("更新包包含不安全路径：{}", entry.name()))?;
        let target = destination.join(&safe);
        if entry.is_dir() {
            fs::create_dir_all(&target).map_err(|error| error.to_string())?;
            continue;
        }
        extracted_size = extracted_size
            .checked_add(entry.size())
            .ok_or_else(|| "更新包解压大小溢出。".to_owned())?;
        if extracted_size > MAX_EXTRACTED_SIZE {
            return Err("更新包解压后超过允许大小。".to_owned());
        }
        if !files.insert(safe.to_string_lossy().replace('\\', "/")) {
            return Err(format!("更新包包含重复文件：{}", entry.name()));
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let mut output = File::create(&target).map_err(|error| error.to_string())?;
        io::copy(&mut entry, &mut output).map_err(|error| error.to_string())?;
        output.flush().map_err(|error| error.to_string())?;
    }
    for required in REQUIRED_FILES {
        if !files.contains(*required) {
            return Err(format!("Windows 更新包缺少 {required}。"));
        }
    }
    let executable = destination.join("zotero-pdf2zh-pro.exe");
    let mut magic = [0u8; 2];
    File::open(&executable)
        .and_then(|mut file| file.read_exact(&mut magic))
        .map_err(|error| format!("无法验证控制中心程序：{error}"))?;
    if magic != *b"MZ" {
        return Err("Windows 更新包中的控制中心不是有效 PE 文件。".to_owned());
    }
    let packaged_version = package_version(&destination.join("common.ps1"))?;
    if packaged_version != version {
        return Err(format!(
            "更新包版本 {packaged_version} 与清单版本 {version} 不一致。"
        ));
    }
    Ok(())
}

pub async fn download_and_stage(app: &AppHandle, app_root: &Path) -> Result<StagedUpdate, String> {
    emit_progress(app, "checking", 0, 0);
    let (manifest, latest_version) = fetch_manifest().await?;
    let current = parse_stable_version(env!("CARGO_PKG_VERSION"))?;
    if latest_version <= current {
        return Err("当前已经是最新稳定版本。".to_owned());
    }

    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let staging = app_root.join("updates").join(format!(
        "{}-{}-{nonce}",
        manifest.version,
        std::process::id()
    ));
    let extracted = staging.join("package");
    fs::create_dir_all(&extracted).map_err(|error| format!("无法创建更新目录：{error}"))?;

    let result = async {
        let mut response = http_client()?
            .get(&manifest.url)
            .timeout(Duration::from_secs(15 * 60))
            .send()
            .await
            .map_err(|error| format!("下载更新失败：{error}"))?
            .error_for_status()
            .map_err(|error| format!("更新下载返回错误：{error}"))?;
        if let Some(length) = response.content_length() {
            if length != manifest.size {
                return Err("更新服务器返回的文件大小与清单不一致。".to_owned());
            }
        }
        let mut payload = Vec::with_capacity(manifest.size as usize);
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|error| format!("读取更新数据失败：{error}"))?
        {
            if payload.len() as u64 + chunk.len() as u64 > manifest.size {
                return Err("下载的更新包超过清单声明大小。".to_owned());
            }
            payload.extend_from_slice(&chunk);
            emit_progress(app, "downloading", payload.len() as u64, manifest.size);
        }
        emit_progress(app, "verifying", manifest.size, manifest.size);
        verify_payload(&payload, &manifest)?;
        extract_package(payload, &extracted, &manifest.version)?;
        emit_progress(app, "ready", manifest.size, manifest.size);
        Ok(StagedUpdate {
            directory: staging.clone(),
            apply_script: extracted.join("apply-update.ps1"),
            version: manifest.version.clone(),
        })
    }
    .await;
    if result.is_err() {
        let _ = fs::remove_dir_all(&staging);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

    fn manifest(version: &str, url: &str) -> WindowsUpdateManifest {
        WindowsUpdateManifest {
            schema_version: 1,
            version: version.to_owned(),
            url: url.to_owned(),
            size: 3,
            sha256: "a".repeat(64),
        }
    }

    fn zip_bytes(entries: Vec<(&str, Vec<u8>)>) -> Vec<u8> {
        let mut writer = ZipWriter::new(Cursor::new(Vec::new()));
        let options = SimpleFileOptions::default().compression_method(CompressionMethod::Stored);
        for (name, contents) in entries {
            writer.start_file(name, options).unwrap();
            writer.write_all(&contents).unwrap();
        }
        writer.finish().unwrap().into_inner()
    }

    fn destination(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "zotero-pdf2zh-pro-update-test-{}-{name}",
            std::process::id()
        ))
    }

    #[test]
    fn accepts_only_official_stable_release_assets() {
        let valid = manifest(
            "1.4.0",
            "https://github.com/study-233/zotero-pdf2zh-pro/releases/download/v1.4.0/zotero-pdf2zh-pro-windows-x64.zip",
        );
        assert_eq!(validate_manifest(&valid).unwrap(), Version::new(1, 4, 0));

        for (version, url) in [
            ("1.4.0-beta.1", valid.url.as_str()),
            ("not-a-version", valid.url.as_str()),
            ("1.4.0", "https://example.com/update.zip"),
            ("1.4.0", "http://github.com/study-233/zotero-pdf2zh-pro/releases/download/v1.4.0/zotero-pdf2zh-pro-windows-x64.zip"),
            ("1.4.0", "https://github.com/study-233/zotero-pdf2zh-pro/releases/download/v1.5.0/zotero-pdf2zh-pro-windows-x64.zip"),
        ] {
            assert!(validate_manifest(&manifest(version, url)).is_err());
        }
    }

    #[test]
    fn rejects_unsafe_archive_paths() {
        for path in ["../evil", "/absolute", "C:/windows", "folder\\evil"] {
            assert!(safe_archive_path(path).is_none(), "accepted {path}");
        }
        assert_eq!(
            safe_archive_path("install.ps1"),
            Some(PathBuf::from("install.ps1"))
        );

        let target = destination("unsafe-path");
        let _ = fs::remove_dir_all(&target);
        fs::create_dir_all(&target).unwrap();
        let result = extract_package(
            zip_bytes(vec![("../evil", b"bad".to_vec())]),
            &target,
            "1.4.0",
        );
        assert!(result.is_err());
        let _ = fs::remove_dir_all(target);
    }

    #[test]
    fn rejects_incomplete_or_mismatched_packages() {
        let target = destination("invalid-package");
        let _ = fs::remove_dir_all(&target);
        fs::create_dir_all(&target).unwrap();
        assert!(extract_package(
            zip_bytes(vec![(
                "common.ps1",
                b"$PackageVersion = \"1.4.0\"\n".to_vec()
            )]),
            &target,
            "1.4.0",
        )
        .is_err());
        let _ = fs::remove_dir_all(&target);
        fs::create_dir_all(&target).unwrap();

        let entries = REQUIRED_FILES
            .iter()
            .map(|name| {
                let contents = match *name {
                    "zotero-pdf2zh-pro.exe" => b"MZ".to_vec(),
                    "common.ps1" => b"$PackageVersion = \"1.5.0\"\n".to_vec(),
                    _ => b"script".to_vec(),
                };
                (*name, contents)
            })
            .collect();
        assert!(extract_package(zip_bytes(entries), &target, "1.4.0").is_err());
        let _ = fs::remove_dir_all(target);
    }

    #[test]
    fn rejects_size_and_hash_mismatches() {
        let mut value = manifest(
            "1.4.0",
            "https://github.com/study-233/zotero-pdf2zh-pro/releases/download/v1.4.0/zotero-pdf2zh-pro-windows-x64.zip",
        );
        assert!(verify_payload(b"abc", &value).is_err());
        value.sha256 = format!("{:x}", Sha256::digest(b"abc"));
        assert!(verify_payload(b"abc", &value).is_ok());
        value.size = 4;
        assert!(verify_payload(b"abc", &value).is_err());
    }

    #[test]
    fn compares_current_and_latest_versions() {
        let current = Version::parse("1.3.0").unwrap();
        assert!(update_available(
            &current,
            &Version::parse("1.4.0").unwrap()
        ));
        assert!(!update_available(
            &current,
            &Version::parse("1.3.0").unwrap()
        ));
        assert!(!update_available(
            &current,
            &Version::parse("1.2.0").unwrap()
        ));
    }
}
