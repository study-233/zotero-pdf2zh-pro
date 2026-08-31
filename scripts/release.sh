#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/release.sh <version> [--no-push] [--no-release] [--no-pypi] [--no-tap] [--tap-path <path>]

Build, validate, and publish a unified zotero-pdf2zh-pro release.

The release includes the Zotero XPI, PyPI wheel/sdist, Windows helper ZIP,
a local corresponding-source archive, and an optional update to the private
source-only Homebrew tap. Add a matching CHANGELOG.md section first.
EOF
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

TEMP_PATHS=()
cleanup() {
    local path
    for path in "${TEMP_PATHS[@]}"; do
        if [[ -n "$path" && -e "$path" ]]; then
            rm -rf -- "$path"
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
[[ $# -ge 1 ]] || { usage; exit 2; }

VERSION="$1"
shift
PUSH=1
PUBLISH_RELEASE=1
PUBLISH_PYPI=1
UPDATE_TAP=1
TAP_PATH=""
PYPI_TOKEN="${UV_PUBLISH_TOKEN:-}"
unset UV_PUBLISH_TOKEN

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)
            PUSH=0
            PUBLISH_RELEASE=0
            PUBLISH_PYPI=0
            UPDATE_TAP=0
            ;;
        --no-release) PUBLISH_RELEASE=0 ;;
        --no-pypi) PUBLISH_PYPI=0 ;;
        --no-tap) UPDATE_TAP=0 ;;
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

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "version must look like semver, got: $VERSION"

for cmd in git gh node npx uv perl curl; do
    require_command "$cmd"
done

PRODUCT="zotero-pdf2zh-pro"
TAG="v$VERSION"
MAIN_REPO="study-233/zotero-pdf2zh-pro"
TAP_REPO="study-233/homebrew-formula"
TAP_SSH="git@github.com:study-233/homebrew-formula.git"
PYPI_VERSION_URL="https://pypi.org/pypi/$PRODUCT/$VERSION/json"
PYPI_CHECK_URL="https://pypi.org/simple/$PRODUCT/"
WINDOWS_PACKAGE="dist/$PRODUCT-windows-x64.zip"
SOURCE_ARCHIVE="dist/$PRODUCT-$VERSION-source.zip"
XPI="plugin/build/$PRODUCT.xpi"
PNPM=(npx --yes pnpm@10.34.5)

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

ORIGIN_URL="$(git remote get-url origin)"
case "$ORIGIN_URL" in
    "https://github.com/$MAIN_REPO"|"https://github.com/$MAIN_REPO.git"|"git@github.com:$MAIN_REPO"|"git@github.com:$MAIN_REPO.git") ;;
    *) die "origin must be $MAIN_REPO, got: $ORIGIN_URL" ;;
esac

[[ "$(git branch --show-current)" == "main" ]] || die "release must run from main"
git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

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
const file = "plugin/package.json";
const data = JSON.parse(fs.readFileSync(file, "utf8"));
data.version = version;
fs.writeFileSync(file, JSON.stringify(data, null, 4) + "\n");
NODE

VERSION="$VERSION" perl -0pi -e 's/version = "[^"]+"/version = "$ENV{VERSION}"/' server/pyproject.toml
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
  /(\[\[package\]\]\nname = "zotero-pdf2zh-pro"\nversion = ")[^"]+(")/g,
  `$1${version}$2`,
  "server lock version",
);
NODE

UV_DEFAULT_INDEX=https://pypi.org/simple uv --directory server lock --locked
UV_DEFAULT_INDEX=https://pypi.org/simple \
    uv run --directory server --locked python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
uv run --no-project python scripts/check_pypi_artifacts.py server/dist "$VERSION"

SMOKE_ROOT="$(mktemp -d)"
TEMP_PATHS+=("$SMOKE_ROOT")
UV_NO_CONFIG=1 uv venv --python 3.13 "$SMOKE_ROOT/venv"
if [[ -x "$SMOKE_ROOT/venv/bin/python" ]]; then
    SMOKE_PYTHON="$SMOKE_ROOT/venv/bin/python"
else
    SMOKE_PYTHON="$SMOKE_ROOT/venv/Scripts/python.exe"
fi
env -u UV_INDEX_URL -u PIP_INDEX_URL UV_NO_CONFIG=1 UV_DEFAULT_INDEX=https://pypi.org/simple \
    uv pip install --python "$SMOKE_PYTHON" \
    "server/dist/zotero_pdf2zh_pro-$VERSION-py3-none-any.whl"
"$SMOKE_PYTHON" scripts/check_installed_runtime.py "$VERSION"

CI=true "${PNPM[@]}" --dir plugin install --frozen-lockfile
"${PNPM[@]}" --dir plugin lint:check
rm -rf -- plugin/build
"${PNPM[@]}" --dir plugin build

node - "$VERSION" <<'NODE'
const fs = require("fs");
const version = process.argv[2];
const manifest = JSON.parse(fs.readFileSync("plugin/build/addon/manifest.json", "utf8"));
const zotero = manifest.applications.zotero;
if (manifest.name !== "zotero-pdf2zh-pro" || manifest.version !== version) throw new Error("manifest identity mismatch");
if (zotero.id !== "zotero-pdf2zh-pro@study-233") throw new Error("manifest add-on ID mismatch");
if (Object.hasOwn(zotero, "update_url")) throw new Error("private add-on must not contain update_url");
NODE

if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check_windows_scripts.ps1
fi
uv run --no-project python scripts/build_windows_package.py --version "$VERSION"

git add README.md plugin/package.json server/pyproject.toml server/server.py server/uv.lock scripts/windows/common.ps1
if ! git diff --cached --quiet; then
    git commit -m "chore: release $TAG"
fi
COMMIT="$(git rev-parse HEAD)"

mkdir -p dist
git archive --format=zip --prefix="$PRODUCT-$VERSION/" \
    --output="$SOURCE_ARCHIVE" "$COMMIT"

for artifact in "$XPI" "$WINDOWS_PACKAGE" "$SOURCE_ARCHIVE"; do
    [[ -f "$artifact" ]] || die "missing release artifact: $artifact"
done

REMOTE_TAG_REFS="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")"
REMOTE_TAG_COMMIT="$(printf '%s\n' "$REMOTE_TAG_REFS" | awk '
    $2 ~ /\^\{\}$/ { peeled = $1 }
    $2 !~ /\^\{\}$/ { direct = $1 }
    END { print peeled ? peeled : direct }
')"
if [[ -n "$REMOTE_TAG_COMMIT" && "$REMOTE_TAG_COMMIT" != "$COMMIT" ]]; then
    die "remote tag $TAG points to $REMOTE_TAG_COMMIT, expected $COMMIT"
fi

if [[ "$PUSH" -eq 1 ]]; then
    git push origin main
fi

pypi_release_complete() {
    local response
    response="$(curl -fsS "$PYPI_VERSION_URL")" || return 1
    printf '%s' "$response" | node -e '
const fs = require("fs");
const version = process.argv[1];
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const files = new Set(data.urls.map((item) => item.filename));
for (const expected of [`zotero_pdf2zh_pro-${version}-py3-none-any.whl`, `zotero_pdf2zh_pro-${version}.tar.gz`]) {
  if (!files.has(expected)) process.exit(1);
}
' "$VERSION"
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
WINDOWS_SHA256="$(sha256_file "$WINDOWS_PACKAGE")"
SOURCE_SHA256="$(sha256_file "$SOURCE_ARCHIVE")"

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if ! pypi_release_complete; then
        if [[ -n "$PYPI_TOKEN" ]]; then
            UV_PUBLISH_TOKEN="$PYPI_TOKEN" uv publish --check-url "$PYPI_CHECK_URL" server/dist/*
        else
            if [[ -z "$REMOTE_TAG_COMMIT" ]]; then
                git tag -a "$TAG" -m "$TAG" "$COMMIT"
                git push origin "$TAG"
                REMOTE_TAG_COMMIT="$COMMIT"
            fi
            gh workflow run publish-pypi.yml --repo "$MAIN_REPO" --ref main -f tag="$TAG"
        fi
    fi
    PYPI_VERIFIED=0
    for _ in {1..60}; do
        if pypi_release_complete; then PYPI_VERIFIED=1; break; fi
        sleep 5
    done
    [[ "$PYPI_VERIFIED" -eq 1 ]] || die "PyPI did not expose $PRODUCT==$VERSION"
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
    NOTES_FILE="$(mktemp)"
    TEMP_PATHS+=("$NOTES_FILE")
    printf '%s\n\n' "$CHANGELOG_SECTION" >"$NOTES_FILE"
    printf '\nSHA-256:\n\n' >>"$NOTES_FILE"
    printf -- '- `%s`  `%s`\n' "$XPI_SHA256" "$(basename "$XPI")" >>"$NOTES_FILE"
    printf -- '- `%s`  `%s`\n' "$WINDOWS_SHA256" "$(basename "$WINDOWS_PACKAGE")" >>"$NOTES_FILE"
    if gh release view "$TAG" --repo "$MAIN_REPO" >/dev/null 2>&1; then
        gh release upload "$TAG" "$XPI" "$WINDOWS_PACKAGE" \
            --repo "$MAIN_REPO" --clobber
    else
        gh release create "$TAG" "$XPI" "$WINDOWS_PACKAGE" \
            --repo "$MAIN_REPO" --target "$COMMIT" --title "$TAG" \
            --notes-file "$NOTES_FILE" --latest
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
            git clone "$TAP_SSH" "$TAP_PATH"
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
s/(url "git\@github\.com:study-233\/zotero-pdf2zh-pro\.git", using: :git, revision: ")[^"]+("\n)/$1$ENV{COMMIT}$2/;
s/version "[^"]+"/version "$ENV{VERSION}"/;
' "$FORMULA"
    grep -Fq "revision: \"$COMMIT\"" "$FORMULA" || die "formula revision update failed"
    grep -Fq "version \"$VERSION\"" "$FORMULA" || die "formula version update failed"
    if command -v ruby >/dev/null 2>&1; then ruby -c "$FORMULA"; fi

    git -C "$TAP_PATH" add "$FORMULA_REL"
    if ! git -C "$TAP_PATH" diff --cached --quiet; then
        git -C "$TAP_PATH" commit -m "chore: update zotero-pdf2zh-pro to $TAG"
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
    gh run watch "$TAP_RUN" --repo "$TAP_REPO" --exit-status
fi

echo "Released $TAG at $COMMIT"
echo "Artifacts: $XPI $WINDOWS_PACKAGE $SOURCE_ARCHIVE"
echo "SHA-256:"
echo "  $XPI_SHA256  $XPI"
echo "  $WINDOWS_SHA256  $WINDOWS_PACKAGE"
echo "  $SOURCE_SHA256  $SOURCE_ARCHIVE"
