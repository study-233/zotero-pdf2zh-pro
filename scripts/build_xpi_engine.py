"""Bundle the maintained service and its frozen dependency recipe in the universal XPI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'
PYTHON = '3.13.15'
UV = '0.13.6'
# Official uv wheels contain one portable executable. Zotero verifies the whole
# wheel before extracting that single member, without a system Python or shell.
BOOTSTRAP = {
    'windows-x64': ('7c/9d/714523188aea3054644d8e69ae8089aac70325a3cee2d3d05509d4f23398/uv-0.13.6-py3-none-win_amd64.whl', '7c9bc35ccb65932d8dc10a0669f9c805eaa3c9827ce18ecbecb1bd67944b2cdf', 15334118, '81ea623608f8c17720c62efd0a380a8ead380c4d365ba6968e9d279a2319c674'),
    'macos-arm64': ('f4/14/77230cc4f6a73a31578cb8070a5f67f58a76946ead643ae8a4155620d6da/uv-0.13.6-py3-none-macosx_11_0_arm64.whl', '51361029b7cf91185d0092c4b356d110b4ad629e9cb11593f1e006ac72a72ca9', 14344205, '3996850e6d85e60b0142b40af4bd984822d65f736b52209921c47312450685cb'),
}


def build(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    config = tomllib.loads((SERVER / 'pyproject.toml').read_text('utf-8'))
    version = config['project']['version']
    files = [SERVER / (name + '.py') for name in config['tool']['setuptools']['py-modules']]
    files.extend(SERVER / name for name in ('pyproject.toml', 'uv.lock', 'README.md', 'THIRD_PARTY_NOTICES.md'))
    for folder in ('pdf2zh_pro', 'babeldoc', 'LICENSES'):
        files.extend(p for p in (SERVER / folder).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and not p.name.startswith('.') and p.suffix != '.pyc')
    controller = (ROOT / 'scripts/managed_engine.py').read_bytes().replace(b'\r\n', b'\n')
    (out / 'manager.py').write_bytes(controller)
    (out / 'homebrew_update.py').write_bytes((ROOT / 'scripts/homebrew_update.py').read_bytes().replace(b'\r\n', b'\n'))
    identity = hashlib.sha256(controller + PYTHON.encode() + UV.encode())
    payload = out / 'service.zip'
    with zipfile.ZipFile(payload, 'w') as archive:
        for p in sorted(files):
            name = p.relative_to(SERVER).as_posix()
            data = p.read_bytes()
            if p.suffix in {'.py', '.toml', '.lock', '.md', '.json', '.txt'}:
                data = data.replace(b'\r\n', b'\n')
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3  # Identical ZIP metadata on Windows and POSIX builders.
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
            # A coordinated UI-only release bumps the application version as well.
            # Active engines need replacing only if executable code or dependencies differ.
            if name.endswith('.md') or name.startswith('LICENSES/'):
                continue
            for marker in {'server.py': ['VERSION = '], 'pyproject.toml': ['\nversion = '],
                           'uv.lock': ['name = "zotero-pdf2zh-pro"\nversion = ']}.get(name, []):
                data = data.replace(f'{marker}"{version}"'.encode(), f'{marker}"@release"'.encode(), 1)
            identity.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    manifest = dict(id=identity.hexdigest(), python=PYTHON, uv=UV,
                    sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                    platforms={key: dict(url='https://files.pythonhosted.org/packages/' + path, sha256=sha, size=size, executableSha256=exe_sha)
                               for key, (path, sha, size, exe_sha) in BOOTSTRAP.items()})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', 'utf-8')


if __name__ == '__main__':
    build(Path(sys.argv[1]))
