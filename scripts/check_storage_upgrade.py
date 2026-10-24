"""Exercise real HTTP service restarts/upgrades in a disposable Homebrew layout.

Use --download to download the public computing glossary through the service.
No installed service, Zotero profile or model API is modified or used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from workspace import ROOT, Workspace


def check(download=False, wheel=None):
    with Workspace().run("storage-http-upgrade") as (work, report):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "server"),
               "PDF2ZH_CONFIG_DIR": str(work / "config"),
               "PDF2ZH_TRANSLATION_CACHE_DIR": str(work / "cache")}
        for key in ("PDF2ZH_DATA_DIR", "PDF2ZH_CHATGPT_AUTH_DIR", "PDF2ZH_LOG_FILE"):
            env.pop(key, None)
        cellar = work / "brew/Cellar/zotero-pdf2zh-pro"
        persistent = work / "brew/var/zotero-pdf2zh-pro"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        processes = []
        logs = []
        evidence = {"publicDownload": download, "checks": []}

        def request(route, body=None, raw=False):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + route, data=data, headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=5) as response:
                return response.read() if raw else json.load(response)

        def install(version):
            package = cellar / version / "libexec/venv/lib/python3.13/site-packages"
            package.mkdir(parents=True)
            if wheel is not None:
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(package)
            else:
                for name in ("server.py", "service_launcher.py", "runtime_storage.py"):
                    shutil.copy2(ROOT / "server" / name, package / name)
            return package

        def start(package, explicit=None):
            log = (report / f"server-{len(logs)}.log").open("wb")
            logs.append(log)
            command = [sys.executable, str(package / "service_launcher.py"), "--port", str(port)]
            if explicit is not None:
                command += ["--data-dir", str(explicit)]
            proc = subprocess.Popen(command, env=env, cwd=work, stdout=log, stderr=log)
            processes.append(proc)
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError(f"Service exited; see {log.name}")
                try:
                    return proc, request("/health")
                except (OSError, ValueError):
                    time.sleep(0.1)
            raise RuntimeError("Isolated service did not become healthy")

        def stop(proc):
            proc.terminate()
            proc.wait(timeout=30)

        def fixture(action, data):
            subprocess.run([sys.executable, str(ROOT / "scripts/release-data-fixtures.py"), action, str(data)],
                           env=env, cwd=work, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        try:
            old = install("1.9.3")
            legacy = old / "translates"
            proc, health = start(old, legacy)
            assert Path(health["workspace"]["path"]) == legacy
            sha = None
            if download:
                request("/glossaries/computing/download", {})
                for _ in range(300):
                    pack = next(p for p in request("/glossaries")["packs"] if p["id"] == "computing")
                    if pack["status"] == "installed":
                        sha = pack["sha256"]
                        evidence["downloadedEntries"] = pack["entryCount"]
                        break
                    if pack["status"] == "failed":
                        raise RuntimeError(pack.get("download", {}).get("error", "Download failed"))
                    time.sleep(0.1)
                assert sha, "Download did not complete"
            stop(proc)
            fixture("seed", legacy)
            (legacy / "upgrade-settings.json").write_text('{"keep":true}', encoding="utf-8")
            expected_pdf = b"%PDF-1.4\n% synthetic preserved result\n%%EOF\n"
            result = legacy / "release-compatibility-task/result.pdf"
            result.parent.mkdir()
            result.write_bytes(expected_pdf)
            task_file = legacy / "tasks.json"
            tasks = json.loads(task_file.read_text(encoding="utf-8"))
            task = tasks["tasks"][0]
            task["result_files"] = {"dual": {"output_path": str(result), "filename": "result.pdf"}}
            task_file.write_text(json.dumps(tasks), encoding="utf-8")
            before = {path.relative_to(legacy): path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
            for version in ("1.9.4", "1.9.5"):
                proc, health = start(install(version))
                assert Path(health["workspace"]["path"]) == persistent
                packs = request("/glossaries")["packs"]
                computing = next(pack for pack in packs if pack["id"] == "computing")
                assert computing["installedVersions"], "Downloaded glossary became unavailable"
                if sha:
                    assert computing["status"] == "installed"
                    assert any(row["sha256"] == sha for row in computing["installedVersions"])
                assert any(t["taskId"] == "release-compatibility-task" for t in request("/tasks")["tasks"])
                assert (persistent / "upgrade-settings.json").read_text() == '{"keep":true}'
                migrated = json.loads((persistent / "tasks.json").read_text())["tasks"][0]
                assert Path(migrated["result_files"]["dual"]["output_path"]).read_bytes() == expected_pdf
                stop(proc)
                fixture("verify", persistent)
                evidence["checks"].append({"versionDirectory": version, "glossaryStatus": computing["status"],
                                           "taskAndResult": "preserved", "settingsAndMemory": "preserved"})
                if legacy.exists():
                    assert all((legacy / path).read_bytes() == content for path, content in before.items())
                shutil.rmtree(cellar)  # Homebrew cleanup must not remove any current data.
            evidence["passed"] = True
            (report / "validation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            print(f"Report: {report}")
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
            for log in logs:
                log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--wheel", type=Path, help="Use a locally built wheel for each simulated installation")
    args = parser.parse_args()
    check(args.download, args.wheel)
