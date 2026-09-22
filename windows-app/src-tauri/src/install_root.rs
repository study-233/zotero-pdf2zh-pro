use std::{
    env,
    path::{Path, PathBuf},
};
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

const INSTALL_REGISTRY_KEY: &str = r"Software\zotero-pdf2zh-pro";

pub struct InstallRoot {
    pub root: PathBuf,
    pub source: &'static str,
    pub registry_diagnostic: String,
}

fn registry_subkey(override_key: Option<&str>) -> String {
    let value = override_key.filter(|value| !value.trim().is_empty());
    let value = value.unwrap_or(INSTALL_REGISTRY_KEY);
    if value
        .get(..6)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("HKCU:\\"))
    {
        value[6..].to_owned()
    } else {
        value.to_owned()
    }
}

fn saved_install_root(subkey: &str) -> Result<Option<PathBuf>, String> {
    let key = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(subkey)
        .map_err(|error| format!("open: {error}; win32={:?}", error.raw_os_error()))?;
    let value: String = key.get_value("InstallRoot").map_err(|error| {
        format!(
            "read InstallRoot: {error}; win32={:?}",
            error.raw_os_error()
        )
    })?;
    parse_saved_root(value)
}

fn parse_saved_root(value: String) -> Result<Option<PathBuf>, String> {
    if value.trim().is_empty() {
        return Ok(None);
    }
    let root = PathBuf::from(value);
    if !root.is_absolute() {
        return Err(format!(
            "InstallRoot is not an absolute path: {}",
            root.display()
        ));
    }
    Ok(Some(root))
}

fn is_installation(root: &Path) -> bool {
    root.join("bin")
        .join(format!("{}.exe", crate::PRODUCT_NAME))
        .is_file()
        && root.join("bin/common.ps1").is_file()
        && root.join("installed-version.txt").is_file()
}

fn executable_install_root(executable: &Path) -> Option<PathBuf> {
    let bin = executable.parent()?;
    if !bin
        .file_name()?
        .to_string_lossy()
        .eq_ignore_ascii_case("bin")
    {
        return None;
    }
    let root = bin.parent()?;
    let expected = root
        .join("bin")
        .join(format!("{}.exe", crate::PRODUCT_NAME));
    (is_installation(root) && crate::path_equal(executable, &expected)).then(|| root.to_owned())
}

fn select_root(
    explicit: Option<&Path>,
    saved: Option<&Path>,
    executable: Option<&Path>,
    default: &Path,
) -> (PathBuf, &'static str) {
    if let Some(root) =
        explicit.filter(|root| !root.as_os_str().to_string_lossy().trim().is_empty())
    {
        return (root.to_owned(), "environment");
    }
    if let Some(root) = saved.filter(|root| is_installation(root)) {
        return (root.to_owned(), "registered-installation");
    }
    if let Some(root) = executable.and_then(executable_install_root) {
        return (root, "executable-installation");
    }
    if let Some(root) = saved {
        return (root.to_owned(), "saved-location");
    }
    (default.to_owned(), "default")
}

pub fn discover() -> Result<InstallRoot, String> {
    let explicit = env::var_os("PDF2ZH_WINDOWS_APP_ROOT").map(PathBuf::from);
    let subkey = registry_subkey(env::var("PDF2ZH_WINDOWS_REGISTRY_KEY").ok().as_deref());
    let saved = saved_install_root(&subkey);
    let registry_diagnostic = match &saved {
        Ok(Some(root)) => format!(
            "registry=HKCU\\{subkey}; saved={}; valid={}",
            root.display(),
            is_installation(root)
        ),
        Ok(None) => format!("registry=HKCU\\{subkey}; saved=<empty>"),
        Err(error) => format!("registry=HKCU\\{subkey}; {error}"),
    };
    let executable = env::current_exe().ok();
    let (root, source) = select_root(
        explicit.as_deref(),
        saved.as_ref().ok().and_then(|root| root.as_deref()),
        executable.as_deref(),
        &crate::default_install_root()?,
    );
    Ok(InstallRoot {
        root,
        source,
        registry_diagnostic,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            let root = env::temp_dir().join(format!(
                "pdf2zh-root-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }

        fn install(&self, name: &str) -> PathBuf {
            let root = self.0.join(name);
            fs::create_dir_all(root.join("bin")).unwrap();
            for file in [
                "bin/zotero-pdf2zh-pro.exe",
                "bin/common.ps1",
                "installed-version.txt",
            ] {
                fs::write(root.join(file), "").unwrap();
            }
            root
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn recovers_local_installation_without_readable_or_valid_registration() {
        let fixture = Fixture::new();
        let local = fixture.install("D drive 安装 with spaces");
        let exe = local.join("bin/zotero-pdf2zh-pro.exe");
        let default = fixture.0.join("default");
        fs::create_dir_all(default.join("logs")).unwrap();
        for saved in [None, Some(default.as_path())] {
            assert_eq!(
                select_root(None, saved, Some(&exe), &default),
                (local.clone(), "executable-installation")
            );
        }
        assert_eq!(
            select_root(Some(Path::new("")), None, Some(&exe), &default).0,
            local
        );
    }

    #[test]
    fn explicit_override_and_registered_installation_keep_priority() {
        let fixture = Fixture::new();
        let local = fixture.install("old installation");
        let registered = fixture.install("registered installation");
        let explicit = fixture.0.join("explicit target");
        let exe = local.join("bin/zotero-pdf2zh-pro.exe");
        assert_eq!(
            select_root(Some(&explicit), Some(&registered), Some(&exe), &fixture.0),
            (explicit, "environment")
        );
        assert_eq!(
            select_root(None, Some(&registered), Some(&exe), &fixture.0),
            (registered, "registered-installation")
        );
    }

    #[test]
    fn preserves_custom_reinstall_location_and_default_for_new_install() {
        let fixture = Fixture::new();
        let saved = fixture.0.join("previous install");
        assert_eq!(
            select_root(None, Some(&saved), None, &fixture.0),
            (saved, "saved-location")
        );
        assert_eq!(
            select_root(None, None, None, &fixture.0),
            (fixture.0.clone(), "default")
        );
    }

    #[test]
    fn rejects_packages_staging_and_missing_markers() {
        let fixture = Fixture::new();
        for directory in ["zip", "updates/id/package", "logs only"] {
            let root = fixture.0.join(directory);
            fs::create_dir_all(&root).unwrap();
            for file in [
                "zotero-pdf2zh-pro.exe",
                "common.ps1",
                "installed-version.txt",
            ] {
                fs::write(root.join(file), "").unwrap();
            }
            assert!(executable_install_root(&root.join("zotero-pdf2zh-pro.exe")).is_none());
        }
        for missing in ["bin/common.ps1", "installed-version.txt"] {
            let root = fixture.install(&missing.replace('/', "-"));
            fs::remove_file(root.join(missing)).unwrap();
            assert!(executable_install_root(&root.join("bin/zotero-pdf2zh-pro.exe")).is_none());
        }
    }

    #[test]
    fn recognizes_case_variations_but_not_another_executable() {
        let fixture = Fixture::new();
        let root = fixture.install("installation");
        let exe = root.join("BIN/ZOTERO-PDF2ZH-PRO.EXE");
        assert_eq!(executable_install_root(&exe), Some(root.clone()));
        fs::write(root.join("installed-version.txt"), "malformed version").unwrap();
        assert!(is_installation(&root));
        fs::write(root.join("bin/other.exe"), "").unwrap();
        assert!(executable_install_root(&root.join("bin/other.exe")).is_none());
    }

    #[test]
    fn registry_override_accepts_powershell_prefix_case_insensitively() {
        assert_eq!(registry_subkey(None), INSTALL_REGISTRY_KEY);
        assert_eq!(registry_subkey(Some("")), INSTALL_REGISTRY_KEY);
        assert_eq!(
            registry_subkey(Some(r"hkcu:\Software\test")),
            r"Software\test"
        );
    }

    #[test]
    fn unreadable_registration_retains_the_native_error_and_allows_local_recovery() {
        let fixture = Fixture::new();
        let local = fixture.install("registered key missing");
        let subkey = format!(
            r"Software\{}",
            fixture.0.file_name().unwrap().to_string_lossy()
        );
        let saved = saved_install_root(&subkey);
        assert!(saved.as_ref().unwrap_err().contains("win32=Some(2)"));
        let exe = local.join("bin/zotero-pdf2zh-pro.exe");
        assert_eq!(
            select_root(
                None,
                saved.ok().flatten().as_deref(),
                Some(&exe),
                &fixture.0
            )
            .0,
            local
        );
    }

    #[test]
    fn rejects_working_directory_dependent_registration() {
        for value in [r"relative", r"D:relative", r"\rooted"] {
            assert!(parse_saved_root(value.to_owned()).is_err());
        }
        assert_eq!(parse_saved_root("  ".to_owned()).unwrap(), None);
        assert_eq!(
            parse_saved_root(r"D:\Apps\翻译".to_owned()).unwrap(),
            Some(PathBuf::from(r"D:\Apps\翻译"))
        );
    }
}
