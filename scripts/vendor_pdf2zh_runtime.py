#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"



@dataclass(frozen=True)
class WheelSnapshot:
    name: str
    version: str
    package: str
    url: str
    sha256: str
    license_member: str | None
    license_name: str
    excludes: tuple[str, ...] = ()


SNAPSHOTS = (
    WheelSnapshot(
        name="pdf2zh-next",
        version="2.8.2",
        package="pdf2zh_next",
        url=(
            "https://files.pythonhosted.org/packages/2a/f5/"
            "897dbd72b2875411540502ea74e460dc222d8085f1699b2c2972acf3f3cc/"
            "pdf2zh_next-2.8.2-py3-none-any.whl"
        ),
        sha256="5416f8e65828783df9a2323893380145d30846ed7c201539f847307b8689b770",
        license_member="pdf2zh_next-2.8.2.dist-info/licenses/LICENSE",
        license_name="pdf2zh-next-AGPL-3.0.txt",
        excludes=(
            "assets",
            "gui.py",
            "gui_translation.yaml",
            "http_api.py",
            "i18n.py",
            "main.py",
        ),
    ),
    WheelSnapshot(
        name="BabelDOC",
        version="0.6.4",
        package="babeldoc",
        url=(
            "https://files.pythonhosted.org/packages/3e/b1/"
            "7036b4a5ec6fda008e161950ac619a6e989730a05f3a90fcf1e437f07dec/"
            "babeldoc-0.6.4-py3-none-any.whl"
        ),
        sha256="e7dcdd5b8213f657af1df68e329f92d0534c9b94c53fcd82e9e04c52060cb7d0",
        license_member="babeldoc-0.6.4.dist-info/licenses/LICENSE",
        license_name="BabelDOC-AGPL-3.0.txt",
        excludes=("tools",),
    ),
)


def download(url: str, expected_sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"SHA256 mismatch for {url}: {actual_sha256} != {expected_sha256}"
        )
    return data


def should_extract(snapshot: WheelSnapshot, member_name: str) -> bool:
    prefix = f"{snapshot.package}/"
    if not member_name.startswith(prefix) or member_name.endswith("/"):
        return False
    relative = member_name.removeprefix(prefix)
    return not any(
        relative == excluded or relative.startswith(f"{excluded}/")
        for excluded in snapshot.excludes
    )


def extract_snapshot(snapshot: WheelSnapshot, wheel_path: Path, staging: Path) -> None:
    """Extract an upstream reference into a new directory, never into maintained code."""
    staging = staging.resolve()
    source = SERVER_DIR.resolve()
    if staging.is_relative_to(source) or source.is_relative_to(staging):
        raise ValueError("Snapshot staging must be separate from maintained server sources")
    # Refuse reuse; never remove an existing package or license directory.
    staging.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(wheel_path) as archive:
        members = []
        for member in archive.infolist():
            if not should_extract(snapshot, member.filename):
                continue
            name = PurePosixPath(member.filename)
            target = staging / member.filename
            if ("\\" in member.filename or ":" in member.filename or ".." in name.parts
                    or not target.resolve().is_relative_to(staging / snapshot.package)):
                raise ValueError(f"Unsafe wheel member: {member.filename}")
            members.append((member, target))
        for member, target in members:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
        if snapshot.license_member:
            licenses = staging / "LICENSES"
            licenses.mkdir()
            (licenses / snapshot.license_name).write_bytes(archive.read(snapshot.license_member))
    if not (staging / snapshot.package / "__init__.py").is_file():
        raise RuntimeError(f"Missing package after extraction: {snapshot.package}")


def main() -> None:
    workspace = Workspace(REPO_ROOT)
    work = workspace.create("vendor-snapshot", "run")
    (work / ".keep").write_text("Upstream reference for manual merge; never copy over pdf2zh_pro.\n", encoding="utf-8")
    status = "failed"
    try:
        for snapshot in SNAPSHOTS:
            wheel_path = work / Path(snapshot.url).name
            wheel_path.write_bytes(download(snapshot.url, snapshot.sha256))
            staging = work / f"{snapshot.package}-{snapshot.version}"
            extract_snapshot(snapshot, wheel_path, staging)
            print(f"Staged {snapshot.name}=={snapshot.version}: {staging}")
        print("Maintained sources are unchanged. Review vendor-manifest.json and merge explicitly.")
        status = "passed"
    finally:
        workspace.finish(work, status)


if __name__ == "__main__":
    main()
