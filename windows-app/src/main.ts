import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./styles.css";
import { ControlState, PrimaryAction, toViewModel } from "./state";

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
const versionLabel = byId<HTMLSpanElement>("version-label");
const operationPanel = byId<HTMLDivElement>("operation-panel");
const operationOutput = byId<HTMLPreElement>("operation-output");
const errorMessage = byId<HTMLParagraphElement>("error-message");

let currentState: ControlState | null = null;
let currentAction: PrimaryAction = "none";
let busy = false;

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
}

function render(state: ControlState): void {
    currentState = state;
    const view = toViewModel(state);
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
    versionLabel.textContent = `控制中心 ${state.appVersion} · 服务 ${state.serviceVersion ?? state.installedVersion ?? "未安装"}`;
}

async function refresh(): Promise<void> {
    if (busy) return;
    try {
        render(await invoke<ControlState>("get_state"));
        clearError();
    } catch (error) {
        showError(error);
    }
}

async function runOperation(command: string, pendingLabel: string): Promise<void> {
    clearError();
    operationPanel.hidden = false;
    operationOutput.textContent = "";
    setBusy(true, pendingLabel);
    try {
        const state = await invoke<ControlState>(command);
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
        await runOperation("install_or_upgrade", currentAction === "install" ? "正在安装…" : "正在升级…");
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

await refresh();
window.setInterval(refresh, 3000);
