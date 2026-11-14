"""Run the three native macOS Zotero acceptance suites against the current checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from workspace import ROOT


ZOTERO = Path("/Applications/Zotero.app/Contents/MacOS/zotero")
SUITES = ("zotero_import_smoke.py", "zotero_upload_smoke.py", "zotero_reliability_smoke.py")


def write_profile(profile, library, overrides=None):
    prefs = {
        "extensions.zotero.dataDir": str(library),
        "extensions.zotero.useDataDir": True,
        "extensions.zotero.firstRun2": False,
        "extensions.zotero.sync.autoSync": False,
        "extensions.zotero.automaticScraperUpdates": False,
        "extensions.update.enabled": False,
        "extensions.update.autoUpdateDefault": False,
        "extensions.autoDisableScopes": 0,
        "extensions.enabledScopes": 15,
        "extensions.startupScanScopes": 15,
        "browser.shell.checkDefaultBrowser": False,
        "toolkit.telemetry.enabled": False,
        **(overrides or {}),
    }
    # Replace phase-specific overrides so the next launch can use persisted values.
    (profile / "user.js").write_text("".join(
        f"user_pref({json.dumps(key)}, {json.dumps(value)});\n" for key, value in prefs.items()
    ), encoding="utf-8")


def create_test_pdf(path, text):
    import pymupdf

    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), text)
        document.save(path)


def bundle_module(exports):
    """Bundle the real plugin modules and their transitive dependencies with the installed scaffold."""
    build = """
        const {createRequire} = require('node:module');
        const scaffold = createRequire(require.resolve('./node_modules/zotero-plugin-scaffold/package.json'));
        const result = scaffold('esbuild').buildSync({
            stdin: {contents: process.argv[1], resolveDir: process.cwd()},
            bundle: true, write: false, format: 'iife', globalName: 'smokeModule', target: 'firefox115',
        });
        process.stdout.write(result.outputFiles[0].contents);
    """
    return subprocess.check_output(["node", "-e", build, exports], cwd=ROOT / "plugin")


def run_phase(work, report, log_name, result_name):
    command = [ZOTERO, "-no-remote", "-profile", work / "profile", "--dataDir", work / "library"]
    with (report / log_name).open("w") as log:
        process = subprocess.run(command, env={**os.environ, "MOZ_NO_REMOTE": "1"},
                                 stdout=log, stderr=subprocess.STDOUT, timeout=90)
    failure = report / "failure.json"
    if failure.is_file():
        raise RuntimeError(failure.read_text(encoding="utf-8"))
    process.check_returncode()
    result = json.loads((report / result_name).read_text(encoding="utf-8"))
    if result["passed"] is not True:
        raise RuntimeError(f"Acceptance failed: {report / result_name}")
    checks = result.get("results", result.get("checks"))
    print(f"{result_name}: {len(checks)} checks passed (Zotero {result['version']})", flush=True)


def main():
    python = ROOT / "server/.venv/bin/python"
    if not ZOTERO.is_file():
        sys.exit(f"找不到 macOS Zotero 程序：{ZOTERO}")
    if not python.is_file():
        sys.exit("请先运行 uv sync --directory server --locked 准备验收依赖。")
    if not shutil.which("pnpm"):
        sys.exit("构建插件需要 pnpm，请先安装并加入 PATH。")

    # The reliability suite installs the production XPI built from this checkout.
    env = {**os.environ, "PDF2ZH_DEV_RUNTIME": ""}
    subprocess.run(["pnpm", "run", "build"], cwd=ROOT / "plugin", env=env, check=True)
    for name in SUITES:
        print(f"运行 {name}", flush=True)
        subprocess.run([python, ROOT / "scripts" / name], cwd=ROOT, env=env, check=True)
    print("三套 Zotero 原生验收均已通过。")


if __name__ == "__main__":
    main()
