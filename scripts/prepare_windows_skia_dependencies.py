"""Prepare only the pinned Skia dependencies required by the Windows renderer."""
import ast
import os
from pathlib import Path
import subprocess
import sys

REQUIRED = {'brotli', 'd3d12allocator', 'expat', 'libjpeg-turbo', 'libpng', 'wuffs', 'zlib', 'spirv-cross'}

def prepare(root: Path):
    root = root.resolve(strict=True)
    tree = ast.parse((root / 'DEPS').read_text(encoding='utf-8'))
    deps = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'deps' for t in node.targets):
            for key, value in zip(node.value.keys, node.value.values):
                name = ast.literal_eval(key)
                if name.startswith('third_party/externals/') and Path(name).name in REQUIRED:
                    deps[name] = ast.literal_eval(value)
    if {Path(name).name for name in deps} != REQUIRED:
        raise ValueError('Skia DEPS does not contain the required pinned sources')
    subset = root / 'DEPS.windows-controller'
    subset.write_text('deps = ' + repr(deps) + '\n', encoding='utf-8')
    environment = dict(os.environ, GIT_SYNC_DEPS_PATH=str(subset), GIT_SYNC_DEPS_SKIP_EMSDK='1')
    if not (root / 'bin/gn.exe').is_file():
        subprocess.run([sys.executable, 'bin/fetch-gn'], cwd=root, env=environment, check=True)
    subprocess.run([sys.executable, 'tools/git-sync-deps', '--shallow'], cwd=root, env=environment, check=True)
    for relative, source in deps.items():
        expected = source.rsplit('@', 1)[1]
        actual = subprocess.check_output(['git', '-C', str(root / relative), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != expected:
            raise ValueError(f'Skia dependency revision mismatch: {relative}')

if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
