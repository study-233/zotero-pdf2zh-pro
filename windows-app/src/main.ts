import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import "./styles.css";
import { ControlState, PrimaryAction, UpdateCheck, productRootForParent, toViewModel } from "./state";

const byId = <T extends HTMLElement>(id: string): T => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing element: ${id}`);
    return element as T;
};

const statusBadge = byId<HTMLSpanElement>("status-badge");
const statusTitle = byId<HTMLParagraphElement>("status-title");
const statusDetail = byId<HTMLParagraphElement>("status-detail");
const address = byId<HTMLElement>("service-address");
const primary = byId<HTMLButtonElement>("primary-action");
const autostart = byId<HTMLInputElement>("autostart-toggle");
const openLog = byId<HTMLButtonElement>("open-log");
const openData = byId<HTMLButtonElement>("open-data");
const uninstall = byId<HTMLButtonElement>("uninstall");
const checkUpdate = byId<HTMLButtonElement>("check-update");
const updateStatus = byId<HTMLParagraphElement>("update-status");
const versionLabel = byId<HTMLSpanElement>("version-label");
const operationPanel = byId<HTMLDivElement>("operation-panel");
const operationOutput = byId<HTMLPreElement>("operation-output");
const errorMessage = byId<HTMLParagraphElement>("error-message");
const installRoot = byId<HTMLElement>("install-root");
const installLocationHint = byId<HTMLElement>("install-location-hint");
const chooseLocation = byId<HTMLButtonElement>("choose-location");

let currentState: ControlState | null = null;
let currentUpdate: UpdateCheck | null = null;
let currentAction: PrimaryAction = "none";
let busy = false;
let selectedInstallRoot: string | null = null;

function showError(message: unknown): void {
    errorMessage.textContent = message instanceof Error ? message.message : String(message);
    errorMessage.hidden = false;
}

function clearError(): void {
    errorMessage.hidden = true;
    errorMessage.textContent = "";
}

function setBusy(value: boolean, label?: string): void {
    busy = value;
    primary.disabled = value || currentAction === "none";
    if (value && label) primary.textContent = label;
    autostart.disabled = value || !currentState || currentState.installation !== "current";
    openLog.disabled = value || !currentState;
    openData.disabled = value || !currentState;
    uninstall.disabled = value || !currentState || currentState.installation === "notInstalled";
    checkUpdate.disabled = value || !currentState?.runningFromInstalledPath;
    chooseLocation.disabled = value || !currentState || (
        currentState.installation !== "notInstalled" && !currentState.canRelocate
    );
}

function render(state: ControlState): void {
    currentState = state;
    const view = toViewModel(state, currentUpdate);
    currentAction = view.primaryAction;
    statusBadge.textContent = view.badge;
    statusBadge.className = `status-badge ${view.tone}`;
    statusTitle.textContent = view.title;
    statusDetail.textContent = view.detail;
    address.textContent = state.address;
    primary.textContent = view.primaryLabel;
    primary.disabled = busy || view.primaryAction === "none";
    autostart.checked = state.autostartEnabled;
    autostart.disabled = busy || state.installation !== "current" || !state.runningFromInstalledPath;
    openLog.disabled = busy;
    openData.disabled = busy;
    uninstall.disabled = busy || state.installation === "notInstalled";
    checkUpdate.disabled = busy || !state.runningFromInstalledPath;
    if (state.installation === "notInstalled") {
        selectedInstallRoot ??= state.installRoot;
        installRoot.textContent = selectedInstallRoot;
        installLocationHint.textContent = "控制中心、运行时、数据和日志都安装到这里";
        chooseLocation.textContent = "选择";
        chooseLocation.disabled = busy;
    } else {
        selectedInstallRoot = null;
        installRoot.textContent = state.installRoot;
        installLocationHint.textContent = state.canRelocate
            ? "可安全迁移全部程序和任务数据"
            : "请从已安装的控制中心更改位置";
        chooseLocation.textContent = "更改";
        chooseLocation.disabled = busy || !state.canRelocate;
    }
    versionLabel.textContent = `控制中心 ${state.appVersion} · 服务 ${state.serviceVersion ?? state.installedVersion ?? "未安装"}`;
    if (state.lastOperationError) showError(state.lastOperationError);
}

async function checkForUpdates(silent: boolean): Promise<void> {
    if (busy || !currentState?.runningFromInstalledPath) return;
    if (!silent) {
        clearError();
        checkUpdate.disabled = true;
        checkUpdate.textContent = "正在检查…";
    }
    try {
        currentUpdate = await invoke<UpdateCheck>("check_for_update");
        updateStatus.textContent = currentUpdate.available
            ? `发现稳定版本 v${currentUpdate.latestVersion}。`
            : `已是最新稳定版本 v${currentUpdate.currentVersion}。`;
        checkUpdate.textContent = "重新检查";
        render(currentState);
    } catch (error) {
        updateStatus.textContent = "暂时无法检查更新，不影响本地服务使用。";
        checkUpdate.textContent = "重试检查";
        if (!silent) showError(error);
    } finally {
        checkUpdate.disabled = busy || !currentState?.runningFromInstalledPath;
    }
}

async function refresh(): Promise<void> {
    if (busy) return;
    try {
        clearError();
        render(await invoke<ControlState>("get_state"));
    } catch (error) {
        showError(error);
    }
}

async function runOperation(command: string, pendingLabel: string, args?: Record<string, unknown>): Promise<void> {
    clearError();
    operationPanel.hidden = false;
    operationOutput.textContent = "";
    setBusy(true, pendingLabel);
    try {
        const state = await invoke<ControlState>(command, args);
        render(state);
    } catch (error) {
        showError(error);
    } finally {
        setBusy(false);
        await refresh();
    }
}

primary.addEventListener("click", async () => {
    if (busy) return;
    if (currentAction === "install" || currentAction === "upgrade") {
        if (
            currentAction === "upgrade" &&
            !window.confirm("确认升级并重启吗？任务数据、日志和自启选择都会保留。")
        ) {
            return;
        }
        await runOperation(
            "install_or_upgrade",
            currentAction === "install" ? "正在安装…" : "正在升级…",
            { installRoot: currentAction === "install" ? selectedInstallRoot : currentState?.installRoot ?? null },
        );
    } else if (currentAction === "update") {
        if (!window.confirm(
            `确认更新到 v${currentUpdate?.latestVersion ?? "最新版本"} 并重启吗？任务数据、日志和自启选择都会保留。`,
        )) return;
        clearError();
        operationPanel.hidden = false;
        operationOutput.textContent = "准备下载 Windows 更新…\n";
        setBusy(true, "正在准备更新…");
        try {
            await invoke("download_and_apply_update");
        } catch (error) {
            showError(error);
            setBusy(false);
            await refresh();
        }
    } else if (currentAction === "start") {
        await runOperation("start_server", "正在启动…");
    } else if (currentAction === "stop") {
        await runOperation("stop_server", "正在停止…");
    } else {
        await refresh();
    }
});

autostart.addEventListener("change", async () => {
    const desired = autostart.checked;
    setBusy(true, primary.textContent ?? undefined);
    try {
        await invoke("set_autostart", { enabled: desired });
        await refresh();
    } catch (error) {
        autostart.checked = !desired;
        showError(error);
    } finally {
        setBusy(false);
    }
});

byId<HTMLButtonElement>("copy-address").addEventListener("click", async () => {
    await navigator.clipboard.writeText(currentState?.address ?? "http://127.0.0.1:8890");
});
openLog.addEventListener("click", () => invoke("open_log").catch(showError));
openData.addEventListener("click", () => invoke("open_data_dir").catch(showError));
checkUpdate.addEventListener("click", () => void checkForUpdates(false));
chooseLocation.addEventListener("click", async () => {
    if (busy || !currentState) return;
    clearError();
    let selected: string | string[] | null;
    try {
        selected = await open({
            directory: true,
            multiple: false,
            title: currentState.installation === "notInstalled" ? "选择安装位置" : "选择新的安装位置",
        });
    } catch (error) {
        showError(error);
        return;
    }
    if (typeof selected !== "string") return;
    const target = productRootForParent(selected);
    if (currentState.installation === "notInstalled") {
        selectedInstallRoot = target;
        installRoot.textContent = target;
        return;
    }
    if (!window.confirm(
        `确认将控制中心、运行时和全部任务数据迁移到：\n\n${target}\n\n迁移时服务会暂时停止，并打开命令行窗口显示进度。`,
    )) return;
    setBusy(true, "正在准备迁移…");
    try {
        await invoke("relocate_installation", { destinationRoot: target });
    } catch (error) {
        showError(error);
        setBusy(false);
    }
});
byId<HTMLButtonElement>("clear-output").addEventListener("click", () => {
    operationOutput.textContent = "";
});

uninstall.addEventListener("click", async () => {
    if (!window.confirm("确定要卸载 zotero-pdf2zh-pro 吗？默认会保留任务数据和日志。")) return;
    const purge = window.confirm("是否同时永久删除任务数据、翻译结果和日志？\n\n选择“取消”会保留这些数据。 ");
    setBusy(true, "正在卸载…");
    try {
        await invoke("uninstall_product", { purgeData: purge });
    } catch (error) {
        showError(error);
        setBusy(false);
    }
});

await listen<{ line: string }>("operation-log", ({ payload }) => {
    operationPanel.hidden = false;
    operationOutput.textContent += `${payload.line}\n`;
    operationOutput.scrollTop = operationOutput.scrollHeight;
});

await listen<{ phase: string; downloaded: number; total: number }>("update-progress", ({ payload }) => {
    const labels: Record<string, string> = {
        checking: "正在确认最新版本…",
        downloading: payload.total > 0
            ? `正在下载更新… ${Math.floor((payload.downloaded / payload.total) * 100)}%`
            : "正在下载更新…",
        verifying: "正在校验更新包…",
        ready: "校验完成，正在重启安装…",
    };
    const label = labels[payload.phase] ?? "正在更新…";
    primary.textContent = label;
    operationOutput.textContent += `${label}\n`;
    operationOutput.scrollTop = operationOutput.scrollHeight;
});

await refresh();
void checkForUpdates(true);
window.setInterval(refresh, 3000);
