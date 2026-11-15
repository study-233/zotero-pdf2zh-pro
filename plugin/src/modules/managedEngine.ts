import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import { recordDiagnostic } from "./diagnostics";

const assets = "chrome://pdf2zhpro/content/engine/";
const local = /^http:\/\/(127\.0\.0\.1|localhost):8890\/?$/i;
type Package = {
    url: string;
    sha256: string;
    executableSha256: string;
    size: number;
};
type Manifest = {
    id: string;
    python: string;
    uv: string;
    platforms: Record<string, Package>;
};
let operation: Promise<void> | undefined;
let retry: ReturnType<typeof setTimeout> | undefined;
let stopped = false;
let lastResult = "";
let retryAt = 0;
export function engineStatusMessage() {
    return ["fallback", "deferred"].includes(lastResult)
        ? getString("engine-" + lastResult)
        : "";
}
function setupError(key: string) {
    return Object.assign(new Error(getString(key)), {
        name: "EngineSetupError",
    });
}
async function legacyServiceInstalled() {
    const product = "zotero-pdf2zh-pro";
    const home = Services.dirsvc.get("Home", Ci.nsIFile).path;
    let paths: string[] = [];
    if (Zotero.isMac)
        paths = [
            "/opt/homebrew/var/" + product,
            "/usr/local/var/" + product,
            PathUtils.join(home, "Library", "Application Support", product),
            PathUtils.join(
                home,
                "Library",
                "LaunchAgents",
                "homebrew.mxcl." + product + ".plist",
            ),
        ];
    if (Zotero.isWin) {
        const registry = Cc[
            "@mozilla.org/windows-registry-key;1"
        ].createInstance(Ci.nsIWindowsRegKey);
        try {
            registry.open(
                Ci.nsIWindowsRegKey.ROOT_KEY_CURRENT_USER!,
                "Software",
                Ci.nsIWindowsRegKey.ACCESS_READ!,
            );
            if (registry.hasChild(product)) return true;
        } finally {
            registry.close();
        }
        paths = [
            PathUtils.join(
                Services.dirsvc.get("LocalAppData", Ci.nsIFile).path,
                product,
            ),
        ];
    }
    return (await Promise.all(paths.map((path) => IOUtils.exists(path)))).some(
        Boolean,
    );
}
let manifestPromise: Promise<Manifest> | undefined;

function manifest() {
    return (manifestPromise ??= Zotero.File.getContentsFromURLAsync(
        assets + "manifest.json",
    )
        .then((raw) => JSON.parse(String(raw)) as Manifest)
        .catch((error) => {
            manifestPromise = undefined;
            throw error;
        }));
}

export function initializeEngine(existing: boolean) {
    stopped = false;
    if (!getPref("engineManagement"))
        setPref("engineManagement", existing ? "external" : "auto");
    if (getPref("engineManagement") === "managed") {
        void ensureEngine(String(getPref("new_serverip")), false).catch(() =>
            schedule(900000),
        );
    }
}

export function stopEngineManager() {
    stopped = true;
    clearTimeout(retry);
}

function schedule(delay = 60000) {
    if (stopped) return;
    clearTimeout(retry);
    retryAt = Date.now() + delay;
    retry = setTimeout(() => {
        void ensureEngine(String(getPref("new_serverip")), false).catch(() =>
            schedule(900000),
        );
    }, delay);
}

async function asset(path: string) {
    const response = await Zotero.HTTP.request("GET", path, {
        responseType: "arraybuffer",
        timeout: 180000,
        errorDelayMax: 0,
    });
    return new Uint8Array(response.response);
}

function platformKey() {
    const abi = Services.appinfo.XPCOMABI;
    if (Zotero.isWin && abi.startsWith("x86_64")) return "windows-x64";
    if (Zotero.isMac && abi.startsWith("aarch64")) return "macos-arm64";
    throw setupError("engine-platform");
}

async function nativeCommand(
    command: string,
    args: string[],
    workdir: string,
    environment: Record<string, string> = {},
) {
    const { Subprocess } = ChromeUtils.importESModule(
        "resource://gre/modules/Subprocess.sys.mjs",
    );
    const child = await Subprocess.call({
        command,
        arguments: args,
        workdir,
        environmentAppend: true,
        environment: {
            PYTHONUTF8: "1",
            PYTHONPATH: "",
            PYTHONHOME: "",
            VIRTUAL_ENV: "",
            ...environment,
        },
        stderr: "stdout",
    });
    let tail = "";
    for (;;) {
        const chunk = await child.stdout.readString();
        if (!chunk) break;
        tail = (tail + chunk).slice(-2048);
    }
    const result = tail.trim().split(/\r?\n/).pop();
    if (
        (await child.wait()).exitCode !== 0 ||
        !result ||
        !["ready", "external", "deferred", "fallback"].includes(result)
    )
        throw new Error("Engine setup failed");
    return result;
}

// All subprocess arguments are passed separately. No shell or global Python installation.
export async function runEngineManager(root: string, port: number) {
    const spec = await manifest();
    const pkg = spec.platforms[platformKey()];
    await IOUtils.makeDirectory(root, { permissions: 0o700 });
    const stage = await IOUtils.createUniqueDirectory(root, "install");
    const exe = Zotero.isWin ? "uv.exe" : "uv";
    const tool = PathUtils.join(root, spec.uv + "-" + exe);
    try {
        const digest = () =>
            IOUtils.computeHexDigest(tool, "sha256").catch(() => "");
        if ((await digest()) !== pkg.executableSha256) {
            const wheel = PathUtils.join(stage, "bootstrap.whl");
            const bytes = await asset(pkg.url);
            await IOUtils.write(wheel, bytes);
            if (
                bytes.length !== pkg.size ||
                (await IOUtils.computeHexDigest(wheel, "sha256")) !== pkg.sha256
            )
                throw new Error("Bootstrap checksum mismatch");
            const zip = Cc["@mozilla.org/libjar/zip-reader;1"].createInstance(
                Ci.nsIZipReader,
            );
            const extracted = PathUtils.join(stage, exe);
            try {
                zip.open(Zotero.File.pathToFile(wheel));
                zip.extract(
                    "uv-" + spec.uv + ".data/scripts/" + exe,
                    Zotero.File.pathToFile(extracted),
                );
            } finally {
                zip.close();
            }
            await IOUtils.setPermissions(extracted, 0o700);
            try {
                await IOUtils.move(extracted, tool, { noOverwrite: false });
            } catch (error) {
                if ((await digest()) !== pkg.executableSha256) throw error;
            }
            if ((await digest()) !== pkg.executableSha256)
                throw new Error("Invalid bootstrap executable");
        }
        for (const name of ["manifest.json", "manager.py", "service.zip"])
            await IOUtils.write(
                PathUtils.join(stage, name),
                await asset(assets + name),
            );
        return await nativeCommand(
            tool,
            [
                "run",
                "--no-project",
                "--managed-python",
                "--python",
                spec.python,
                "--",
                "python",
                PathUtils.join(stage, "manager.py"),
                root,
                stage,
                tool,
                "--port",
                String(port),
            ],
            stage,
            {
                UV_NO_CONFIG: "1",
                UV_PYTHON_INSTALL_DIR: PathUtils.join(root, "python"),
                UV_CACHE_DIR: PathUtils.join(root, "cache"),
                PYTHONNOUSERSITE: "1",
            },
        );
    } finally {
        await IOUtils.remove(stage, { recursive: true, ignoreAbsent: true });
    }
}

async function prepare(url: string, interactive: boolean, force: boolean) {
    const mode = getPref("engineManagement");
    let health;
    try {
        const response = await Zotero.HTTP.request(
            "GET",
            url.replace(/\/$/, "") + "/health",
            { responseType: "json", timeout: 4000, errorDelayMax: 0 },
        );
        health = response.response;
        if (!health?.version || !["ok", "degraded"].includes(health.status))
            return;
    } catch (error) {
        // An HTTP error belongs to the listener already on this port.
        if ((error as { status?: number }).status) return;
    }
    const root = PathUtils.join(
        Services.dirsvc.get("ProfD", Ci.nsIFile).path,
        "pdf2zh-engine",
    );
    if (health) {
        const ownerPath = PathUtils.join(root, "owner");
        if (
            !health.managedOwner ||
            !(await IOUtils.exists(ownerPath)) ||
            (await IOUtils.readUTF8(ownerPath)).trim() !== health.managedOwner
        ) {
            lastResult = "external";
            setPref("engineManagement", "external");
            return;
        }
        if (health.managedRevision === (await manifest()).id) {
            lastResult = "ready";
            return;
        }
        if (!force && lastResult === "fallback" && Date.now() < retryAt) return;
        if (health.maintenance?.busy || health.status === "degraded") {
            lastResult = "deferred";
            schedule();
            return;
        }
    }
    if (!health && mode === "auto" && (await legacyServiceInstalled())) {
        setPref("engineManagement", "external");
        throw setupError("engine-legacy");
    }
    if (
        !health &&
        mode === "managed" &&
        (await IOUtils.exists(PathUtils.join(root, "active")))
    ) {
        for (const pointer of ["active", "previous"]) {
            const active = (
                await IOUtils.readUTF8(PathUtils.join(root, pointer)).catch(
                    () => "",
                )
            ).trim();
            if (!/^[a-f0-9]{64}$/.test(active)) continue;
            const folder = PathUtils.join(root, "versions", active);
            const python = PathUtils.join(
                folder,
                ".venv",
                Zotero.isWin ? "Scripts" : "bin",
                Zotero.isWin ? "python.exe" : "python",
            );
            if (!(await IOUtils.exists(python))) continue;
            try {
                const state = await nativeCommand(
                    python,
                    [
                        PathUtils.join(folder, "manager.py"),
                        "restart",
                        root,
                        "8890",
                    ],
                    folder,
                );
                lastResult = state;
                if (state === "external")
                    setPref("engineManagement", "external");
                if (
                    state === "fallback" ||
                    (state === "ready" && active !== (await manifest()).id)
                )
                    schedule();
                return;
            } catch {
                // The installer can repair a missing runtime or restore the previous one.
            }
        }
    }
    platformKey();
    if (mode !== "managed") {
        if (
            !interactive ||
            !Zotero.getMainWindow().confirm(getString("engine-confirm"))
        )
            throw setupError("engine-cancelled");
        setPref("engineManagement", "managed");
    }
    const progress = interactive
        ? new ztoolkit.ProgressWindow(getString("engine-title"), {
              closeOnClick: true,
          })
        : undefined;
    progress
        ?.createLine({ text: getString("engine-preparing"), type: "default" })
        .show();
    try {
        const state = await runEngineManager(root, 8890);
        lastResult = state;
        if (state === "external") setPref("engineManagement", "external");
        if (state === "deferred" || state === "fallback") {
            recordDiagnostic(
                state === "deferred"
                    ? "managed_engine_deferred"
                    : "managed_engine_fallback",
            );
            schedule(state === "fallback" ? 900000 : 60000);
        }
    } catch {
        recordDiagnostic("managed_engine_failed");
        if (
            health &&
            (
                await Zotero.HTTP.request(
                    "GET",
                    url.replace(/\/$/, "") + "/health",
                    { responseType: "json", timeout: 4000, errorDelayMax: 0 },
                ).catch(() => undefined)
            )?.response?.managedOwner === health.managedOwner
        ) {
            lastResult = "fallback";
            schedule(900000);
            return;
        }
        throw setupError("engine-failed");
    } finally {
        progress?.close();
    }
}

export function ensureEngine(
    url: string,
    interactive = true,
    force = false,
): Promise<void> {
    const mode = getPref("engineManagement");
    if (stopped || !local.test(url) || (mode !== "auto" && mode !== "managed"))
        return Promise.resolve();
    return (operation ??= prepare(url, interactive, force).finally(() => {
        operation = undefined;
    }));
}
