export type InstallationStatus =
    | "notInstalled"
    | "current"
    | "updateAvailable"
    | "downgradeBlocked";

export type ServiceStatus = "stopped" | "running" | "portConflict";

export interface ControlState {
    installation: InstallationStatus;
    service: ServiceStatus;
    appVersion: string;
    installedVersion: string | null;
    serviceVersion: string | null;
    address: string;
    autostartEnabled: boolean;
    dataDir: string;
    logFile: string;
    controlLog: string;
    runningFromInstalledPath: boolean;
}

export interface UpdateCheck {
    available: boolean;
    currentVersion: string;
    latestVersion: string;
}

export type PrimaryAction = "install" | "upgrade" | "update" | "start" | "stop" | "refresh" | "none";

export interface ViewModel {
    badge: string;
    tone: "neutral" | "success" | "warning" | "danger";
    title: string;
    detail: string;
    primaryLabel: string;
    primaryAction: PrimaryAction;
    installed: boolean;
}

export function toViewModel(state: ControlState, update?: UpdateCheck | null): ViewModel {
    if (state.installation === "notInstalled") {
        return {
            badge: "未安装",
            tone: "neutral",
            title: "尚未安装本地翻译服务",
            detail: "安装过程会准备 uv、Python 3.13 和配套服务。",
            primaryLabel: "安装并启动",
            primaryAction: "install",
            installed: false,
        };
    }
    if (state.installation === "updateAvailable") {
        return {
            badge: "发现新版",
            tone: "warning",
            title: `可以升级到 ${state.appVersion}`,
            detail: `当前已安装 ${state.installedVersion ?? "未知版本"}，任务数据和自启选择会保留。`,
            primaryLabel: "升级并重启",
            primaryAction: "upgrade",
            installed: true,
        };
    }
    if (state.installation === "downgradeBlocked") {
        return {
            badge: "版本较旧",
            tone: "danger",
            title: "不能使用较旧的控制中心覆盖当前版本",
            detail: `当前已安装 ${state.installedVersion ?? "未知版本"}，请下载更新版本。`,
            primaryLabel: "重新检测",
            primaryAction: "refresh",
            installed: true,
        };
    }
    if (update?.available && state.runningFromInstalledPath) {
        return {
            badge: "发现新版",
            tone: "warning",
            title: `可以更新到 ${update.latestVersion}`,
            detail: "控制中心和服务端将一起更新，任务数据、日志和自启选择会保留。",
            primaryLabel: `更新到 v${update.latestVersion}`,
            primaryAction: "update",
            installed: true,
        };
    }
    if (state.service === "running") {
        return {
            badge: "运行中",
            tone: "success",
            title: "翻译服务已就绪",
            detail: "Zotero 现在可以连接本机翻译服务。",
            primaryLabel: "停止服务",
            primaryAction: "stop",
            installed: true,
        };
    }
    if (state.service === "portConflict") {
        return {
            badge: "端口冲突",
            tone: "danger",
            title: "8890 端口正由其他程序使用",
            detail: "为了安全，控制中心不会停止未知进程。请关闭占用程序后重试。",
            primaryLabel: "重新检测",
            primaryAction: "refresh",
            installed: true,
        };
    }
    return {
        badge: "已停止",
        tone: "neutral",
        title: "翻译服务未运行",
        detail: "启动后即可从 Zotero 提交翻译任务。",
        primaryLabel: "启动服务",
        primaryAction: "start",
        installed: true,
    };
}
