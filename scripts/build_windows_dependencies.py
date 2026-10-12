"""Export the release lock, including hashes, without resolving new dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PYTHON = "3.13.15"


def export_dependencies(destination: Path, uv: str = "uv") -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [uv, "export", "--directory", str(ROOT / "server"), "--frozen", "--offline",
         "--no-dev", "--no-emit-project", "--no-header", "--no-annotate"],
        check=True, capture_output=True, encoding="utf-8",
    )
    requirements = result.stdout.replace("\r\n", "\n").encode("utf-8")
    if b"--hash=sha256:" not in requirements:
        raise ValueError("The Windows dependency export must contain hashes")
    lock = (ROOT / "server/uv.lock").read_bytes()
    version = tomllib.loads((ROOT / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    uv_path = Path(shutil.which(uv) or uv).resolve()
    uv_version = subprocess.check_output([str(uv_path), "--version"], text=True).split()[1]
    manifest = {"schemaVersion": 1, "version": version, "python": WINDOWS_PYTHON,
                "uvVersion": uv_version, "uvBinarySha256": hashlib.sha256(uv_path.read_bytes()).hexdigest(),
                "lockSha256": hashlib.sha256(lock).hexdigest(),
                "requirementsSha256": hashlib.sha256(requirements).hexdigest()}
    wheel = ROOT / "server/dist" / f"zotero_pdf2zh_pro-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise FileNotFoundError("Build the service wheel before the Windows package")
    manifest["serverWheel"] = {"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "size": wheel.stat().st_size}
    (destination / "windows-requirements.txt").write_bytes(requirements)
    (destination / "windows-dependencies.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    export_dependencies(args.output_dir, args.uv)
