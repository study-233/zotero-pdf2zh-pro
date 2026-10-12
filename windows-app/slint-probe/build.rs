use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    slint_build::compile_with_config(
        "ui/probe.slint",
        slint_build::CompilerConfiguration::new().with_style("fluent".into()),
    )
    .unwrap();
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .parent()
        .unwrap()
        .to_path_buf();
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let escape = |path: PathBuf| path.to_string_lossy().replace('\\', "\\\\");
    let rc = format!(
        "32512 ICON \"{}\"\n1 24 \"{}\"\n1 VERSIONINFO\nFILEVERSION 1,8,2,0\nPRODUCTVERSION 1,8,2,0\nFILEOS 0x40004\nFILETYPE 1\nBEGIN\nBLOCK \"StringFileInfo\"\nBEGIN\nBLOCK \"040904B0\"\nBEGIN\nVALUE \"FileDescription\", \"pdf2zh Slint Framework Probe\\0\"\nVALUE \"FileVersion\", \"1.8.2\\0\"\nEND\nEND\nBLOCK \"VarFileInfo\"\nBEGIN\nVALUE \"Translation\", 0x409, 1200\nEND\nEND\n",
        escape(root.join("icons/icon.ico")),
        escape(root.join("app.manifest"))
    );
    fs::write(out.join("probe.rc"), rc).unwrap();
    let kits = PathBuf::from(env::var_os("ProgramFiles(x86)").unwrap()).join("Windows Kits/10/bin");
    let mut paths: Vec<_> = fs::read_dir(kits)
        .unwrap()
        .filter_map(Result::ok)
        .map(|e| e.path())
        .collect();
    paths.sort();
    let compiler = paths
        .into_iter()
        .rev()
        .map(|p| p.join("x64/rc.exe"))
        .find(|p| p.is_file())
        .unwrap();
    let resource = out.join("probe.res");
    assert!(
        Command::new(compiler)
            .arg("/nologo")
            .arg("/fo")
            .arg(&resource)
            .arg(out.join("probe.rc"))
            .status()
            .unwrap()
            .success()
    );
    println!("cargo:rustc-link-arg={}", resource.display());
    println!("cargo:rerun-if-changed=../icons/icon.ico");
    println!("cargo:rerun-if-changed=../app.manifest");
}
