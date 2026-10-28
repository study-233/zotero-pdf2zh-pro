#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/release.sh <version> [--build-source auto|local|actions] [--resume <state.json>] [--full-validation] [--baseline <version>] [--remote-validation] [--no-push] [--no-release] [--no-pypi] [--no-tap] [--tap-path <path>] [--replace-existing <old-commit>]

Build, validate, and publish a unified zotero-pdf2zh-pro release.

The release includes the Zotero XPI and update manifest, PyPI wheel/sdist,
Windows GUI ZIP, a local corresponding-source archive, and an optional
update to the public source-only Homebrew tap. Add a matching CHANGELOG.md
section first. Windows prefers local build and validation, then imports verified packages
into Actions for publication. --build-source actions explicitly uses the remote builder.
--no-push builds on Windows without publishing.
--full-validation also runs rollback and relocation checks against the final packages.
--baseline explicitly adds an older release; defaults use only the previous stable.
Explicit compatibility upgrades run locally on Windows, remotely on macOS/Linux. Use
--remote-validation if this Windows host cannot run the extended checks.
--replace-existing explicitly replaces clients at an existing tag, preserves the
original PyPI files, and requires unchanged server and license inputs.
--resume reuses the recorded workflow and artifacts; it never implicitly rebuilds.
--no-pypi skips uploading, but distributing installers still requires matching public packages.
EOF
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

TEMP_PATHS=()
REPLACEMENT_BACKUP=""
REPLACEMENT_TAP_PUSHED=0
# BEGIN_RELEASE_CLEANUP
cleanup() {
    local exit_status=$?
    local path
    if [[ "$exit_status" -ne 0 && -n "$REPLACEMENT_BACKUP" ]]; then
        echo "Release failed after client replacement; restoring backup: $REPLACEMENT_BACKUP" >&2
        if ! uv run --no-project python scripts/release_replacement.py restore "$REPLACEMENT_BACKUP"; then
            echo "Automatic recovery stopped; preserve and inspect backup: $REPLACEMENT_BACKUP" >&2
        fi
        if [[ "$REPLACEMENT_TAP_PUSHED" -eq 1 ]]; then
            echo "Homebrew tap update was attempted; inspect its state before restoring tap history." >&2
        fi
    fi
    if [[ -n "${WORKSPACE_RUN:-}" ]]; then
        local validation_status=failed
        [[ "$exit_status" -ne 0 ]] || validation_status=passed
        if ! uv run --no-project python scripts/workspace.py finish "$WORKSPACE_RUN" --status "$validation_status"; then
            echo "Validation workspace retained: $WORKSPACE_RUN" >&2
        fi
    fi
    # macOS Bash 3.2 treats an empty array as unset under `set -u`.
    for path in ${TEMP_PATHS[@]+"${TEMP_PATHS[@]}"}; do
        if [[ -n "$path" && -e "$path" ]]; then
            rm -rf -- "$path"
        fi
    done
    return "$exit_status"
}
# END_RELEASE_CLEANUP
trap cleanup EXIT

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

if [[ $# -gt 0 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
    usage
    exit 0
fi
[[ $# -ge 1 ]] || { usage; exit 2; }

VERSION="$1"
shift
PUSH=1
PUBLISH_RELEASE=1
PUBLISH_PYPI=1
UPDATE_TAP=1
FULL_VALIDATION=false
REMOTE_VALIDATION=false
BUILD_SOURCE=auto
TAP_PATH=""
REPLACE_EXISTING=""
RESUME_STATE=""
EXPLICIT_BASELINES=()
PYPI_TOKEN="${UV_PUBLISH_TOKEN:-}"
unset UV_PUBLISH_TOKEN

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-source)
            [[ $# -ge 2 ]] || die "--build-source requires auto, local or actions"
            BUILD_SOURCE="$2"
            shift
            ;;
        --full-validation) FULL_VALIDATION=true ;;
        --baseline)
            [[ $# -ge 2 ]] || die "--baseline requires a version"
            EXPLICIT_BASELINES+=(--baseline "$2")
            shift
            ;;
        --remote-validation) REMOTE_VALIDATION=true ;;
        --no-push)
            PUSH=0
            PUBLISH_RELEASE=0
            PUBLISH_PYPI=0
            UPDATE_TAP=0
            ;;
        --no-release) PUBLISH_RELEASE=0 ;;
        --no-pypi) PUBLISH_PYPI=0 ;;
        --no-tap) UPDATE_TAP=0 ;;
        --resume)
            [[ $# -ge 2 ]] || die "--resume requires a release-state.json path"
            RESUME_STATE="$2"
            shift
            ;;
        --replace-existing)
            [[ $# -ge 2 ]] || die "--replace-existing requires the expected old commit"
            REPLACE_EXISTING="$2"
            shift
            ;;
        --tap-path)
            [[ $# -ge 2 ]] || die "--tap-path requires a path"
            TAP_PATH="$2"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

case "$BUILD_SOURCE" in auto|local|actions) ;; *) die "Invalid --build-source: $BUILD_SOURCE" ;; esac

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "version must look like semver, got: $VERSION"
[[ -z "$REPLACE_EXISTING" || "$REPLACE_EXISTING" =~ ^[0-9a-f]{40}$ ]] ||
    die "--replace-existing requires a full lowercase commit SHA"
# Do not enable replacement implicitly from the environment. It is a CLI action.
export REPLACEMENT_COMMIT="$REPLACE_EXISTING"

for cmd in git gh node uv perl curl; do
    require_command "$cmd"
done
if [[ "$PUSH" -eq 0 ]]; then
    for cmd in npx cargo powershell.exe; do require_command "$cmd"; done
fi

PRODUCT="zotero-pdf2zh-pro"
TAG="v$VERSION"
MAIN_REPO="study-233/zotero-pdf2zh-pro"
TAP_REPO="study-233/homebrew-formula"
TAP_URL="https://github.com/study-233/homebrew-formula.git"
PYPI_CHECK_URL="https://pypi.org/simple/$PRODUCT/"
WINDOWS_PACKAGE="dist/$PRODUCT-windows-x64.zip"
WINDOWS_INSTALLER="dist/$PRODUCT-windows-x64-setup.exe"
WINDOWS_UPDATE_MANIFEST="dist/windows-update.json"
SOURCE_ARCHIVE="dist/$PRODUCT-$VERSION-source.zip"
XPI="plugin/build/$PRODUCT.xpi"
UPDATE_MANIFEST="plugin/build/update.json"
PNPM=(npx --yes pnpm@10.34.5)

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

uv run --no-project python scripts/generate_diagnostic_contract.py --check

ORIGIN_URL="$(git remote get-url origin)"
case "$ORIGIN_URL" in
    "https://github.com/$MAIN_REPO"|"https://github.com/$MAIN_REPO.git"|"git@github.com:$MAIN_REPO"|"git@github.com:$MAIN_REPO.git") ;;
    *) die "origin must be $MAIN_REPO, got: $ORIGIN_URL" ;;
esac

[[ "$(git branch --show-current)" == "main" ]] || die "release must run from main"
git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"
if [[ "${OS:-}" == Windows_NT ]]; then
    uv run --no-project python scripts/windows_powershell.py --preflight-only
fi

if [[ -n "$REPLACE_EXISTING" ]]; then
    uv run --no-project python scripts/release_replacement.py prepare "$VERSION" "$REPLACE_EXISTING"
fi

CHANGELOG_SECTION="$(awk -v tag="$TAG" '
    $0 ~ "^## " tag "([[:space:]]|-|$)" { found = 1; print; next }
    found && /^## / { exit }
    found { print }
' CHANGELOG.md)"
[[ -n "$(printf '%s' "$CHANGELOG_SECTION" | tr -d '[:space:]')" ]] ||
    die "CHANGELOG.md must contain a section starting with: ## $TAG"

node - "$VERSION" <<'NODE'
const fs = require("fs");
const version = process.argv[2];
for (const file of [
  "plugin/package.json",
]) {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  data.version = version;
  fs.writeFileSync(file, JSON.stringify(data, null, 4) + "\n");
}
NODE

VERSION="$VERSION" perl -0pi -e 's/version = "[^"]+"/version = "$ENV{VERSION}"/' server/pyproject.toml
VERSION="$VERSION" perl -0pi -e 's/^(version = ")[^"]+("\s*)$/$1$ENV{VERSION}$2/m' windows-app/Cargo.toml
VERSION="$VERSION" perl -0pi -e 's/VERSION = "[^"]+"/VERSION = "$ENV{VERSION}"/' server/server.py
VERSION="$VERSION" perl -0pi -e 's/(\$PackageVersion = ")[^"]+(" # release-version)/$1$ENV{VERSION}$2/' scripts/windows/common.ps1

node - "$VERSION" <<'NODE'
const fs = require("fs");
const version = process.argv[2];
function replaceOnce(file, pattern, replacement, label) {
  const text = fs.readFileSync(file, "utf8");
  const matches = [...text.matchAll(pattern)];
  if (matches.length !== 1) throw new Error(`expected one ${label}, found ${matches.length}`);
  fs.writeFileSync(file, text.replace(pattern, replacement));
}
replaceOnce("README.md", /(<!-- release-version --> `)[^`]+(`)/g, `$1${version}$2`, "README version marker");
replaceOnce(
  "server/uv.lock",
  /(\[\[package\]\]\r?\nname = "zotero-pdf2zh-pro"\r?\nversion = ")[^"]+(")/g,
  `$1${version}$2`,
  "server lock version",
);
replaceOnce(
  "windows-app/Cargo.lock",
  /(\[\[package\]\]\r?\nname = "zotero-pdf2zh-pro-control"\r?\nversion = ")[^"]+(")/g,
  `$1${version}$2`,
  "control-center lock version",
);
NODE

UV_DEFAULT_INDEX=https://pypi.org/simple uv --directory server lock --locked
if [[ "$PUSH" -eq 0 ]]; then
# Source/script regressions are covered by the required same-commit core CI.
# This stage validates the actual artifacts produced below.
if [[ -z "$REPLACE_EXISTING" ]]; then
    uv build server --out-dir server/dist --clear --no-sources
fi
uv run --no-project python scripts/check_pypi_artifacts.py server/dist "$VERSION"

CI=true "${PNPM[@]}" --dir plugin install --frozen-lockfile
rm -rf -- plugin/build
"${PNPM[@]}" --dir plugin build

uv run --no-project python scripts/windows_powershell.py scripts/build_windows_control.ps1
uv run --no-project python scripts/windows_powershell.py scripts/build_windows_control.ps1 -RunTests

uv run --no-project python scripts/build_windows_package.py --version "$VERSION"
uv run --no-project python scripts/build_windows_installer.py --version "$VERSION"
uv run --no-project python scripts/test_windows_installer.py
uv run --no-project python scripts/build_windows_update_manifest.py \
    --version "$VERSION" --package "$WINDOWS_PACKAGE" --output "$WINDOWS_UPDATE_MANIFEST"
uv run --no-project python scripts/test_windows_update_manifest.py
uv run --no-project python scripts/windows_powershell.py scripts/test_windows_package.ps1 -Package "$WINDOWS_PACKAGE"
fi

git add README.md plugin/package.json server/pyproject.toml server/server.py server/uv.lock \
    scripts/windows/common.ps1 windows-app/Cargo.toml \
    windows-app/Cargo.lock
if ! git diff --cached --quiet; then
    git commit -m "chore: release $TAG"
fi
COMMIT="$(git rev-parse HEAD)"

mkdir -p dist
if [[ "$PUSH" -eq 0 ]]; then
    git archive --format=zip --prefix="$PRODUCT-$VERSION/" \
        --output="$SOURCE_ARCHIVE" "$COMMIT"
fi

REMOTE_TAG_REFS="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")"
REMOTE_TAG_COMMIT="$(printf '%s\n' "$REMOTE_TAG_REFS" | awk '
    $2 ~ /\^\{\}$/ { peeled = $1 }
    $2 !~ /\^\{\}$/ { direct = $1 }
    END { print peeled ? peeled : direct }
')"
if [[ -n "$REPLACE_EXISTING" ]]; then
    [[ "$REMOTE_TAG_COMMIT" == "$REPLACE_EXISTING" ]] || die "existing tag moved since replacement preparation"
elif [[ -n "$REMOTE_TAG_COMMIT" && "$REMOTE_TAG_COMMIT" != "$COMMIT" ]]; then
    die "remote tag $TAG points to $REMOTE_TAG_COMMIT, expected $COMMIT"
fi

if [[ "$PUSH" -eq 1 ]]; then
    state_args=("$VERSION" "$COMMIT")
    [[ -z "$RESUME_STATE" ]] || state_args+=(--resume "$RESUME_STATE")
    RELEASE_STATE="$(uv run --no-project python scripts/release_control.py state "${state_args[@]}")"
    echo "Release recovery record: $RELEASE_STATE" >&2
    VALIDATION_PLAN="$(dirname "$RELEASE_STATE")/validation-plan.json"
    VALIDATION_RECEIPT="$(dirname "$RELEASE_STATE")/local-validation.json"
    [[ -z "$REPLACE_EXISTING" ]] || FULL_VALIDATION=true
    plan_args=("$VERSION" "$COMMIT" "$VALIDATION_PLAN" ${EXPLICIT_BASELINES[@]+"${EXPLICIT_BASELINES[@]}"})
    [[ "$FULL_VALIDATION" != true ]] || plan_args+=(--full)
    uv run --no-project python scripts/release_control.py plan "${plan_args[@]}" >&2
    FULL_VALIDATION="$(node -e 'console.log(require(process.argv[1]).fullValidation)' "$VALIDATION_PLAN")"
    EXTENDED_VALIDATION="$(node -e 'console.log(require(process.argv[1]).cases.length > 1)' "$VALIDATION_PLAN")"
    git push origin main
fi

# A publication reuses core CI and packages from the standard Windows check.
# Runtime/OCR and lifecycle checks run once against those packages in CI.
# Selected rollback/reinstall/relocation checks share that one lifecycle run.
# The workflow's --no-push build does not enter this block recursively.
BUILD_RUN=""
if [[ "$PUSH" -eq 1 ]]; then
    CORE_RUN=""
    for _ in {1..40}; do
        CORE_RUN="$(gh run list --repo "$MAIN_REPO" --workflow ci.yml \
            --commit "$COMMIT" --event push --limit 1 --json databaseId \
            --jq '.[0].databaseId // empty')"
        [[ -n "$CORE_RUN" ]] && break
        sleep 2
    done
    [[ -n "$CORE_RUN" ]] || die "Core CI did not start for the release commit"
    [[ -z "$REPLACE_EXISTING" ]] || FULL_VALIDATION=true
    # BEGIN_VALIDATION_ROUTING
    REMOTE_FULL_VALIDATION="$FULL_VALIDATION"
    LOCAL_EXTENDED_VALIDATION=false
    if [[ "$EXTENDED_VALIDATION" == true && "${OS:-}" == Windows_NT && "$REMOTE_VALIDATION" == false && -z "$REPLACE_EXISTING" ]]; then
        LOCAL_EXTENDED_VALIDATION=true
    fi
    # END_VALIDATION_ROUTING
    build_args=("$RELEASE_STATE" build build-windows-release.yml
        --field "version=$VERSION" --field "commit=$COMMIT"
        --field "full_validation=$REMOTE_FULL_VALIDATION" --field "replacement_commit=$REPLACE_EXISTING"
        --field "defer_extended_validation=$LOCAL_EXTENDED_VALIDATION"
        --field "validation_plan=$(cat "$VALIDATION_PLAN")")
    if [[ "$BUILD_SOURCE" == auto ]]; then
        if [[ "${OS:-}" == Windows_NT && "$REMOTE_VALIDATION" == false && -z "$REPLACE_EXISTING" ]] &&
            uv run --no-project python scripts/release_local.py preflight; then
            BUILD_SOURCE=local
        else
            BUILD_SOURCE=actions
            echo 'Using Actions build; select --build-source local to require the local toolchain.' >&2
        fi
    fi
    if [[ "$BUILD_SOURCE" == local ]]; then
        [[ -z "$REPLACE_EXISTING" && "$REMOTE_VALIDATION" == false ]] || die "Local build cannot use replacement or --remote-validation"
        LOCAL_EXTENDED_VALIDATION=false
        LOCAL_CANDIDATE="$(uv run --no-project python scripts/release_local.py build "$VERSION" "$COMMIT" "$VALIDATION_PLAN" "$(dirname "$RELEASE_STATE")/local-build.json" --upload)"
        build_args=("$RELEASE_STATE" build build-windows-release.yml
            --field "version=$VERSION" --field "commit=$COMMIT"
            --field "local_candidate=$(printf '%s' "$LOCAL_CANDIDATE" | node -e 'process.stdout.write(String(JSON.parse(require("fs").readFileSync(0,"utf8")).assetId))')"
            --field "candidate_sha256=$(printf '%s' "$LOCAL_CANDIDATE" | node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(0,"utf8")).sha256)')")
    fi
    uv run --no-project python scripts/release_control.py watch "$CORE_RUN" --timeout 1200
    uv run --no-project python scripts/release_control.py record "$RELEASE_STATE" core --field "runId=$CORE_RUN" --field status=passed
    [[ -z "$RESUME_STATE" ]] || build_args+=(--retry-failed)
    BUILD_RUN="$(uv run --no-project python scripts/release_control.py workflow "${build_args[@]}")"
    VERIFIED_INFO="$(uv run --no-project python scripts/workspace.py start verified-download --owner-pid "$$")"
    # Git Bash must not rewrite the JavaScript replacement slash as its install path.
    WORKSPACE_RUN="$(printf '%s' "$VERIFIED_INFO" | MSYS2_ARG_CONV_EXCL='*' node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8")).work.replace(/\\/g,"/"))')"
    VERIFIED_DIR="$WORKSPACE_RUN/download"
    uv run --no-project python scripts/release_control.py download "$BUILD_RUN" "release-$VERSION-$COMMIT" "$VERIFIED_DIR"
    node - "$VERIFIED_DIR" "$VERSION" "$COMMIT" "$REPLACE_EXISTING" <<'VERIFY_BUILD'
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const [directory, version, commit, previousCommit = ""] = process.argv.slice(2);
const product = "zotero-pdf2zh-pro";
const manifest = JSON.parse(fs.readFileSync(path.join(directory, "checksums.json"), "utf8"));
if (manifest.version !== version || manifest.commit !== commit) throw new Error("Verified build identity mismatch");
if (previousCommit) {
  const snapshot = JSON.parse(fs.readFileSync("dist/replacement-source.json", "utf8"));
  const replacement = manifest.replacement;
  if (!replacement || replacement.previousCommit !== previousCommit || replacement.pypiSourceCommit !== previousCommit || replacement.clientCommit !== commit)
    throw new Error("Verified replacement identity mismatch");
  for (const field of ["previousCommit", "previousTagObject", "pypiSourceCommit", "clientCommit"])
    if (replacement[field] !== snapshot[field]) throw new Error(`Replacement provenance mismatch: ${field}`);
  for (const name of [`zotero_pdf2zh_pro-${version}-py3-none-any.whl`, `zotero_pdf2zh_pro-${version}.tar.gz`]) {
    for (const field of ["size", "sha256", "url"])
      if (replacement.pypiArtifacts?.[name]?.[field] !== snapshot.pypiArtifacts?.[name]?.[field])
        throw new Error(`Replacement PyPI provenance mismatch: ${name}`);
    for (const field of ["size", "sha256"])
      if (manifest.artifacts?.[name]?.[field] !== snapshot.pypiArtifacts[name][field])
        throw new Error(`Replacement must reuse original PyPI bytes: ${name}`);
  }
} else if (manifest.replacement) {
  throw new Error("Replacement artifacts require explicit --replace-existing");
}
const destinations = {
  [`${product}.xpi`]: `plugin/build/${product}.xpi`,
  "update.json": "plugin/build/update.json",
  [`${product}-windows-x64.zip`]: `dist/${product}-windows-x64.zip`,
  [`${product}-windows-x64-setup.exe`]: `dist/${product}-windows-x64-setup.exe`,
  "windows-update.json": "dist/windows-update.json",
  [`${product}-${version}-source.zip`]: `dist/${product}-${version}-source.zip`,
  [`zotero_pdf2zh_pro-${version}-py3-none-any.whl`]: `server/dist/zotero_pdf2zh_pro-${version}-py3-none-any.whl`,
  [`zotero_pdf2zh_pro-${version}.tar.gz`]: `server/dist/zotero_pdf2zh_pro-${version}.tar.gz`,
};
if (JSON.stringify(Object.keys(manifest.artifacts).sort()) !== JSON.stringify(Object.keys(destinations).sort()))
  throw new Error("Unexpected verified artifact set");
for (const name of Object.keys(destinations)) {
  const data = fs.readFileSync(path.join(directory, name));
  const expected = manifest.artifacts[name];
  if (data.length !== expected.size || crypto.createHash("sha256").update(data).digest("hex") !== expected.sha256)
    throw new Error(`Verified artifact checksum mismatch: ${name}`);
}
for (const [name, destination] of Object.entries(destinations)) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(path.join(directory, name), destination);
}
VERIFY_BUILD
    uv run --no-project python scripts/check_release_artifacts.py "$VERSION" "$COMMIT" --collect
    if [[ "$LOCAL_EXTENDED_VALIDATION" == true ]]; then
        extended_args=(-Version "$VERSION" -Plan "$VALIDATION_PLAN" -Receipt "$VALIDATION_RECEIPT" -SkipLifecycle -SkipRecentUpgrade)
        [[ -z "$REPLACE_EXISTING" ]] || extended_args+=(-SkipPreviousRelease)
        if [[ -f "$VALIDATION_RECEIPT" ]] && uv run --no-project python scripts/release_control.py verify-contract "$VERIFIED_DIR" --receipt "$VALIDATION_RECEIPT"; then
            echo 'Reusing matching local extended validation.' >&2
        else
            uv run --no-project python scripts/windows_powershell.py scripts/test_windows_release_extended.ps1 "${extended_args[@]}"
        fi
    fi
    contract_args=("$VERIFIED_DIR")
    [[ ! -f "$VALIDATION_RECEIPT" ]] || contract_args+=(--receipt "$VALIDATION_RECEIPT")
    uv run --no-project python scripts/release_control.py verify-contract "${contract_args[@]}"
fi

for artifact in "$XPI" "$UPDATE_MANIFEST" "$WINDOWS_PACKAGE" "$WINDOWS_INSTALLER" "$WINDOWS_UPDATE_MANIFEST" "$SOURCE_ARCHIVE"; do
    [[ -f "$artifact" ]] || die "missing release artifact: $artifact"
done

pypi_release_complete() {
    uv run --no-project python scripts/pypi_publication.py dist/release/checksums.json --timeout 0
}

sha256_file() {
    node -e '
const crypto = require("crypto");
const fs = require("fs");
const file = process.argv[1];
const hash = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
process.stdout.write(hash);
' "$1"
}

XPI_SHA256="$(sha256_file "$XPI")"
UPDATE_SHA256="$(sha256_file "$UPDATE_MANIFEST")"
WINDOWS_SHA256="$(sha256_file "$WINDOWS_PACKAGE")"
WINDOWS_INSTALLER_SHA256="$(sha256_file "$WINDOWS_INSTALLER")"
WINDOWS_UPDATE_SHA256="$(sha256_file "$WINDOWS_UPDATE_MANIFEST")"
SOURCE_SHA256="$(sha256_file "$SOURCE_ARCHIVE")"

if [[ -n "$REPLACE_EXISTING" ]]; then
    pypi_release_complete || die "Replacement requires both original PyPI files to remain unchanged and available"
elif [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    PYPI_STATUS=0
    pypi_release_complete || PYPI_STATUS=$?
    [[ "$PYPI_STATUS" -ne 2 ]] || die "Public PyPI files do not match verified artifacts; publication stopped"
    if [[ "$PYPI_STATUS" -ne 0 ]]; then
        if [[ -n "$PYPI_TOKEN" ]]; then
            UV_PUBLISH_TOKEN="$PYPI_TOKEN" uv publish --check-url "$PYPI_CHECK_URL" \
                "server/dist/zotero_pdf2zh_pro-$VERSION-py3-none-any.whl" \
                "server/dist/zotero_pdf2zh_pro-$VERSION.tar.gz"
        else
            if [[ -z "$REMOTE_TAG_COMMIT" ]]; then
                git tag -a "$TAG" -m "$TAG" "$COMMIT"
                git push origin "$TAG"
                REMOTE_TAG_COMMIT="$COMMIT"
            fi
            LOCAL_RECEIPT_JSON=""
            [[ ! -f "$VALIDATION_RECEIPT" ]] || LOCAL_RECEIPT_JSON="$(cat "$VALIDATION_RECEIPT")"
            publish_args=("$RELEASE_STATE" pypi publish-pypi.yml --field "tag=$TAG" --field "build_run_id=$BUILD_RUN" --field "local_validation=$LOCAL_RECEIPT_JSON" --field "publish_github=$([[ "$PUBLISH_RELEASE" -eq 1 ]] && echo true || echo false)" --field "visibility_timeout=${PDF2ZH_PYPI_WAIT_SECONDS:-600}")
            [[ -z "$RESUME_STATE" ]] || publish_args+=(--retry-failed)
            PUBLISH_RUN="$(uv run --no-project python scripts/release_control.py workflow "${publish_args[@]}")"
        fi
    fi
    uv run --no-project python scripts/pypi_publication.py dist/release/checksums.json \
        --timeout "${PDF2ZH_PYPI_WAIT_SECONDS:-600}" --receipt "$(dirname "$RELEASE_STATE")/pypi-public.json"
    [[ "$PUSH" -ne 1 ]] || uv run --no-project python scripts/release_control.py record "$RELEASE_STATE" pypi-visible --field status=passed
fi

# Skipping upload must never bypass the installer's public dependency gate.
if [[ "$PUBLISH_RELEASE" -eq 1 && "$PUBLISH_PYPI" -eq 0 && -z "$REPLACE_EXISTING" ]]; then
    pypi_release_complete || die "--no-pypi requires matching public wheel and sdist before distributing installers"
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
    NOTES_FILE="$(mktemp)"
    TEMP_PATHS+=("$NOTES_FILE")
    printf '%s\n\n' "$CHANGELOG_SECTION" >"$NOTES_FILE"
    if [[ -n "$REPLACE_EXISTING" ]]; then
        printf '\n本次为同版本客户端修订，PyPI 服务端包保持不变。已安装同版本的用户请按上方更新说明手动更新。\n' >>"$NOTES_FILE"
        REPLACEMENT_BACKUP_FILE="$(mktemp)"
        TEMP_PATHS+=("$REPLACEMENT_BACKUP_FILE")
        uv run --no-project python scripts/release_replacement.py publish \
            "$VERSION" "$REPLACE_EXISTING" "$COMMIT" --notes "$NOTES_FILE" \
            --backup-path-file "$REPLACEMENT_BACKUP_FILE"
        # This file is unique to the successful invocation above; never use a stale backup pointer.
        REPLACEMENT_BACKUP="$(cat "$REPLACEMENT_BACKUP_FILE")"
        [[ -n "$REPLACEMENT_BACKUP" ]] || die "Replacement did not record its recovery backup"
    else
        uv run --no-project python scripts/release_control.py publish "$TAG" "$COMMIT" "$NOTES_FILE" \
            "$XPI" "$UPDATE_MANIFEST" "$WINDOWS_PACKAGE" "$WINDOWS_INSTALLER" "$WINDOWS_UPDATE_MANIFEST"
    fi
fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    if [[ -z "$TAP_PATH" ]]; then
        if [[ -d "$REPO_ROOT/../homebrew-formula/.git" ]]; then
            TAP_PATH="$REPO_ROOT/../homebrew-formula"
        else
            TAP_TEMP="$(mktemp -d)"
            TEMP_PATHS+=("$TAP_TEMP")
            TAP_PATH="$TAP_TEMP/tap"
            git clone "$TAP_URL" "$TAP_PATH"
        fi
    fi
    [[ -d "$TAP_PATH/.git" ]] || die "Homebrew tap path is not a git repo: $TAP_PATH"
    [[ "$(git -C "$TAP_PATH" branch --show-current)" == "main" ]] || die "Homebrew tap must be on main"
    [[ -z "$(git -C "$TAP_PATH" status --porcelain)" ]] || die "Homebrew tap worktree is dirty"
    git -C "$TAP_PATH" pull --ff-only origin main

    FORMULA_REL="Formula/zotero-pdf2zh-pro.rb"
    FORMULA="$TAP_PATH/$FORMULA_REL"
    [[ -f "$FORMULA" ]] || die "Homebrew formula not found: $FORMULA"
    VERSION="$VERSION" COMMIT="$COMMIT" perl -0pi -e '
s/(url "https:\/\/github\.com\/study-233\/zotero-pdf2zh-pro\.git", using: :git, revision: ")[^"]+(")/$1$ENV{COMMIT}$2/;
s/version "[^"]+"/version "$ENV{VERSION}"/;
' "$FORMULA"
    grep -Fq "revision: \"$COMMIT\"" "$FORMULA" || die "formula revision update failed"
    grep -Fq "version \"$VERSION\"" "$FORMULA" || die "formula version update failed"
    if command -v ruby >/dev/null 2>&1; then ruby -c "$FORMULA"; fi

    git -C "$TAP_PATH" add "$FORMULA_REL"
    if ! git -C "$TAP_PATH" diff --cached --quiet; then
        git -C "$TAP_PATH" commit -m "chore: update zotero-pdf2zh-pro to $TAG"
        REPLACEMENT_TAP_PUSHED=1
        git -C "$TAP_PATH" push origin main
    fi
    TAP_COMMIT="$(git -C "$TAP_PATH" rev-parse HEAD)"

    TAP_RUN=""
    for _ in {1..30}; do
        TAP_RUN="$(gh run list --repo "$TAP_REPO" --workflow formula-checks.yml \
            --commit "$TAP_COMMIT" --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)"
        [[ -n "$TAP_RUN" ]] && break
        sleep 2
    done
    [[ -n "$TAP_RUN" ]] || die "Homebrew formula checks did not start for $TAP_COMMIT"
    uv run --no-project python scripts/release_control.py watch "$TAP_RUN" --repo "$TAP_REPO" --timeout 1200
    uv run --no-project python scripts/release_control.py record "$RELEASE_STATE" homebrew --field "runId=$TAP_RUN" --field "commit=$TAP_COMMIT" --field status=passed
fi

# All publication targets and checks have succeeded; retain the backup without rolling back.
REPLACEMENT_BACKUP=""
if [[ "$PUSH" -eq 1 ]]; then
    uv run --no-project python scripts/release_control.py record "$RELEASE_STATE" complete --field "githubRelease=$PUBLISH_RELEASE" --field "pypiUpload=$PUBLISH_PYPI" --field "homebrew=$UPDATE_TAP"
fi
echo "Released $TAG at $COMMIT"
if [[ -n "$REPLACE_EXISTING" ]]; then
    echo "PyPI distributions preserved from $REPLACE_EXISTING; client source $COMMIT"
fi
echo "Artifacts: $XPI $UPDATE_MANIFEST $WINDOWS_PACKAGE $WINDOWS_INSTALLER $WINDOWS_UPDATE_MANIFEST $SOURCE_ARCHIVE"
echo "SHA-256:"
echo "  $XPI_SHA256  $XPI"
echo "  $UPDATE_SHA256  $UPDATE_MANIFEST"
echo "  $WINDOWS_SHA256  $WINDOWS_PACKAGE"
echo "  $WINDOWS_INSTALLER_SHA256  $WINDOWS_INSTALLER"
echo "  $WINDOWS_UPDATE_SHA256  $WINDOWS_UPDATE_MANIFEST"
echo "  $SOURCE_SHA256  $SOURCE_ARCHIVE"
