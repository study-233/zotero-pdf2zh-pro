import { getPref } from "../utils/prefs";
import type { ServerHealthResponse } from "./pdf2zhTypes";

export const localEngineAddress =
    /^http:\/\/(?:localhost|127\.0\.0\.1):8890\/?$/i;

function profilePath(name: string) {
    return PathUtils.join(Services.dirsvc.get("ProfD", Ci.nsIFile).path, name);
}
export function engineBindingFile() {
    return profilePath("pdf2zh-engine-location.json");
}
export async function engineBinding(): Promise<{
    root: string;
    owner?: string;
}> {
    const file = engineBindingFile();
    if (!(await IOUtils.exists(file)))
        return { root: profilePath("pdf2zh-engine") };
    const entry = JSON.parse(await IOUtils.readUTF8(file));
    if (
        typeof entry.root !== "string" ||
        !PathUtils.isAbsolute(entry.root) ||
        !/^[a-f0-9]{32,64}$/.test(entry.owner)
    )
        throw Error("Invalid engine location");
    return { root: entry.root, owner: entry.owner };
}
export async function bindNewEngine(root: string) {
    await IOUtils.makeDirectory(root, {
        ignoreExisting: false,
        permissions: 0o700,
    });
    const owner = Services.uuid.generateUUID().toString().replace(/[{}-]/g, "");
    await IOUtils.writeUTF8(PathUtils.join(root, "owner"), owner, {
        mode: "create",
    });
    await IOUtils.writeUTF8(
        engineBindingFile(),
        JSON.stringify({ root, owner }),
        { mode: "create" },
    );
}

export async function reserveMigration(source: string, destination: string) {
    const from = Zotero.File.pathToFile(source);
    const to = Zotero.File.pathToFile(destination);
    if (from.equals(to) || from.contains(to) || to.contains(from))
        throw Error("Overlapping engine directories");
    const binding = await engineBinding();
    if (binding.root !== source) throw Error("Engine location changed");
    const owner = (
        await IOUtils.readUTF8(PathUtils.join(source, "owner"))
    ).trim();
    const marker = PathUtils.join(destination, ".pdf2zh-relocation.json");
    const reservation = JSON.stringify({
        source,
        owner,
        binding: engineBindingFile(),
    });
    if (await IOUtils.exists(destination)) {
        if ((await IOUtils.readUTF8(marker).catch(() => "")) !== reservation)
            throw Error("Destination already in use");
        return;
    }
    await IOUtils.makeDirectory(destination, {
        ignoreExisting: false,
        permissions: 0o700,
    });
    await IOUtils.writeUTF8(marker, reservation, { mode: "create" });
}

export type BackendInstallation = {
    kind:
        | "managed"
        | "windows"
        | "external"
        | "uninstalled"
        | "conflict"
        | "missing";
    root?: string;
    version?: string;
};

// Windows records the chosen location in HKCU even when its service is stopped.
export async function windowsInstallation() {
    if (!Zotero.isWin) return;
    const key = Cc["@mozilla.org/windows-registry-key;1"].createInstance(
        Ci.nsIWindowsRegKey,
    );
    let root: string;
    try {
        key.open(
            Ci.nsIWindowsRegKey.ROOT_KEY_CURRENT_USER!,
            "Software\\zotero-pdf2zh-pro",
            Ci.nsIWindowsRegKey.ACCESS_READ!,
        );
        root = key.readStringValue("InstallRoot");
    } catch {
        return;
    } finally {
        key.close();
    }
    if (!/^[a-z]:[\\/]/i.test(root)) return;
    const installed = await IOUtils.readUTF8(
        PathUtils.join(root, "installed-version.txt"),
    ).catch(() => "");
    if (!/^\d+\.\d+\.\d+$/.test(installed.trim())) return;
    const required = ["zotero-pdf2zh-pro.exe", "common.ps1"];
    for (const name of required)
        if (!(await IOUtils.exists(PathUtils.join(root, "bin", name)))) return;
    return { root, version: installed.trim() };
}

export async function identifyBackend(
    url: string,
    health?: ServerHealthResponse,
): Promise<BackendInstallation> {
    if (!localEngineAddress.test(url)) return { kind: "external" };
    const binding = await engineBinding();
    const root = binding.root;
    const owner = (
        await IOUtils.readUTF8(PathUtils.join(root, "owner")).catch(() => "")
    ).trim();
    if (binding.owner && binding.owner !== owner)
        return { kind: "missing", root };
    if (health?.managedOwner) {
        return owner && owner === health.managedOwner
            ? { kind: "managed", root, version: health.version }
            : { kind: "external" };
    }
    const windows = await windowsInstallation();
    if (windows && health?.workspace?.path) {
        const normalize = (s: string) =>
            s
                .replace(/^\\\\\?\\/, "")
                .replace(/\//g, "\\")
                .replace(/\\+$/, "")
                .toLowerCase();
        if (
            normalize(health.workspace.path) ===
            normalize(PathUtils.join(windows.root, "data"))
        )
            return { kind: "windows", ...windows };
    }
    if (health) return { kind: "external", version: health.version };
    if (owner && windows) {
        const selected = String(getPref("backendSelection") || "");
        if (selected === "managed:" + owner) return { kind: "managed", root };
        if (selected === "windows:" + windows.root)
            return { kind: "windows", ...windows };
        return { kind: "conflict", root };
    }
    if (owner) return { kind: "managed", root };
    if (windows) return { kind: "windows", ...windows };

    if (getPref("engineManagement") === "external") return { kind: "external" };
    return { kind: "uninstalled", root };
}
