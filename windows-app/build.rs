use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    slint_build::compile_with_config("ui/app.slint", slint_build::CompilerConfiguration::new().with_style("fluent".into())).expect("Slint UI compilation");
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let version = env::var("CARGO_PKG_VERSION").unwrap();
    let numeric = version.replace('.', ",");
    let manifest = root.join("app.manifest");
    let icon = root.join("icons/icon.ico");
    let escape = |path: &PathBuf| path.to_string_lossy().replace('\\', "\\\\");
    let rc = format!("32512 ICON \"{}\"\n1 24 \"{}\"\n1 VERSIONINFO\nFILEVERSION {numeric},0\nPRODUCTVERSION {numeric},0\nFILEOS 0x40004\nFILETYPE 1\nBEGIN\nBLOCK \"StringFileInfo\"\nBEGIN\nBLOCK \"040904B0\"\nBEGIN\nVALUE \"FileDescription\", \"zotero-pdf2zh-pro Native Control Center\\0\"\nVALUE \"FileVersion\", \"{version}\\0\"\nVALUE \"ProductName\", \"zotero-pdf2zh-pro\\0\"\nVALUE \"ProductVersion\", \"{version}\\0\"\nEND\nEND\nBLOCK \"VarFileInfo\"\nBEGIN\nVALUE \"Translation\", 0x409, 1200\nEND\nEND\n", escape(&icon), escape(&manifest));
    fs::write(out.join("app.rc"), rc).unwrap();
    let kits = PathBuf::from(
        env::var_os("ProgramFiles(x86)").unwrap_or_else(|| "C:/Program Files (x86)".into()),
    )
    .join("Windows Kits/10/bin");
    let mut versions: Vec<_> = fs::read_dir(kits)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect();
    versions.sort();
    let compiler = versions
        .into_iter()
        .rev()
        .map(|path| path.join("x64/rc.exe"))
        .find(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from("rc.exe"));
    let resource = out.join("app.res");
    assert!(Command::new(compiler)
        .arg("/nologo")
        .arg("/fo")
        .arg(&resource)
        .arg(out.join("app.rc"))
        .status()
        .expect("Windows SDK resource compiler")
        .success());
    println!("cargo:rustc-link-arg={}", resource.display());
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=app.manifest");
}
