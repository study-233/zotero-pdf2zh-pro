import { version } from "../../package.json";
import { getPref } from "../utils/prefs";
import { recordDiagnostic } from "./diagnostics";
import {
    identifyBackend,
    localEngineAddress,
    windowsInstallation,
    type BackendInstallation,
} from "./backendInstallation";

const release = "https://github.com/study-233/zotero-pdf2zh-pro/releases/";
const path = (root: string, name: string) =>
    PathUtils.join(root, "updates", name);
const read = async (root: string, name: string) =>
    JSON.parse(await IOUtils.readUTF8(path(root, name)).catch(() => "null"));
function address() {
    return String(getPref("new_serverip") || "").trim();
}
export function compareVersions(installed: string, target = version) {
    return Services.vc.compare(installed, target);
}
export async function windowsState(info: BackendInstallation) {
    const root = info.root!;
    const [bridge, policy, status, request, processing] = await Promise.all(
        [
            "plugin-management.json",
            "plugin-policy.json",
            "state.json",
            "plugin-request.json",
            "plugin-processing.json",
        ].map((n) => read(root, n)),
    );
    const current = compareVersions(info.version!) >= 0;
    const phase = status?.version === version ? status.phase : "";
    return {
        supported: bridge?.schema === 1 && bridge.version === info.version,
        follow: !!policy?.follow,
        current,
        waiting: !current && phase === "ready",
        working:
            !current &&
            (!!request ||
                !!processing ||
                ["preparing", "switching"].includes(phase)),
        failed: !current && ["failed", "network"].includes(phase),
        cancelled: policy?.skip === version,
    };
}
function startCenter(root: string) {
    const proc = Cc["@mozilla.org/process/util;1"].createInstance(
        Ci.nsIProcess,
    );
    proc.init(
        Zotero.File.pathToFile(
            PathUtils.join(root, "bin", "zotero-pdf2zh-pro.exe"),
        ),
    );
    proc.runw(false, ["--post-install", "--keep-stopped"], 2);
}
export async function setWindowsPolicy(
    root: string,
    follow: boolean,
    skip = "",
) {
    const file = path(root, "plugin-policy.json");
    await IOUtils.makeDirectory(PathUtils.parent(file)!, {
        permissions: 0o700,
    });
    await IOUtils.writeUTF8(file, JSON.stringify({ follow, skip }), {
        tmpPath: file + Services.uuid.generateUUID().toString(),
    });
    if (follow && !skip) startWindowsUpdates();
}
async function queue(
    root: string,
    action: "update" | "cancel" | "move",
    destination?: string,
    target = version,
) {
    const file = path(root, "plugin-request.json");
    const tmp = file + Services.uuid.generateUUID().toString();
    try {
        await IOUtils.writeUTF8(
            tmp,
            JSON.stringify({ action, target, destination }),
            { mode: "create" },
        );
        await IOUtils.move(tmp, file, { noOverwrite: true });
    } catch (error) {
        const pending = await read(root, "plugin-request.json");
        if (
            pending?.action !== action ||
            pending.target !== target ||
            pending.destination !== destination
        )
            throw error;
    } finally {
        await IOUtils.remove(tmp, { ignoreAbsent: true });
    }
    startCenter(root);
}
export async function windowsAction(
    info: BackendInstallation,
    action: "update" | "cancel" | "bootstrap" | "move",
    destination?: string,
) {
    const host = address();
    if (!localEngineAddress.test(host) || !Zotero.isWin)
        throw Error("External backend");
    const check = await windowsInstallation();
    if (!check || check.root !== info.root || check.version !== info.version)
        throw Error("Installation changed");
    let health;
    try {
        health = (
            await Zotero.HTTP.request(
                "GET",
                host.replace(/\/$/, "") + "/health",
                { responseType: "json", timeout: 3000, errorDelayMax: 0 },
            )
        ).response;
    } catch (error) {
        if ((error as { status?: number }).status) throw error;
    }
    if (
        address() !== host ||
        (await identifyBackend(host, health)).kind !== "windows"
    )
        throw Error("Backend changed");
    const state = await windowsState(info);
    if (action === "move" && destination && state.supported)
        return queue(check.root, "move", destination, check.version);
    if (action === "cancel") {
        await setWindowsPolicy(check.root, state.follow, version);
        return queue(check.root, "cancel");
    }
    if (state.current || state.working || state.waiting) return;
    if (action === "bootstrap") {
        if (
            health &&
            (health.status !== "ok" ||
                health.maintenance?.busy ||
                (health.maintenance?.idleSeconds ?? 0) < 60)
        )
            return "retry-idle";
        if (state.supported)
            throw Error("Control center already supports updates");
        const source = await Zotero.File.getContentsFromURLAsync(
            "chrome://pdf2zhpro/content/engine/windows-backend-upgrade.ps1",
        );
        const helper = PathUtils.join(
            Services.dirsvc.get("ProfD", Ci.nsIFile).path,
            "pdf2zh-windows-backend-upgrade.ps1",
        );
        await IOUtils.writeUTF8(helper, String(source), {
            tmpPath: helper + Services.uuid.generateUUID().toString(),
        });
        const { Subprocess } = ChromeUtils.importESModule(
            "resource://gre/modules/Subprocess.sys.mjs",
        );
        const system = Services.dirsvc.get("SysD", Ci.nsIFile).path;
        const proc = await Subprocess.call({
            command: PathUtils.join(
                system,
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe",
            ),
            arguments: [
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                helper,
                "-InstallRoot",
                check.root,
                "-TargetVersion",
                version,
            ],
            stderr: "stdout",
        });
        while (await proc.stdout.readString()) {
            /* Public feedback uses stable states only. */
        }
        if ((await proc.wait()).exitCode !== 0)
            throw Error("Control center upgrade failed");
        return;
    }
    if (!state.supported) throw Error("Control center upgrade required");
    // The native updater checks the same stable manifest and revalidates target and checksum.
    const manifest = (
        await Zotero.HTTP.request(
            "GET",
            release + "latest/download/windows-update.json",
            { responseType: "json", timeout: 15000, errorDelayMax: 0 },
        )
    ).response;
    if (manifest?.version !== version) return "unpublished";
    if (address() !== host) throw Error("Backend changed");
    await queue(check.root, "update");
}
let timer: ReturnType<typeof setTimeout> | undefined,
    generation = 0;
export function stopWindowsUpdates() {
    generation++;
    clearTimeout(timer);
}
export function startWindowsUpdates() {
    stopWindowsUpdates();
    if (!Zotero.isWin) return;
    const current = generation;
    const tick = async () => {
        try {
            if (localEngineAddress.test(address())) {
                const installed = await windowsInstallation();
                if (installed && current === generation) {
                    const info: BackendInstallation = {
                        kind: "windows",
                        ...installed,
                    };
                    const s = await windowsState(info);
                    if (
                        s.follow &&
                        s.supported &&
                        !s.current &&
                        !s.failed &&
                        !s.cancelled &&
                        !s.working &&
                        !s.waiting &&
                        current === generation
                    )
                        await windowsAction(info, "update");
                }
            }
        } catch {
            recordDiagnostic("backend_update_failed");
        } finally {
            if (current === generation)
                timer = setTimeout(() => void tick(), 3600000);
        }
    };
    void tick();
}
