"""Keep installed service data outside replaceable Python/version directories."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

PRODUCT = "zotero-pdf2zh-pro"


def _brew_cellar(module_file: str | Path) -> Path | None:
    for parent in Path(module_file).resolve().parents:
        if parent.name == PRODUCT and parent.parent.name == "Cellar":
            return parent
    return None


def default_data_directory(module_file: str | Path) -> Path:
    module = Path(module_file).resolve()
    cellar = _brew_cellar(module)
    if cellar is not None:
        return cellar.parent.parent / "var" / PRODUCT
    if module.parent.name not in {"site-packages", "dist-packages"}:
        # Source checkouts and the Docker volume keep their existing layout.
        return module.parent / "translates"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return (base / PRODUCT / "data").expanduser().resolve()
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return (base / PRODUCT).expanduser().resolve()


def _legacy_directories(module_file: str | Path) -> list[Path]:
    module = Path(module_file).resolve()
    candidates = {module.parent / "translates"}
    cellar = _brew_cellar(module)
    if cellar is not None:
        # An upgrade can leave the previous keg beside the new, empty one.
        for version in cellar.iterdir():
            candidates.add(version / "libexec/translates")
            try:
                libraries = list((version / "libexec/venv/lib").iterdir())
            except FileNotFoundError:
                continue
            candidates.update(path / "site-packages/translates" for path in libraries
                              if path.name.startswith("python"))
    return sorted(path for path in candidates if _has_data(path))


def _has_data(path: Path) -> bool:
    # pathlib glob can suppress permission errors; inaccessible data is not empty.
    if path.is_symlink() or path.is_junction():
        return True
    try:
        with os.scandir(path) as entries:
            return any(not entry.is_dir(follow_symlinks=False) or _has_data(Path(entry.path))
                       for entry in entries)
    except FileNotFoundError:
        return False


@contextmanager
def _migration_lock(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep the standalone Windows lock outside the installer's managed app root.
    lock = (target.parent.parent / f".{PRODUCT}-data.migration.lock"
            if target.name == "data" and target.parent.name == PRODUCT
            else target.parent / f".{target.name}.migration.lock")
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _manifest(root: Path) -> dict[str, str]:
    result = {}
    pending = [root]
    while pending:
        path = pending.pop()
        if path.is_symlink() or path.is_junction():
            raise OSError(f"Linked legacy data must be migrated manually: {path}")
        if path.is_dir():
            pending.extend(path.iterdir())
        elif path.is_file():
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        else:
            raise OSError(f"Unsupported legacy data file: {path}")
    return result


def _rebase_tasks(staging: Path, source: Path, target: Path):
    def relocate(container, key):
        value = container.get(key)
        if isinstance(value, str) and Path(value).is_absolute():
            try:
                relative = Path(value).relative_to(source)
            except ValueError:
                return
            container[key] = str(target / relative)

    for name in ("tasks.json", "tasks.json.bak"):
        path = staging / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise ValueError(f"Invalid legacy task store: {name}")
        for task in data["tasks"]:
            relocate(task, "workspace_dir")
            request = task.get("request_payload", {})
            for key in ("input_path", "output_dir", "translation_memory_path"):
                relocate(request, key)
            for output in (task.get("result_files") or {}).values():
                relocate(output, "output_path")
        backup = staging / "migration-backup" / name
        backup.parent.mkdir(exist_ok=True, mode=0o700)
        shutil.copy2(path, backup)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_data_directory(module_file: str | Path, requested: str | Path | None = None) -> Path:
    explicit = requested or os.environ.get("PDF2ZH_DATA_DIR")
    if explicit:
        # Windows installer, Docker overrides and development isolation own this path.
        return Path(explicit).expanduser().resolve()
    target = default_data_directory(module_file)
    legacy = Path(module_file).resolve().parent / "translates"
    if target == legacy:
        return target
    with _migration_lock(target):
        if target.exists() and any(target.iterdir()):
            return target  # Never overwrite current data or resurrect removed packs.
        sources = _legacy_directories(module_file)
        if len(sources) > 1:
            raise RuntimeError(f"Multiple legacy data directories found. Back up and consolidate them into {target} "
                               "before restarting: " + ", ".join(map(str, sources)))
        if not sources:
            target.mkdir(exist_ok=True, mode=0o700)
            return target
        source = sources[0]
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.migration-", dir=target.parent))
        try:
            before = _manifest(source)
            shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)
            os.chmod(staging, 0o700)
            if _manifest(staging) != before or _manifest(source) != before:
                raise OSError("Legacy data changed while copying; stop the old service and retry")
            _rebase_tasks(staging, source, target)
            if target.exists():
                target.rmdir()  # Only an empty destination may be replaced.
            staging.rename(target)
        except Exception as error:
            raise RuntimeError(f"Data migration failed; original data is preserved at {source}. "
                               "Stop the old service and check disk space, permissions and task files before retrying.") from error
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target


def configure_startup_storage(module_file: str | Path):
    # Resolve CLI precedence before importing server/auth modules or opening stores.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--data-dir")
    args, _ = parser.parse_known_args()
    if "--help" in sys.argv or "-h" in sys.argv:
        return
    os.environ["PDF2ZH_DATA_DIR"] = str(prepare_data_directory(module_file, args.data_dir))
