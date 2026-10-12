"""Create an isolated, offline Windows fixture from the development cache."""
from pathlib import Path
import os
import shutil
import sys

root = Path(sys.argv[1]).resolve()
repo = Path(__file__).resolve().parents[1]
if not root.is_relative_to(repo / ".tmp"):
    raise ValueError("The fixture must be inside the repository .tmp directory")
root.mkdir(parents=True, exist_ok=True)

def copy_tree(source, target, immutable=False):
    for directory, _, names in os.walk(source, followlinks=True):
        relative = Path(directory).relative_to(source)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            path, result = Path(directory) / name, destination / name
            if result.exists():
                continue
            if immutable or "archive-v0" in relative.parts:
                try:
                    os.link(path, result)
                    continue
                except OSError:
                    pass
            shutil.copy2(path, result)

development = repo / ".local-dev/runtime"
copy_tree(development / "uv-cache", root / "cache")
copy_tree(development / "python/cpython-3.13.15-windows-x86_64-none",
          root / "runtime/python/cpython-3.13.15-windows-x86_64-none", True)
(root / "runtime/uv").mkdir(parents=True, exist_ok=True)
shutil.copy2(development / "tools/uv.exe", root / "runtime/uv/uv.exe")
print(root)
