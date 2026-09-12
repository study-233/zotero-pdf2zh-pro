import { config } from "../../package.json";
import { getString } from "../utils/locale";
import { getPref } from "../utils/prefs";
import {
    PluginTask,
    ServerConfig,
    ServerTaskEvent,
    ServerTaskSnapshot,
    ServerTaskStatus,
    ServerTaskList,
    ServerSyncMetadata,
} from "./pdf2zhTypes";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { ServerTaskClient } from "./serverTaskClient";
import { TaskEventStream } from "./taskEventStream";
import { ZoteroTaskImporter } from "./zoteroTaskImporter";

type TaskDialogArgs = {
    _initPromise: any;
    getTasks: () => PluginTask[];
    hasActiveTasks: () => boolean;
    onTasksChanged: (listener: () => void) => () => void;
    refreshTasks: () => Promise<void>;
    cancelTask: (taskId: string) => Promise<void>;
    retryTask: (taskId: string) => Promise<void>;
    repairTask: (taskId: string) => Promise<void>;
    retryFailedTasks: () => Promise<void>;
    deleteTask: (taskId: string) => Promise<void>;
    clearFailedTasks: () => Promise<void>;
    getEventStreamState: () => {
        connected: number;
        total: number;
        hasErrors: boolean;
    };
};

const ACTIVE_STATUSES: ServerTaskStatus[] = ["queued", "running", "cancelling"];

type ServerSyncState = {
    instanceId?: string;
    generation: number;
    watermark: number;
    deleted: Map<string, number>;
    retiredInstances: Set<string>;
};

export class PDF2zhTaskManager {
    private static tasks = new Map<string, PluginTask>();
    private static localTasksLoaded = false;
    private static savedBindings = "";
    private static changeSequence = 0;
    private static taskChanges = new Map<string, number>();
    private static serverSync = new Map<string, ServerSyncState>();

    private static loadLocalTasks(): void {
        if (this.localTasksLoaded) return;
        this.localTasksLoaded = true;
        try {
            const raw = Zotero.Prefs.get(
                `${config.prefsPrefix}.taskBindings`,
                true,
            );
            const tasks = typeof raw === "string" ? JSON.parse(raw) : [];
            for (const task of Array.isArray(tasks) ? tasks : []) {
                if (
                    typeof task.taskId !== "string" ||
                    typeof task.itemID !== "number" ||
                    typeof task.serverUrl !== "string"
                )
                    continue;
                if (task.importState === "importing")
                    task.importState = "pending";
                this.tasks.set(task.taskId, task);
            }
        } catch (error) {
            ztoolkit.log("读取任务附件关联失败", error);
        }
    }

    private static saveLocalTasks(): void {
        if (!this.localTasksLoaded) return;
        const tasks = [...this.tasks.values()].filter(
            (task) => task.source === "local" && task.itemID,
        );
        const signature = JSON.stringify(
            tasks.map((task) => [
                task.taskId,
                task.itemID,
                task.serverUrl,
                task.attempt,
                task.importState,
                task.importedOutputs,
                task.qualitySummary,
            ]),
        );
        if (signature === this.savedBindings) return;
        try {
            Zotero.Prefs.set(
                `${config.prefsPrefix}.taskBindings`,
                JSON.stringify(tasks),
                true,
            );
            this.savedBindings = signature;
        } catch (error) {
            ztoolkit.log("保存任务附件关联失败", error);
        }
    }
    private static pollPromise: Promise<void> | null = null;
    private static refreshAgain = false;
    private static taskListeners = new Set<() => void>();
    private static dialogWindow: Window | undefined;
    private static eventStream = new TaskEventStream({
        onTaskEvent: (serverUrl, event) =>
            PDF2zhTaskManager.handleServerTaskEvent(serverUrl, event),
        onStateChange: (serverUrl, state) => {
            PDF2zhTaskManager.notifyTaskListeners();
            if (state === "open") {
                PDF2zhTaskManager.syncState(serverUrl).generation += 1;
                void PDF2zhTaskManager.refreshTasks();
            }
        },
    });
    private static importer = new ZoteroTaskImporter({
        getTask: (taskId) => PDF2zhTaskManager.tasks.get(taskId),
        updateTask: (taskId, patch) =>
            PDF2zhTaskManager.updateLocalTask(taskId, patch),
        onTaskImported: (taskId) =>
            PDF2zhTaskManager.notifyTranslationCompleted(taskId),
    });

    static async processWorker() {
        this.loadLocalTasks();
        const pane = ztoolkit.getGlobal("ZoteroPane");
        const selectedItems = pane.getSelectedItems();
        if (selectedItems.length === 0) {
            ztoolkit.getGlobal("alert")("请先选择一个条目或附件。");
            return;
        }

        const progressWindow = new ztoolkit.ProgressWindow(
            "zotero-pdf2zh-pro 任务",
        ).createLine({
            text: "正在提交翻译任务...",
            type: "default",
            progress: 0,
        });
        progressWindow.show();

        this.openWindow();

        let submitted = 0;
        const errors: string[] = [];
        const total = selectedItems.length;
        let serverConfig: ServerConfig;
        try {
            serverConfig = PDF2zhHelperFactory.getServerConfig();
            if (!serverConfig.apiConfig)
                throw new Error("请先在设置中选择翻译配置。");
        } catch (error) {
            ztoolkit.getGlobal("alert")(String(error));
            progressWindow.close();
            return;
        }

        if (serverConfig.outputModes.length === 0) {
            ztoolkit.getGlobal("alert")("请至少选择一种输出PDF模式。");
            return;
        }

        for (let index = 0; index < selectedItems.length; index++) {
            const item = selectedItems[index];
            try {
                await this.submitTask(item, serverConfig);
                submitted += 1;
            } catch (error) {
                const message =
                    error instanceof Error ? error.message : String(error);
                errors.push(message);
            }

            progressWindow.changeLine({
                text: `已提交 ${index + 1}/${total} 个任务...`,
                type: errors.length > 0 ? "warning" : "default",
                progress: Math.round(((index + 1) / total) * 100),
            });
        }

        await this.refreshTasks();

        progressWindow.changeLine({
            text: `任务已提交：成功 ${submitted}，失败 ${errors.length}`,
            type: errors.length > 0 ? "warning" : "success",
            progress: 100,
        });

        if (errors.length > 0) {
            ztoolkit.getGlobal("alert")(
                `部分任务提交失败：\n${errors.slice(0, 5).join("\n")}`,
            );
        }
    }

    static openWindow() {
        this.loadLocalTasks();
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.focus();
            return;
        }

        const windowArgs: TaskDialogArgs = {
            _initPromise: Zotero.Promise.defer(),
            getTasks: () => this.getTasks(),
            hasActiveTasks: () => this.hasActiveTasks(),
            onTasksChanged: (listener: () => void) =>
                this.onTasksChanged(listener),
            refreshTasks: () => this.refreshTasks(),
            cancelTask: (taskId: string) => this.cancelTask(taskId),
            retryTask: (taskId: string) => this.retryTask(taskId),
            repairTask: (taskId: string) => this.repairTask(taskId),
            retryFailedTasks: () => this.retryFailedTasks(),
            deleteTask: (taskId: string) => this.deleteTask(taskId),
            clearFailedTasks: () => this.clearFailedTasks(),
            getEventStreamState: () => this.getEventStreamState(),
        };

        const dialogWindow = Zotero.getMainWindow().openDialog(
            `chrome://${config.addonRef}/content/taskManager.xhtml`,
            `${config.addonRef}-taskManager`,
            "chrome,centerscreen,resizable,status,dialog=no,width=980,height=640",
            windowArgs,
        );
        if (!dialogWindow) {
            return;
        }

        this.dialogWindow = dialogWindow;
        this.ensureEventStreams();
        dialogWindow.addEventListener("unload", () => {
            if (this.dialogWindow === dialogWindow) {
                this.dialogWindow = undefined;
                this.ensureEventStreams();
            }
        });
    }

    static closeWindow() {
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.close();
        }
        this.dialogWindow = undefined;
        this.ensureEventStreams();
    }

    static getTasks(): PluginTask[] {
        return Array.from(this.tasks.values()).sort((left, right) =>
            right.createdAt.localeCompare(left.createdAt),
        );
    }

    static hasActiveTasks(): boolean {
        return Array.from(this.tasks.values()).some(
            (task) =>
                ACTIVE_STATUSES.includes(task.status) ||
                (task.status === "completed" &&
                    (task.importState === "pending" ||
                        task.importState === "importing")),
        );
    }

    static onTasksChanged(listener: () => void): () => void {
        this.taskListeners.add(listener);
        return () => {
            this.taskListeners.delete(listener);
        };
    }

    static async refreshTasks(): Promise<void> {
        this.loadLocalTasks();
        if (this.pollPromise) {
            this.refreshAgain = true;
            return this.pollPromise;
        }

        this.pollPromise = (async () => {
            do {
                this.refreshAgain = false;
                await this.refreshTasksInternal();
            } while (this.refreshAgain);
        })();
        try {
            await this.pollPromise;
        } finally {
            this.pollPromise = null;
        }
    }

    static async cancelTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        const generation = this.syncState(task.serverUrl).generation;
        const snapshot = await ServerTaskClient.cancelTask(
            task.serverUrl,
            taskId,
        );
        if (
            snapshot &&
            this.canApplyResponse(task.serverUrl, generation, snapshot)
        ) {
            this.upsertTask(snapshot, task.serverUrl);
        }
    }

    static async repairTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) throw new Error("任务不存在");
        if (!task.itemID) {
            const matches: Zotero.Item[] = [];
            for (const library of Zotero.Libraries.getAll()) {
                for (const item of await Zotero.Items.getAll(
                    library.libraryID,
                )) {
                    if (!item.isAttachment()) continue;
                    const path = await item.getFilePathAsync();
                    if (path && PathUtils.filename(path) === task.fileName)
                        matches.push(item);
                }
            }
            if (matches.length !== 1)
                throw new Error(
                    "无法唯一匹配原始 PDF 附件，请从原附件重新提交翻译任务。",
                );
            this.updateLocalTask(taskId, {
                itemID: matches[0].id,
                source: "local",
            });
        }
        const generation = this.syncState(task.serverUrl).generation;
        const snapshot = await ServerTaskClient.repairTask(
            task.serverUrl,
            taskId,
        );
        if (
            snapshot &&
            this.canApplyResponse(task.serverUrl, generation, snapshot)
        )
            this.upsertTask(snapshot, task.serverUrl);
        this.ensureEventStreams();
    }

    static async retryTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        if (task.status === "incomplete") return this.repairTask(taskId);
        if (task.status === "completed" && task.importState === "failed") {
            this.updateLocalTask(taskId, {
                importState: "pending",
                importError: undefined,
            });
            await this.importer.importTaskOutputs(taskId);
            return;
        }

        const generation = this.syncState(task.serverUrl).generation;
        const snapshot = await ServerTaskClient.retryTask(
            task.serverUrl,
            taskId,
        );
        if (
            snapshot &&
            this.canApplyResponse(task.serverUrl, generation, snapshot)
        ) {
            this.upsertTask(snapshot, task.serverUrl);
        }
        this.ensureEventStreams();
    }

    static async retryFailedTasks(): Promise<void> {
        const failedTasks = this.getTasks().filter(
            (task) =>
                task.status === "failed" ||
                task.status === "incomplete" ||
                (task.status === "completed" && task.importState === "failed"),
        );
        for (const task of failedTasks) {
            await this.retryTask(task.taskId);
        }
    }

    static async deleteTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }

        const generation = this.syncState(task.serverUrl).generation;
        const event = await ServerTaskClient.deleteTask(task.serverUrl, taskId);
        if (this.canApplyResponse(task.serverUrl, generation, event))
            this.handleServerTaskEvent(task.serverUrl, event);
        this.ensureEventStreams();
    }

    static async clearFailedTasks(): Promise<void> {
        const failedTasks = this.getTasks().filter(
            (task) => task.status === "failed",
        );
        if (failedTasks.length === 0) {
            return;
        }

        const serverUrls = new Set(failedTasks.map((task) => task.serverUrl));
        for (const serverUrl of serverUrls) {
            const generation = this.syncState(serverUrl).generation;
            await ServerTaskClient.clearFailedTasks(serverUrl);
            if (generation !== this.syncState(serverUrl).generation) continue;

            for (const task of failedTasks) {
                if (
                    task.serverUrl === serverUrl &&
                    this.tasks.get(task.taskId) === task
                ) {
                    this.removeLocalTask(task.taskId);
                }
            }
        }

        this.notifyTasksChanged();
        this.ensureEventStreams();
        await this.refreshTasks();
    }

    static getEventStreamState(): {
        connected: number;
        total: number;
        hasErrors: boolean;
    } {
        return this.eventStream.getSummary();
    }

    private static async submitTask(item: Zotero.Item, config: ServerConfig) {
        this.loadLocalTasks();
        const fileData = await PDF2zhHelperFactory.prepareFileData(item);
        const requestBody = PDF2zhHelperFactory.buildTaskRequestBody(
            fileData,
            config,
        );

        const generation = this.syncState(config.serverUrl).generation;
        const task = await ServerTaskClient.createTask(
            config.serverUrl,
            requestBody,
        );
        if (this.canApplyResponse(config.serverUrl, generation, task)) {
            this.upsertTask(task, config.serverUrl, {
                itemID: item.id,
                source: "local",
                importState: "pending",
            });
        } else {
            // A restarted server can already have restored this submission.
            // Its old response can supply the binding, never its old state.
            const current = this.tasks.get(task.taskId);
            if (current?.serverUrl === config.serverUrl && !current.itemID) {
                this.updateLocalTask(task.taskId, {
                    itemID: item.id,
                    source: "local",
                    importState:
                        current.importState === "none"
                            ? "pending"
                            : current.importState,
                });
            }
        }
        this.ensureEventStreams();
    }

    private static async refreshTasksInternal(): Promise<void> {
        const serverUrls = new Set<string>();
        const currentServerUrl = getPref("new_serverip")?.toString() || "";
        if (currentServerUrl) {
            serverUrls.add(currentServerUrl);
        }
        for (const task of this.tasks.values()) {
            if (task.serverUrl) {
                serverUrls.add(task.serverUrl);
            }
        }

        for (const serverUrl of serverUrls) {
            const startedAt = this.changeSequence;
            const before = new Map(this.tasks);
            const state = this.syncState(serverUrl);
            const generation = state.generation;
            let list: ServerTaskList;
            try {
                list = await ServerTaskClient.listTasks(serverUrl);
            } catch (_error) {
                continue;
            }
            if (generation !== state.generation) {
                this.refreshAgain = true;
                continue;
            }
            if (!this.acceptInstance(serverUrl, list)) continue;
            const versioned =
                list.serverInstanceId !== undefined &&
                list.revision !== undefined;
            if (versioned && list.revision! < state.watermark) continue;
            const snapshots = list.tasks;
            const serverTaskIds = new Set(
                snapshots.map((snapshot) => snapshot.taskId),
            );
            snapshots.forEach((snapshot) => {
                if (
                    !this.tasks.has(snapshot.taskId) &&
                    (this.taskChanges.get(snapshot.taskId) || 0) > startedAt
                )
                    return;
                this.upsertTask(snapshot, serverUrl, {}, true);
            });
            for (const task of this.getTasks()) {
                if (task.serverUrl !== serverUrl) {
                    continue;
                }
                const newerThanList =
                    versioned &&
                    task.serverInstanceId === list.serverInstanceId &&
                    (task.revision || 0) > list.revision!;
                const canReconcile =
                    versioned && task.serverInstanceId === list.serverInstanceId
                        ? !newerThanList
                        : before.get(task.taskId) === task;
                if (
                    !serverTaskIds.has(task.taskId) &&
                    !newerThanList &&
                    canReconcile
                ) {
                    this.removeLocalTask(task.taskId);
                }
            }
            if (versioned) {
                state.watermark = list.revision!;
                for (const [taskId, revision] of state.deleted) {
                    if (revision <= state.watermark)
                        state.deleted.delete(taskId);
                }
            }
        }

        await this.importCompletedLocalTasks();
        this.ensureEventStreams();
    }

    private static async importCompletedLocalTasks(): Promise<void> {
        for (const task of this.getTasks()) {
            if (
                task.source === "local" &&
                task.status === "completed" &&
                task.importState === "pending"
            ) {
                await this.importer.importTaskOutputs(task.taskId);
            }
        }
    }

    private static upsertTask(
        snapshot: ServerTaskSnapshot,
        serverUrl: string,
        overrides: Partial<PluginTask> = {},
        fromList = false,
    ) {
        if (!this.acceptInstance(serverUrl, snapshot)) return;
        const state = this.syncState(serverUrl);
        const existing = this.tasks.get(snapshot.taskId);
        const versioned =
            snapshot.serverInstanceId !== undefined &&
            snapshot.revision !== undefined;
        const deletedRevision = state.deleted.get(snapshot.taskId);
        if (
            versioned &&
            deletedRevision !== undefined &&
            snapshot.revision! <= deletedRevision
        )
            return;
        const sameInstance =
            existing?.serverInstanceId === snapshot.serverInstanceId;
        const stale =
            (versioned && !fromList && snapshot.revision! <= state.watermark) ||
            (versioned &&
                sameInstance &&
                existing?.revision !== undefined &&
                snapshot.revision! <= existing.revision) ||
            (existing &&
                (!versioned || sameInstance) &&
                ((snapshot.attempt || 1) < (existing.attempt || 1) ||
                    (!versioned &&
                        (snapshot.attempt || 1) === (existing.attempt || 1) &&
                        snapshot.updatedAt < existing.updatedAt)));
        if (stale) {
            // The creation response may arrive after its first SSE events.
            if (overrides.itemID && existing && !existing.itemID) {
                this.updateLocalTask(snapshot.taskId, {
                    itemID: overrides.itemID,
                    source: "local",
                    importState:
                        existing.importState === "none"
                            ? "pending"
                            : existing.importState,
                });
            }
            return;
        }
        const nextTask: PluginTask = {
            serverInstanceId: snapshot.serverInstanceId,
            revision: snapshot.revision,
            taskId: snapshot.taskId,
            fileName: snapshot.fileName,
            service: snapshot.service,
            outputModes: snapshot.outputModes,
            status: snapshot.status,
            stage: snapshot.stage,
            stageCurrent: snapshot.stageCurrent,
            stageTotal: snapshot.stageTotal,
            stageProgress: snapshot.stageProgress,
            overallProgress: snapshot.overallProgress,
            error: snapshot.error,
            errorDiagnostics: snapshot.errorDiagnostics,
            attempt: snapshot.attempt,
            resultFiles: snapshot.resultFiles,
            createdAt: snapshot.createdAt,
            updatedAt: snapshot.updatedAt,
            canCancel: snapshot.canCancel,
            cancelRequested: snapshot.cancelRequested,
            metrics: snapshot.metrics,
            canRepair: snapshot.canRepair,
            translationSummary: snapshot.translationSummary,
            qualitySummary: snapshot.qualitySummary,
            failedParagraphs: snapshot.failedParagraphs,
            importedOutputs: existing?.importedOutputs,
            serverUrl,
            source: existing?.source || "remote",
            importState: existing?.importState || "none",
            itemID: existing?.itemID,
            importError: existing?.importError,
            ...overrides,
        };
        if (existing && (snapshot.attempt || 1) > (existing.attempt || 1)) {
            nextTask.importState = nextTask.itemID ? "pending" : "none";
            nextTask.importError = undefined;
            nextTask.importedOutputs = [];
        }
        this.tasks.set(snapshot.taskId, nextTask);
        this.taskChanges.set(snapshot.taskId, ++this.changeSequence);
        this.notifyTasksChanged();
    }

    private static updateLocalTask(
        taskId: string,
        patch: Partial<PluginTask>,
    ): void {
        const current = this.tasks.get(taskId);
        if (!current) {
            return;
        }
        this.tasks.set(taskId, {
            ...current,
            ...patch,
        });
        this.taskChanges.set(taskId, ++this.changeSequence);
        this.notifyTasksChanged();
    }

    private static removeLocalTask(taskId: string): void {
        this.tasks.delete(taskId);
        this.taskChanges.set(taskId, ++this.changeSequence);
        this.notifyTasksChanged();
    }

    private static syncState(serverUrl: string): ServerSyncState {
        let state = this.serverSync.get(serverUrl);
        if (!state) {
            state = {
                generation: 0,
                watermark: -1,
                deleted: new Map(),
                retiredInstances: new Set(),
            };
            this.serverSync.set(serverUrl, state);
        }
        return state;
    }

    private static canApplyResponse(
        serverUrl: string,
        generation: number,
        metadata: ServerSyncMetadata,
    ): boolean {
        const state = this.syncState(serverUrl);
        return (
            generation === state.generation ||
            (metadata.serverInstanceId
                ? metadata.serverInstanceId === state.instanceId
                : !state.instanceId)
        );
    }

    private static acceptInstance(
        serverUrl: string,
        metadata: ServerSyncMetadata,
    ): boolean {
        if (!metadata.serverInstanceId) return true;
        const state = this.syncState(serverUrl);
        if (state.retiredInstances.has(metadata.serverInstanceId)) return false;
        if (state.instanceId !== metadata.serverInstanceId) {
            if (state.instanceId) {
                state.retiredInstances.add(state.instanceId);
            }
            state.generation += 1;
            state.instanceId = metadata.serverInstanceId;
            state.watermark = -1;
            state.deleted.clear();
        }
        return true;
    }

    private static ensureEventStreams() {
        this.loadLocalTasks();
        const serverUrls = new Set<string>();
        const currentServerUrl = getPref("new_serverip")?.toString() || "";
        const dialogOpen = Boolean(
            this.dialogWindow && !this.dialogWindow.closed,
        );
        // Keep the configured server subscribed for the plugin lifetime. A
        // task dialog can survive an extension reload while the static window
        // reference is reset, so using dialogOpen as a prerequisite can leave
        // a visible dialog with an empty stream set.
        if (currentServerUrl) {
            serverUrls.add(currentServerUrl);
        }
        for (const task of this.tasks.values()) {
            const shouldTrackTaskServer =
                dialogOpen ||
                ACTIVE_STATUSES.includes(task.status) ||
                (task.status === "completed" &&
                    (task.importState === "pending" ||
                        task.importState === "importing"));
            if (task.serverUrl && shouldTrackTaskServer) {
                serverUrls.add(task.serverUrl);
            }
        }

        this.eventStream.sync(serverUrls);
    }

    private static handleServerTaskEvent(
        serverUrl: string,
        event: ServerTaskEvent,
    ) {
        if (!this.acceptInstance(serverUrl, event)) return;
        if (event.type === "resync") {
            void this.refreshTasks();
            return;
        }
        if (
            (event.type === "snapshot" || event.type === "task") &&
            event.task
        ) {
            this.upsertTask(event.task, serverUrl);
            void this.importCompletedLocalTasks();
            return;
        }

        if (event.type === "deleted" && event.taskId) {
            const state = this.syncState(serverUrl);
            if (event.revision !== undefined) {
                const current = this.tasks.get(event.taskId);
                const lastRevision =
                    current?.serverInstanceId === event.serverInstanceId
                        ? current?.revision
                        : undefined;
                if (
                    event.revision <= state.watermark ||
                    event.revision <= (state.deleted.get(event.taskId) ?? -1) ||
                    event.revision < (lastRevision ?? -1)
                )
                    return;
                state.deleted.set(event.taskId, event.revision);
            }
            this.removeLocalTask(event.taskId);
            this.ensureEventStreams();
        }
    }

    private static notifyTasksChanged(): void {
        this.saveLocalTasks();
        this.notifyTaskListeners();
    }

    private static notifyTaskListeners(): void {
        for (const listener of this.taskListeners) {
            try {
                listener();
            } catch (error) {
                ztoolkit.log(error);
            }
        }
    }

    private static notifyTranslationCompleted(taskId: string): void {
        if (
            !PDF2zhHelperFactory.isTrue(getPref("notifyOnTranslationComplete"))
        ) {
            return;
        }

        const task = this.tasks.get(taskId);
        if (
            !task ||
            task.source !== "local" ||
            task.importState !== "imported"
        ) {
            return;
        }

        try {
            const alertsService = Cc[
                "@mozilla.org/alerts-service;1"
            ].getService(Ci.nsIAlertsService);
            alertsService.showAlertNotification(
                `chrome://${config.addonRef}/content/icons/favicon.svg`,
                getString("translation-complete-title"),
                getString("translation-complete-body", {
                    args: { fileName: task.fileName },
                }),
                false,
                "",
                undefined,
                `${config.addonRef}-translation-${task.taskId}`,
            );
        } catch (error) {
            ztoolkit.log("无法发送翻译完成系统通知:", error);
        }
    }
}
