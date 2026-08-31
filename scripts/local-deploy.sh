#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/local-deploy.sh [--check-only]

Build and validate the current worktree, then install it into the local Zotero
profile and the Homebrew-managed zotero-pdf2zh-pro service.

Options:
  --check-only  Build and validate artifacts without changing local installs.
  -h, --help    Show this help.
EOF
}

die() {
    printf 'local-deploy.sh: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[local-deploy] %s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

sha256_file() {
    shasum -a 256 "$1" | awk '{print $1}'
}

listener_pids() {
    lsof -nP -iTCP:8890 -sTCP:LISTEN -t 2>/dev/null | sort -u || true
}

listener_count() {
    local pids
    pids="$(listener_pids)"
    if [[ -z "$pids" ]]; then
        printf '0\n'
    else
        printf '%s\n' "$pids" | wc -l | tr -d ' '
    fi
}

pid_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

wait_for_port_free() {
    local attempts="${1:-30}"
    local i
    for ((i = 0; i < attempts; i++)); do
        [[ "$(listener_count)" == "0" ]] && return 0
        sleep 1
    done
    return 1
}

wait_for_health() {
    local attempts="${1:-45}"
    local i
    for ((i = 0; i < attempts; i++)); do
        if curl -fsS --max-time 2 "$SERVER_URL/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

zotero_is_running() {
    pgrep -x Zotero >/dev/null 2>&1 || pgrep -x zotero >/dev/null 2>&1
}

quit_zotero() {
    local i
    if zotero_is_running; then
        osascript -e 'tell application "Zotero" to quit'
        for ((i = 0; i < 40; i++)); do
            zotero_is_running || return 0
            sleep 0.5
        done
        die "Zotero did not exit normally"
    fi
}

discover_zotero_profile() {
    python3 - "$ZOTERO_ROOT" <<'PY'
import configparser
import sys
from pathlib import Path

root = Path(sys.argv[1])
parser = configparser.ConfigParser()
profiles_ini = root / "profiles.ini"
if not parser.read(profiles_ini):
    raise SystemExit(f"cannot read {profiles_ini}")

profiles = [section for section in parser.sections() if section.startswith("Profile")]
selected = next(
    (section for section in profiles if parser.getboolean(section, "Default", fallback=False)),
    profiles[0] if profiles else None,
)
if selected is None:
    raise SystemExit(f"no Zotero profile in {profiles_ini}")

path = Path(parser.get(selected, "Path"))
if parser.getboolean(selected, "IsRelative", fallback=True):
    path = root / path
print(path.resolve())
PY
}

active_task_count() {
    curl -fsS --max-time 5 "$SERVER_URL/tasks" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
active = {"queued", "running", "cancelling"}
print(sum(task.get("status") in active for task in payload.get("tasks", [])))
'
}

assert_known_listener() {
    local count pid command
    count="$(listener_count)"
    [[ "$count" == "0" ]] && return 0
    [[ "$count" == "1" ]] || die "expected at most one 8890 listener, found $count"
    pid="$(listener_pids)"
    command="$(pid_command "$pid")"
    [[ "$command" == *"$REPO_ROOT/server/server.py"* || "$command" == *"$BREW_PREFIX/"* || "$command" == *"/opt/homebrew/Cellar/$BREW_SERVICE/"* ]] ||
        die "8890 listener PID $pid is not a recognized source or Homebrew service: $command"
}

verify_no_active_tasks() {
    local count
    [[ "$(listener_count)" == "0" ]] && return 0
    assert_known_listener
    count="$(active_task_count)" || die "cannot query active tasks; refusing to deploy"
    [[ "$count" == "0" ]] || die "$count active task(s); refusing to deploy"
}

stop_source_server() {
    local pid command
    if [[ -f "$DEV_SERVER_PID_FILE" ]]; then
        pid="$(<"$DEV_SERVER_PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            command="$(pid_command "$pid")"
            [[ "$command" == *"$REPO_ROOT/server/server.py"* ]] ||
                die "development PID $pid does not belong to this repository: $command"
            kill -TERM "$pid"
            for _ in {1..20}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.5
            done
            kill -0 "$pid" 2>/dev/null && die "source server PID $pid did not stop"
        fi
        rm -f "$DEV_SERVER_PID_FILE"
    fi

    pid="$(listener_pids)"
    if [[ -n "$pid" ]]; then
        command="$(pid_command "$pid")"
        if [[ "$command" == *"$REPO_ROOT/server/server.py"* ]]; then
            kill -TERM "$pid"
        fi
    fi
}

restore_plugin_development_session() {
    if [[ -f "$PLUGIN_SESSION_FILE" ]]; then
        [[ -x "$LOCAL_ROOT/bin/dev-plugin-off" ]] ||
            die "plugin development session exists but dev-plugin-off is unavailable"
        log "restoring the installed add-on before leaving development mode"
        SKIP_ZOTERO_LAUNCH=1 "$LOCAL_ROOT/bin/dev-plugin-off"
    fi
}

atomic_install_xpi() {
    local source="$1"
    local temporary
    mkdir -p "$EXTENSIONS_DIR"
    temporary="$(mktemp "$EXTENSIONS_DIR/.${ADDON_ID}.XXXXXX")"
    cp -p "$source" "$temporary"
    mv -f "$temporary" "$XPI_PATH"
}

write_empty_task_store() {
    python3 - "$TASK_STORE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"tasks": []}, ensure_ascii=False, indent=2) + "\n")
PY
}

install_wheel() {
    local wheel="$1"
    uv pip install \
        --python "$BREW_PYTHON" \
        --reinstall \
        --no-deps \
        "$wheel"
}

verify_installed_service() {
    "$BREW_PYTHON" - "$VERSION" <<'PY'
import importlib.metadata
import sys

import observability
import server
import task_manager

expected = sys.argv[1]
actual = importlib.metadata.version("zotero-pdf2zh-pro")
if actual != expected:
    raise SystemExit(f"installed version mismatch: {actual} != {expected}")
if not callable(observability.empty_metrics):
    raise SystemExit("observability module is incomplete")
if not callable(task_manager.TaskManager):
    raise SystemExit("task manager module is incomplete")
if not callable(server.build_health_payload):
    raise SystemExit("server module is incomplete")
PY
}

verify_running_service() {
    local count pid command tasks events
    wait_for_health 45 || die "Homebrew service health check failed"
    count="$(listener_count)"
    [[ "$count" == "1" ]] || die "expected one 8890 listener, found $count"
    pid="$(listener_pids)"
    command="$(pid_command "$pid")"
    [[ "$command" == *"$BREW_PREFIX/"* || "$command" == *"/opt/homebrew/Cellar/$BREW_SERVICE/"* ]] ||
        die "8890 listener PID $pid is not the Homebrew service: $command"
    tasks="$(curl -fsS --max-time 5 "$SERVER_URL/tasks")"
    printf '%s' "$tasks" | python3 -c 'import json, sys; assert isinstance(json.load(sys.stdin).get("tasks"), list)'
    events="$(curl -sSN --max-time 2 "$SERVER_URL/tasks/events" 2>/dev/null || true)"
    [[ "$events" == *": connected"* ]] || die "task event stream did not connect"
}

rollback() {
    local status=$?
    [[ "$DEPLOY_STARTED" == "1" && "$DEPLOY_SUCCEEDED" == "0" ]] || return "$status"

    trap - ERR EXIT
    set +e
    printf '[local-deploy] deployment failed; restoring the previous installation\n' >&2
    brew services stop "$BREW_SERVICE" >/dev/null 2>&1
    wait_for_port_free 20

    if [[ -f "$OLD_WHEEL" ]]; then
        install_wheel "$OLD_WHEEL" >/dev/null 2>&1
    fi
    if [[ -f "$OLD_TASK_STORE" ]]; then
        mkdir -p "$(dirname "$TASK_STORE")"
        cp -p "$OLD_TASK_STORE" "$TASK_STORE"
    else
        write_empty_task_store
    fi
    if [[ -f "$OLD_XPI" ]]; then
        atomic_install_xpi "$OLD_XPI"
    elif [[ "$OLD_XPI_PRESENT" == "0" ]]; then
        rm -f "$XPI_PATH"
    fi

    brew services start "$BREW_SERVICE" >/dev/null 2>&1
    wait_for_health 45 || printf '[local-deploy] warning: rollback service is unhealthy\n' >&2
    if [[ "$ZOTERO_WAS_RUNNING" == "1" ]]; then
        open -a Zotero >/dev/null 2>&1 || true
    fi
    return "$status"
}

CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-only)
            CHECK_ONLY=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "unknown argument: $1"
            ;;
    esac
    shift
done

[[ "$(uname -s)" == "Darwin" ]] || die "local deployment currently supports macOS only"
for command in awk brew curl git lsof npx osascript pgrep ps python3 shasum uv; do
    require_command "$command"
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BREW_SERVICE="zotero-pdf2zh-pro"
BREW_PREFIX="$(brew --prefix "$BREW_SERVICE")"
BREW_PYTHON="$BREW_PREFIX/libexec/venv/bin/python"
[[ -x "$BREW_PYTHON" ]] || die "Homebrew service Python is missing: $BREW_PYTHON"
SITE_PACKAGES="$($BREW_PYTHON -c 'import site; print(site.getsitepackages()[0])')"
TASK_STORE="$SITE_PACKAGES/translates/tasks.json"
SERVER_URL="http://127.0.0.1:8890"

ADDON_ID="zotero-pdf2zh-pro@study-233"
ZOTERO_ROOT="$HOME/Library/Application Support/Zotero"
PROFILE_DIR="$(discover_zotero_profile)"
EXTENSIONS_DIR="$PROFILE_DIR/extensions"
XPI_PATH="$EXTENSIONS_DIR/$ADDON_ID.xpi"

LOCAL_ROOT="$REPO_ROOT/.local-dev"
DEPLOYMENTS_ROOT="$LOCAL_ROOT/deployments"
CURRENT_FILE="$DEPLOYMENTS_ROOT/current"
PLUGIN_SESSION_FILE="$LOCAL_ROOT/state/plugin-session"
DEV_SERVER_PID_FILE="$LOCAL_ROOT/state/dev-server.pid"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUILD_DIR="$LOCAL_ROOT/builds/$STAMP"
ARTIFACT_DIR="$BUILD_DIR/artifacts"
mkdir -p "$ARTIFACT_DIR" "$DEPLOYMENTS_ROOT"

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("server/pyproject.toml", "rb"))["project"]["version"])')"
PNPM=(npx --yes pnpm@10.34.5)

log "installing plugin dependencies"
CI=true "${PNPM[@]}" --dir plugin install --frozen-lockfile
log "building the Zotero plugin"
"${PNPM[@]}" --dir plugin build

log "building the server package"
uv build server --out-dir "$ARTIFACT_DIR" --clear --no-sources

PLUGIN_XPI_SOURCE="$REPO_ROOT/plugin/build/zotero-pdf2zh-pro.xpi"
SERVER_WHEEL_SOURCE="$ARTIFACT_DIR/zotero_pdf2zh_pro-$VERSION-py3-none-any.whl"
[[ -f "$PLUGIN_XPI_SOURCE" ]] || die "plugin build did not produce $PLUGIN_XPI_SOURCE"
[[ -f "$SERVER_WHEEL_SOURCE" ]] || die "server build did not produce $SERVER_WHEEL_SOURCE"
cp -p "$PLUGIN_XPI_SOURCE" "$ARTIFACT_DIR/zotero-pdf2zh-pro.xpi"
PLUGIN_XPI_SOURCE="$ARTIFACT_DIR/zotero-pdf2zh-pro.xpi"
WHEEL_BASENAME="$(basename "$SERVER_WHEEL_SOURCE")"

python3 - "$PLUGIN_XPI_SOURCE" "$VERSION" "$ADDON_ID" <<'PY'
import json
import sys
import zipfile

xpi, version, addon_id = sys.argv[1:]
with zipfile.ZipFile(xpi) as archive:
    manifest = json.loads(archive.read("manifest.json"))
if manifest.get("version") != version:
    raise SystemExit(f"XPI version mismatch: {manifest.get('version')} != {version}")
applications = manifest.get("applications", {}).get("zotero", {})
if applications.get("id") != addon_id:
    raise SystemExit(f"XPI add-on ID mismatch: {applications.get('id')} != {addon_id}")
PY

GIT_COMMIT="$(git rev-parse HEAD)"
GIT_STATUS="$(git status --porcelain)"
{
    printf 'version=%s\n' "$VERSION"
    printf 'built_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'git_commit=%s\n' "$GIT_COMMIT"
    printf 'git_dirty=%s\n' "$([[ -n "$GIT_STATUS" ]] && printf true || printf false)"
    printf 'xpi_sha256=%s\n' "$(sha256_file "$PLUGIN_XPI_SOURCE")"
    printf 'wheel_sha256=%s\n' "$(sha256_file "$SERVER_WHEEL_SOURCE")"
    if [[ -n "$GIT_STATUS" ]]; then
        printf '\n[git_status]\n%s\n' "$GIT_STATUS"
    fi
} >"$ARTIFACT_DIR/manifest.txt"

if [[ "$CHECK_ONLY" == "1" ]]; then
    log "checks passed; artifacts are in $ARTIFACT_DIR"
    exit 0
fi

verify_no_active_tasks

FIRST_DEPLOY=1
PREVIOUS_DEPLOYMENT=""
if [[ -f "$CURRENT_FILE" ]]; then
    PREVIOUS_DEPLOYMENT="$(<"$CURRENT_FILE")"
    [[ "$PREVIOUS_DEPLOYMENT" == "$DEPLOYMENTS_ROOT/"* && -d "$PREVIOUS_DEPLOYMENT" ]] ||
        die "untrusted current deployment path: $PREVIOUS_DEPLOYMENT"
    FIRST_DEPLOY=0
fi

DEPLOY_DIR="$DEPLOYMENTS_ROOT/$STAMP"
[[ ! -e "$DEPLOY_DIR" ]] || die "deployment directory already exists: $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR/new" "$DEPLOY_DIR/old"
cp -p "$PLUGIN_XPI_SOURCE" "$DEPLOY_DIR/new/zotero-pdf2zh-pro.xpi"
NEW_WHEEL="$DEPLOY_DIR/new/$WHEEL_BASENAME"
cp -p "$SERVER_WHEEL_SOURCE" "$NEW_WHEEL"
cp -p "$ARTIFACT_DIR/manifest.txt" "$DEPLOY_DIR/manifest.txt"

OLD_XPI="$DEPLOY_DIR/old/zotero-pdf2zh-pro.xpi"
OLD_WHEEL=""
OLD_TASK_STORE="$DEPLOY_DIR/old/tasks.json"
OLD_XPI_PRESENT=0
if [[ -f "$XPI_PATH" ]]; then
    cp -p "$XPI_PATH" "$OLD_XPI"
    OLD_XPI_PRESENT=1
fi
[[ ! -f "$TASK_STORE" ]] || cp -p "$TASK_STORE" "$OLD_TASK_STORE"
previous_wheel=""
if [[ "$FIRST_DEPLOY" == "0" ]]; then
    previous_wheel="$(find "$PREVIOUS_DEPLOYMENT/new" -maxdepth 1 -type f -name '*.whl' -print -quit)"
fi
if [[ -n "$previous_wheel" ]]; then
    OLD_WHEEL="$DEPLOY_DIR/old/$(basename "$previous_wheel")"
    cp -p "$previous_wheel" "$OLD_WHEEL"
else
    log "building a rollback wheel from the installed Homebrew source"
    uv build \
        --wheel \
        --directory "$BREW_PREFIX/libexec" \
        --out-dir "$DEPLOY_DIR/old/rollback-dist" \
        --no-sources
    rollback_wheel="$(find "$DEPLOY_DIR/old/rollback-dist" -maxdepth 1 -type f -name '*.whl' -print -quit)"
    [[ -n "$rollback_wheel" ]] || die "could not build the Homebrew rollback wheel"
    OLD_WHEEL="$DEPLOY_DIR/old/$(basename "$rollback_wheel")"
    cp -p "$rollback_wheel" "$OLD_WHEEL"
fi

ZOTERO_WAS_RUNNING=0
zotero_is_running && ZOTERO_WAS_RUNNING=1
quit_zotero
if [[ "$(listener_count)" != "0" ]]; then
    if ! active_after_quit="$(active_task_count)"; then
        [[ "$ZOTERO_WAS_RUNNING" == "0" ]] || open -a Zotero
        die "cannot recheck active tasks after closing Zotero; refusing to deploy"
    fi
    if [[ "$active_after_quit" != "0" ]]; then
        [[ "$ZOTERO_WAS_RUNNING" == "0" ]] || open -a Zotero
        die "$active_after_quit active task(s) appeared during the build; refusing to deploy"
    fi
fi

DEPLOY_STARTED=1
DEPLOY_SUCCEEDED=0
trap rollback ERR EXIT

restore_plugin_development_session
assert_known_listener
stop_source_server
brew services stop "$BREW_SERVICE" >/dev/null
wait_for_port_free 30 || die "8890 remained occupied after stopping services"

log "installing the current server wheel"
install_wheel "$NEW_WHEEL"
verify_installed_service

if [[ "$FIRST_DEPLOY" == "1" ]]; then
    log "starting the Homebrew task store with an empty history"
    write_empty_task_store
elif [[ -f "$OLD_TASK_STORE" ]]; then
    cp -p "$OLD_TASK_STORE" "$TASK_STORE"
fi

log "installing the production XPI"
atomic_install_xpi "$DEPLOY_DIR/new/zotero-pdf2zh-pro.xpi"
[[ "$(sha256_file "$XPI_PATH")" == "$(sha256_file "$DEPLOY_DIR/new/zotero-pdf2zh-pro.xpi")" ]] ||
    die "installed XPI checksum mismatch"

brew pin "$BREW_SERVICE" >/dev/null
brew services start "$BREW_SERVICE" >/dev/null
verify_running_service

printf '%s\n' "$DEPLOY_DIR" >"$CURRENT_FILE.tmp"
mv -f "$CURRENT_FILE.tmp" "$CURRENT_FILE"
DEPLOY_SUCCEEDED=1
trap - ERR EXIT

if [[ "$ZOTERO_WAS_RUNNING" == "1" ]]; then
    open -a Zotero
fi

log "deployment complete"
log "XPI: $(sha256_file "$XPI_PATH")"
log "wheel: $(sha256_file "$NEW_WHEEL")"
log "backup: $DEPLOY_DIR"
