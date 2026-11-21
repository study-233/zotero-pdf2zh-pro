import {
    windowsAction,
    windowsState,
    setWindowsPolicy,
} from "./windowsBackend";
import { recordDiagnostic } from "./diagnostics";
import { config, version } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { getLocaleID } from "../utils/locale";
import {
    identifyBackend,
    localEngineAddress,
    bindNewEngine,
    windowsInstallation,
    engineBindingFile,
    reserveMigration,
    type BackendInstallation,
} from "./backendInstallation";
import {
    engineUpdateAvailable,
    ensureEngine,
    legacyServiceInstalled,
    managedUpdateState,
    requestManagedUpdate,
    cancelManagedUpdate,
    runEngineManager,
} from "./managedEngine";
import type { ServerHealthResponse } from "./pdf2zhTypes";

export function bindBackendPane(win: Window, refresh: () => Promise<void>) {
    const el = (suffix: string) =>
        win.document.getElementById(
            "zotero-prefpane-" + config.addonRef + "-backend-" + suffix,
        )!;
    const label = (node: HTMLElement, key: string) =>
        node.setAttribute("data-l10n-id", getLocaleID("pref-backend-" + key));
    const text = (key: string) =>
        win.document.l10n!.formatValue(getLocaleID("pref-backend-" + key));
    const url = () =>
        String(getPref("new_serverip") || "")
            .trim()
            .replace(/\/+$/, "");
    let timer: number | undefined;
    let revision = 0,
        current: BackendInstallation | undefined,
        busy = false,
        closed = false;
    const feedback = (key: string) => {
        el("result").hidden = false;
        label(el("result"), key);
    };
    function checking() {
        revision++;
        win.clearTimeout(timer);
        current = undefined;
        el("panel").hidden = true;
    }
    async function show(address: string, health?: ServerHealthResponse) {
        const seq = ++revision;
        try {
            if (
                !address ||
                (typeof __devRuntime__ !== "undefined" &&
                    !!__devRuntime__ &&
                    /^http:\/\/(localhost|127\.0\.0\.1):8891\/?$/i.test(
                        address,
                    ))
            ) {
                el("panel").hidden = true;
                return;
            }
            const info = await identifyBackend(address, health);
            const update =
                info.kind === "managed" &&
                (await engineUpdateAvailable(info.root!));
            const ws =
                info.kind === "windows" ? await windowsState(info) : undefined;
            const supported =
                (Zotero.isWin &&
                    Services.appinfo.XPCOMABI.startsWith("x86_64")) ||
                (Zotero.isMac &&
                    Services.appinfo.XPCOMABI.startsWith("aarch64"));
            const canInstall =
                supported &&
                info.kind === "uninstalled" &&
                !(await legacyServiceInstalled());
            if (closed || seq !== revision || url() !== address) return;
            current = info;
            const isManaged = info.kind === "managed",
                isWindows = info.kind === "windows";
            el("panel").hidden = !(
                isManaged ||
                isWindows ||
                canInstall ||
                info.kind === "conflict" ||
                info.kind === "missing" ||
                info.kind === "external"
            );
            el("panel").toggleAttribute(
                "data-external",
                info.kind === "external",
            );
            label(el("source"), info.kind);
            el("result").hidden = !(
                isManaged ||
                isWindows ||
                info.kind === "external"
            );
            if (info.kind === "external")
                feedback(
                    !health
                        ? "unreachable"
                        : health.version &&
                            Services.vc.compare(health.version, version) < 0
                          ? "external-older"
                          : "external-connected",
                );
            for (const [name, path] of [
                ["location", info.root],
                [
                    "data",
                    info.root ? PathUtils.join(info.root, "data") : undefined,
                ],
            ] as const) {
                el(name + "-row").hidden = !path;
                el(name).textContent = path || "";
            }
            for (const [id, visible] of Object.entries({
                "select-managed": info.kind === "conflict",
                "select-windows": info.kind === "conflict",
                install: canInstall,
                choose: canInstall,
                move:
                    (isManaged && !update) ||
                    (isWindows &&
                        !!ws?.supported &&
                        ws.current &&
                        !ws.working &&
                        !ws.waiting),
                update:
                    (isManaged && update) ||
                    (isWindows && !ws?.current && !ws?.working && !ws?.waiting),
                center: isWindows,
                cancel:
                    !!ws?.waiting ||
                    (isManaged &&
                        update &&
                        managedUpdateState() === "deferred"),
            })) {
                el(id).hidden = !visible;
                (el(id) as HTMLButtonElement).disabled = busy;
            }
            label(
                el("update"),
                ws && !ws.supported
                    ? "upgrade"
                    : ws?.failed
                      ? "retry"
                      : "update",
            );
            el("follow-row").hidden = !isManaged && !ws?.supported;
            (el("follow") as HTMLInputElement).checked = ws
                ? ws.follow
                : getPref("managedFollowPlugin") !== false;
            if (!busy && isManaged)
                feedback(
                    !update
                        ? "current"
                        : managedUpdateState() === "deferred"
                          ? "waiting"
                          : managedUpdateState() === "fallback"
                            ? "restore"
                            : "ready",
                );
            if (ws && !busy)
                feedback(
                    ws.current
                        ? "current"
                        : ws.working
                          ? "working"
                          : ws.waiting
                            ? "waiting"
                            : ws.failed
                              ? "failed"
                              : ws.cancelled
                                ? "paused"
                                : "ready",
                );
            if (ws && (ws.working || ws.waiting))
                timer = win.setTimeout(() => void show(address, health), 5000);
        } catch {
            if (!closed && seq === revision) {
                el("panel").hidden = false;
                for (const id of [
                    "install",
                    "choose",
                    "move",
                    "update",
                    "center",
                    "cancel",
                    "follow-row",
                    "location-row",
                    "data-row",
                ])
                    el(id).hidden = true;
                el("source").textContent = "";
                feedback("failed");
            }
        }
    }
    async function action(id: string) {
        const info = current,
            address = url();
        if (!info || busy || !localEngineAddress.test(address)) return;
        busy = true;
        feedback("working");
        let result = "";
        try {
            if (
                info.kind === "conflict" &&
                (id === "select-managed" || id === "select-windows")
            ) {
                const choice = id === "select-managed" ? "managed" : "windows";
                const identity =
                    choice === "managed"
                        ? (
                              await IOUtils.readUTF8(
                                  PathUtils.join(info.root!, "owner"),
                              )
                          ).trim()
                        : (await windowsInstallation())?.root;
                if (!identity || address !== url()) return;
                setPref("backendSelection", choice + ":" + identity);
                setPref(
                    "engineManagement",
                    choice === "managed" ? "managed" : "external",
                );
            } else if (id === "center" && info.kind === "windows") {
                Zotero.File.pathToFile(
                    PathUtils.join(info.root!, "bin", "zotero-pdf2zh-pro.exe"),
                ).launch();
            } else if (
                info.kind === "windows" &&
                (id === "update" || id === "cancel" || id === "move")
            ) {
                const state = await windowsState(info);
                if (id === "move") {
                    const parent = await new ztoolkit.FilePicker(
                        (await text("pick")) || "",
                        "folder",
                    ).open();
                    if (!parent) return;
                    const dest = PathUtils.join(parent, "zotero-pdf2zh-pro");
                    if (
                        !win.confirm(
                            (await text("move")) +
                                "\n" +
                                info.root +
                                " → " +
                                dest,
                        )
                    )
                        return;
                    await windowsAction(info, "move", dest);
                } else {
                    const operation =
                        id === "cancel"
                            ? "cancel"
                            : state.supported
                              ? "update"
                              : "bootstrap";
                    if (
                        operation === "bootstrap" &&
                        !win.confirm((await text("upgrade")) + "\n" + info.root)
                    )
                        return;
                    const outcome = await windowsAction(info, operation);
                    result =
                        outcome === "retry-idle"
                            ? "retry-idle"
                            : outcome === "unpublished"
                              ? "unpublished"
                              : id === "cancel"
                                ? "paused"
                                : operation === "bootstrap"
                                  ? "complete"
                                  : "working";
                }
            } else if (
                (id === "install" || id === "choose") &&
                info.kind === "uninstalled"
            ) {
                let root = info.root!;
                if (id === "choose") {
                    const parent = await new ztoolkit.FilePicker(
                        (await text("pick")) || "",
                        "folder",
                    ).open();
                    if (!parent) return;
                    root = PathUtils.join(parent, "pdf2zh-engine");
                }
                const disk = Zotero.File.pathToFile(PathUtils.parent(root)!);
                const available = disk.diskSpaceAvailable;
                const space =
                    available >= 0
                        ? "\n" +
                          (await text("size")) +
                          " " +
                          (available / 1024 ** 3).toFixed(1) +
                          " GiB"
                        : "";
                if (
                    !win.confirm(
                        (await text("installconfirm")) +
                            "\n" +
                            root +
                            "\n" +
                            (await text("space-estimate")) +
                            space,
                    )
                )
                    return;
                if (
                    address !== url() ||
                    closed ||
                    (await legacyServiceInstalled())
                )
                    return;
                if (id === "choose") {
                    await bindNewEngine(root);
                }
                setPref("engineManagement", "managed");
                await ensureEngine(address, true, true);
            } else if (id === "move" && info.kind === "managed") {
                const parent = await new ztoolkit.FilePicker(
                    (await text("pick")) || "",
                    "folder",
                ).open();
                if (!parent) return;
                const dest = PathUtils.join(parent, "pdf2zh-engine");
                if (
                    !win.confirm(
                        (await text("migration")) +
                            "\n" +
                            info.root +
                            " → " +
                            dest,
                    ) ||
                    closed ||
                    address !== url()
                )
                    return;
                await reserveMigration(info.root!, dest);
                const state = await runEngineManager(dest, 8890, [
                    "--relocate-from",
                    info.root!,
                    "--binding",
                    engineBindingFile(),
                ]);
                result =
                    state === "ready"
                        ? "complete"
                        : state === "deferred"
                          ? "retry-migration"
                          : "failed";
            } else if (id === "cancel" && info.kind === "managed") {
                await cancelManagedUpdate();
                result = "paused";
            } else if (id === "update" && info.kind === "managed") {
                setPref("engineManagement", "managed");
                await requestManagedUpdate(address);
                const state = managedUpdateState();
                result =
                    state === "deferred"
                        ? "waiting"
                        : state === "fallback"
                          ? "restore"
                          : state === "ready"
                            ? "complete"
                            : "failed";
            }
        } catch {
            recordDiagnostic("backend_update_failed");
            result = "failed";
        } finally {
            busy = false;
            if (!closed && address === url()) {
                await refresh();
                if (result) feedback(result);
            }
            if (!closed)
                for (const id of [
                    "install",
                    "choose",
                    "move",
                    "update",
                    "center",
                    "cancel",
                ])
                    (el(id) as HTMLButtonElement).disabled = false;
        }
    }
    for (const id of [
        "install",
        "choose",
        "move",
        "update",
        "center",
        "cancel",
        "select-managed",
        "select-windows",
    ])
        el(id).addEventListener("click", () => void action(id));
    el("follow").addEventListener("change", () => {
        if (current?.kind === "windows" && current.root)
            void setWindowsPolicy(
                current.root,
                (el("follow") as HTMLInputElement).checked,
            )
                .then(refresh)
                .catch(() => feedback("failed"));
        if (current?.kind === "managed") {
            const follow = (el("follow") as HTMLInputElement).checked;
            setPref("managedFollowPlugin", follow);
            if (follow) {
                setPref("engineManagement", "managed");
                setPref("managedSkipRevision", "");
                void ensureEngine(url(), false)
                    .then(refresh)
                    .catch(() => feedback("failed"));
            }
        }
    });
    return {
        checking,
        show,
        close() {
            closed = true;
            win.clearTimeout(timer);
            revision++;
        },
    };
}
