#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/release.sh <version> [--no-push] [--no-release] [--pypi] [--tap-path <path>] [--no-tap]

Build, validate, push, and publish a zotero-pdf2zh-next release.

Examples:
  scripts/release.sh 5.1.0
  scripts/release.sh 5.1.1 --no-release
  scripts/release.sh 5.1.1 --pypi
  scripts/release.sh 5.1.1 --tap-path ../homebrew-formula
  scripts/release.sh 5.1.1 --no-tap

The script updates the shared plugin/server version, runs validation, commits
the version bump, pushes main, creates the private v<version> GitHub release
with the XPI asset using CHANGELOG.md release notes, and updates the private
Homebrew source formula. PyPI publishing is opt-in.

Before running, add a CHANGELOG.md section like:
  ## v5.1.1 - YYYY-MM-DD

Unless --no-push or --no-tap is set, the script also updates the Homebrew tap
formula after the main repo push/release succeeds. The default tap path is the
adjacent ../homebrew-formula checkout.

PyPI publishing uses UV_PUBLISH_TOKEN when available. Otherwise the script
dispatches the trusted-publishing workflow. Use --pypi only after configuring
a package and Trusted Publisher owned by this private fork.
EOF
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

TEMP_PATHS=()
cleanup() {
    local path
    for path in "${TEMP_PATHS[@]-}"; do
        if [[ -n "$path" && -e "$path" ]]; then
            rm -rf "$path"
        fi
    done
}
trap cleanup EXIT

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

if [[ $# -gt 0 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

VERSION="$1"
shift
PUSH=1
PUBLISH_RELEASE=1
UPDATE_TAP=1
PUBLISH_PYPI=0
TAP_PATH=""
PYPI_TOKEN="${UV_PUBLISH_TOKEN:-}"
unset UV_PUBLISH_TOKEN
GITHUB_REPO="${GITHUB_REPO:-study-233/zotero-pdf2zh-next}"
HOMEBREW_TAP_REPO="${HOMEBREW_TAP_REPO:-study-233/homebrew-formula}"
HOMEBREW_TAP_NAME="${HOMEBREW_TAP_NAME:-study-233/formula}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)
            PUSH=0
            PUBLISH_RELEASE=0
            PUBLISH_PYPI=0
            ;;
        --no-release)
            PUBLISH_RELEASE=0
            ;;
        --pypi)
            PUBLISH_PYPI=1
            ;;
        --no-pypi)
            PUBLISH_PYPI=0
            ;;
        --tap-path)
            [[ $# -ge 2 ]] || die "--tap-path requires a path"
            TAP_PATH="$2"
            shift
            ;;
        --no-tap)
            UPDATE_TAP=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
    shift
done

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "version must look like semver, got: $VERSION"

for cmd in git gh node npx uv perl curl; do
    require_command "$cmd"
done
PNPM=(npx --yes pnpm@10.34.5)

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [[ -z "$TAP_PATH" ]]; then
    if [[ -d "$REPO_ROOT/../homebrew-formula/.git" ]]; then
        TAP_PATH="$REPO_ROOT/../homebrew-formula"
    fi
fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    [[ -n "$TAP_PATH" ]] || die "Homebrew tap path not found; use --tap-path <path> or --no-tap"
    [[ -d "$TAP_PATH/.git" ]] || die "Homebrew tap path is not a git repo: $TAP_PATH"
    [[ -f "$TAP_PATH/Formula/zotero-pdf2zh-next.rb" ]] ||
        die "Homebrew formula not found: $TAP_PATH/Formula/zotero-pdf2zh-next.rb"
    [[ "$(git -C "$TAP_PATH" branch --show-current)" == "main" ]] ||
        die "Homebrew tap must be on main: $TAP_PATH"
    [[ -z "$(git -C "$TAP_PATH" status --porcelain)" ]] ||
        die "Homebrew tap worktree is dirty; commit or stash changes first"
    require_command ruby
    TAP_REMOTE="$(git -C "$TAP_PATH" remote get-url origin)"
    [[ "$TAP_REMOTE" == "git@github.com:${HOMEBREW_TAP_REPO}.git" ]] ||
        die "Homebrew tap origin must be git@github.com:${HOMEBREW_TAP_REPO}.git, got: $TAP_REMOTE"
fi

BRANCH="$(git branch --show-current)"
[[ "$BRANCH" == "main" ]] || die "release must run from main, current branch: $BRANCH"

git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

TAG="v$VERSION"
PYPI_PACKAGE="zotero-pdf2zh-next"
PYPI_VERSION_URL="https://pypi.org/pypi/$PYPI_PACKAGE/$VERSION/json"
PYPI_CHECK_URL="https://pypi.org/simple/$PYPI_PACKAGE/"
PYPI_STATUS=""
PYPI_COMPLETE=0
PUBLISH_PYPI_VIA_GITHUB=0

pypi_release_complete() {
    local response
    response="$(curl -fsS "$PYPI_VERSION_URL")" || return 1
    printf '%s' "$response" | node -e '
const fs = require("fs");
const version = process.argv[1];
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const files = new Set(data.urls.map((item) => item.filename));
const expected = [
  `zotero_pdf2zh_next-${version}-py3-none-any.whl`,
  `zotero_pdf2zh_next-${version}.tar.gz`,
];
const missing = expected.filter((filename) => !files.has(filename));
if (missing.length > 0) {
  console.error(`PyPI release is missing: ${missing.join(", ")}`);
  process.exit(1);
}
' "$VERSION"
}

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if ! PYPI_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$PYPI_VERSION_URL")"; then
        die "failed to query PyPI: $PYPI_VERSION_URL"
    fi
    case "$PYPI_STATUS" in
        200)
            if pypi_release_complete; then
                PYPI_COMPLETE=1
                echo "PyPI release is complete; upload will be skipped: $PYPI_PACKAGE==$VERSION"
            else
                if [[ -z "$PYPI_TOKEN" ]]; then
                    [[ "$PUSH" -eq 1 ]] ||
                        die "PyPI release is incomplete; publishing without a token requires push access"
                    PUBLISH_PYPI_VIA_GITHUB=1
                fi
            fi
            ;;
        404)
            if [[ -z "$PYPI_TOKEN" ]]; then
                [[ "$PUSH" -eq 1 ]] ||
                    die "PyPI publishing without UV_PUBLISH_TOKEN requires push access"
                PUBLISH_PYPI_VIA_GITHUB=1
            fi
            ;;
        *)
            die "unexpected PyPI response for $PYPI_PACKAGE==$VERSION: HTTP $PYPI_STATUS"
            ;;
    esac
fi

CHANGELOG_SECTION="$(awk -v tag="$TAG" '
    $0 ~ "^## " tag "([[:space:]]|-|$)" {
        found = 1
        print
        next
    }
    found && /^## / {
        exit
    }
    found {
        print
    }
' CHANGELOG.md)"
[[ -n "$(printf '%s' "$CHANGELOG_SECTION" | tr -d '[:space:]')" ]] ||
    die "CHANGELOG.md must contain a section starting with: ## $TAG"

node -e '
const fs = require("fs");
const version = process.argv[1];
const file = "plugin/package.json";
const data = JSON.parse(fs.readFileSync(file, "utf8"));
data.version = version;
fs.writeFileSync(file, JSON.stringify(data, null, 4) + "\n");
' "$VERSION"

VERSION="$VERSION" perl -0pi -e 's/version = "[^"]+"/version = "$ENV{VERSION}"/' server/pyproject.toml
VERSION="$VERSION" perl -0pi -e 's/VERSION = "[^"]+"/VERSION = "$ENV{VERSION}"/' server/server.py
node -e '
const fs = require("fs");
const version = process.argv[1];
function replaceExactlyOnce(file, pattern, replacement, label) {
  const text = fs.readFileSync(file, "utf8");
  const matches = [...text.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(`expected one ${label} marker, found ${matches.length}`);
  }
  fs.writeFileSync(file, text.replace(pattern, replacement));
}

replaceExactlyOnce(
  "README.md",
  /(<!-- release-version --> `)[^`]+(`)/g,
  `$1${version}$2`,
  "README release-version",
);
replaceExactlyOnce(
  "server/uv.lock",
  /(\[\[package\]\]\nname = "zotero-pdf2zh-next"\nversion = ")[^"]+(")/g,
  `$1${version}$2`,
  "server lock version",
);
' "$VERSION"

UV_LOCK_INDEX="$(awk -F '"' '/^source = \{ registry = / { print $2; exit }' server/uv.lock)"
[[ -n "$UV_LOCK_INDEX" ]] || die "server/uv.lock does not contain a registry source"
UV_DEFAULT_INDEX="$UV_LOCK_INDEX" uv --directory server lock --locked
UV_DEFAULT_INDEX="$UV_LOCK_INDEX" \
    uv run --directory server --locked python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
uv run --no-project python scripts/check_pypi_artifacts.py server/dist "$VERSION"

PYPI_SMOKE_ROOT="$(mktemp -d)"
TEMP_PATHS+=("$PYPI_SMOKE_ROOT")
UV_NO_CONFIG=1 uv venv --python 3.13 "$PYPI_SMOKE_ROOT/venv"
env -u UV_INDEX_URL -u PIP_INDEX_URL \
    UV_NO_CONFIG=1 UV_DEFAULT_INDEX=https://pypi.org/simple \
    uv pip install \
    --python "$PYPI_SMOKE_ROOT/venv/bin/python" \
    "server/dist/zotero_pdf2zh_next-$VERSION-py3-none-any.whl"
"$PYPI_SMOKE_ROOT/venv/bin/python" scripts/check_installed_runtime.py "$VERSION"

CI=true "${PNPM[@]}" --dir plugin install --frozen-lockfile
"${PNPM[@]}" --dir plugin lint:check
"${PNPM[@]}" --dir plugin build

node -e '
const fs = require("fs");
const version = process.argv[1];
const manifest = JSON.parse(fs.readFileSync("plugin/build/addon/manifest.json", "utf8"));
if (manifest.version !== version) {
  throw new Error(`manifest version ${manifest.version} != ${version}`);
}
if (manifest.applications?.zotero?.update_url) {
  throw new Error("private build must not contain an anonymous update URL");
}
' "$VERSION"

git add README.md plugin/package.json server/pyproject.toml server/server.py server/uv.lock
if ! git diff --cached --quiet; then
    git commit -m "chore: release $TAG"
fi

COMMIT="$(git rev-parse HEAD)"

GITHUB_RELEASE_EXISTS=0
if [[ "$PUBLISH_RELEASE" -eq 1 || "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 ]]; then
    if ! REMOTE_TAG_REFS="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")"; then
        die "failed to query remote GitHub tag: $TAG"
    fi
    REMOTE_TAG_COMMIT="$(printf '%s\n' "$REMOTE_TAG_REFS" | awk '
        $2 ~ /\^\{\}$/ { peeled = $1 }
        $2 !~ /\^\{\}$/ { direct = $1 }
        END { print peeled ? peeled : direct }
    ')"
    if [[ -n "$REMOTE_TAG_COMMIT" ]]; then
        [[ "$REMOTE_TAG_COMMIT" == "$COMMIT" ]] ||
            die "GitHub tag $TAG points to $REMOTE_TAG_COMMIT, expected $COMMIT"
    fi
    if [[ "$PUBLISH_RELEASE" -eq 1 ]] &&
        gh release view "$TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
        [[ -n "${REMOTE_TAG_COMMIT:-}" ]] || die "GitHub release exists without a resolvable tag: $TAG"
        GITHUB_RELEASE_EXISTS=1
    fi
fi

if [[ "$PUSH" -eq 1 ]]; then
    git push origin "$BRANCH"
fi

if [[ "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 && -z "$REMOTE_TAG_COMMIT" ]]; then
    if LOCAL_TAG_COMMIT="$(git rev-parse "$TAG^{commit}" 2>/dev/null)"; then
        [[ "$LOCAL_TAG_COMMIT" == "$COMMIT" ]] ||
            die "local tag $TAG points to $LOCAL_TAG_COMMIT, expected $COMMIT"
    else
        git tag -a "$TAG" -m "$TAG" "$COMMIT"
    fi
    git push origin "$TAG"
    REMOTE_TAG_COMMIT="$COMMIT"
fi

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if [[ "$PYPI_COMPLETE" -eq 0 ]]; then
        if [[ "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 ]]; then
            gh workflow run publish-pypi.yml \
                --repo "$GITHUB_REPO" \
                --ref "$BRANCH" \
                -f tag="$TAG"
        else
            UV_PUBLISH_TOKEN="$PYPI_TOKEN" \
                uv publish --check-url "$PYPI_CHECK_URL" server/dist/*
        fi
    fi

    PYPI_VERIFIED=0
    for _ in {1..60}; do
        if pypi_release_complete; then
            PYPI_VERIFIED=1
            break
        fi
        sleep 5
    done
    [[ "$PYPI_VERIFIED" -eq 1 ]] ||
        die "PyPI did not expose $PYPI_PACKAGE==$VERSION after publishing"
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
    NOTES_FILE="$(mktemp)"
    TEMP_PATHS+=("$NOTES_FILE")
    printf '%s\n\nPrivate build: download the XPI while signed in and install it manually in Zotero.\n' \
        "$CHANGELOG_SECTION" >"$NOTES_FILE"

    if [[ "$GITHUB_RELEASE_EXISTS" -eq 1 ]]; then
        echo "Reusing existing GitHub release: $TAG"
        if ! gh release view "$TAG" --repo "$GITHUB_REPO" --json assets --jq '.assets[].name' |
            grep -Fxq 'zotero-pdf2zh-next.xpi'; then
            gh release upload "$TAG" plugin/build/zotero-pdf2zh-next.xpi --repo "$GITHUB_REPO"
        fi
    else
        gh release create "$TAG" \
            plugin/build/zotero-pdf2zh-next.xpi \
            --repo "$GITHUB_REPO" \
            --target "$COMMIT" \
            --title "$TAG" \
            --notes-file "$NOTES_FILE" \
            --latest
    fi

fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    FORMULA_REL="Formula/zotero-pdf2zh-next.rb"
    FORMULA="$TAP_PATH/$FORMULA_REL"
    SOURCE_URL="git@github.com:${GITHUB_REPO}.git"
    SOURCE_URL="$SOURCE_URL" COMMIT="$COMMIT" VERSION="$VERSION" perl -0pi -e '
s{url "[^"]+"[^\n]*}{url "$ENV{SOURCE_URL}", using: :git, revision: "$ENV{COMMIT}"};
s/version "[^"]+"/version "$ENV{VERSION}"/;
' "$FORMULA"

    git -C "$TAP_PATH" diff -- "$FORMULA_REL"
    ruby -c "$FORMULA"
    if command -v brew >/dev/null 2>&1; then
        HOMEBREW_NO_AUTO_UPDATE=1 brew style "$FORMULA"
    fi

    git -C "$TAP_PATH" add "$FORMULA_REL"
    if ! git -C "$TAP_PATH" diff --cached --quiet; then
        git -C "$TAP_PATH" commit -m "chore: update zotero-pdf2zh-next to $TAG"
    fi
    git -C "$TAP_PATH" push origin main

    if command -v brew >/dev/null 2>&1; then
        BREW_TAP_PATH="$(brew --repository "$HOMEBREW_TAP_NAME" 2>/dev/null || true)"
        if [[ -n "$BREW_TAP_PATH" && -d "$BREW_TAP_PATH/.git" ]]; then
            git -C "$BREW_TAP_PATH" pull --ff-only
        fi
        HOMEBREW_NO_AUTO_UPDATE=1 brew readall "$HOMEBREW_TAP_NAME"
        HOMEBREW_NO_AUTO_UPDATE=1 brew install --build-from-source --formula --dry-run \
            "$HOMEBREW_TAP_NAME/zotero-pdf2zh-next"
    else
        echo "Skipping Homebrew validation because brew is not installed" >&2
    fi
fi

echo "Released $TAG at $COMMIT"
