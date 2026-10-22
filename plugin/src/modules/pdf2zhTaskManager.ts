import { exportDiagnostics, recordDiagnostic } from "./diagnostics";
import { config } from "../../package.json";
import { getString } from "../utils/locale";
import { getPref } from "../utils/prefs";
import {
    errorNotice,
    NoticeError,
    renderNotice,
    type UserNotice,
} from "./userNotice";
import { runNoticeAction } from "./pluginNotices";
import { resolveSelectedGlossaryPacks } from "./glossaryPacks";
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
import {
    ZoteroTaskImporter,
    canDownloadTaskResult,
} from "./zoteroTaskImporter";

type TaskDialogArgs = {
    message: typeof getString;
    locale: string;
    errorNotice: (error: unknown) => UserNotice;
    renderNotice: typeof renderNotice;
    noticeAction: typeof runNoticeAction;
    getSubmission: () => SubmissionFeedback | undefined;
    getServerNotice: () => UserNotice | undefined;
    _initPromise: any;
    getTasks: () => PluginTask[];
    getHistoryPage: () => {
        page: number;
        hasMore: boolean;
        total: number;
        loading: boolean;
        supported: boolean;
    };
    changeHistoryPage: (direction: number) => Promise<void>;
    hasActiveTasks: () => boolean;
    hasFailedTasks: () => boolean;
    onTasksChanged: (listener: () => void) => () => void;
    refreshTasks: () => Promise<void>;
    cancelTask: (taskId: string) => Promise<void>;
    exportDiagnostics: (taskId: string) => Promise<void>;
    retryTask: (taskId: string) => Promise<void>;
    repairTask: (taskId: string) => Promise<void>;
    reloginChatGPT: (taskId: string) => void;
    retryFailedTasks: () => Promise<void>;
    resumeTasks: () => Promise<void>;
    resumeTask: (taskId: string) => Promise<void>;
    retryMemorySync: (taskId: string) => Promise<void>;
    deleteTask: (taskId: string) => Promise<void>;
    clearFailedTasks: () => Promise<void>;
    getEventStreamState: () => {
        connected: number;
        total: number;
        hasErrors: boolean;
    };
};

type SubmissionFeedback = {
    notice: UserNotice;
    errors: { fileName: string; text: string }[];
    warnings: string[];
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
    private static stopped = false;

    static async start(): Promise<void> {
        this.stopped = false;
        this.loadLocalTasks();
        this.ensureEventStreams();
        this.scheduleFallbackRefresh();
        await this.refreshTasks();
    }

    static stop(): void {
        this.stopped = true;
        clearTimeout(this.completionTimer);
        this.completionTimer = undefined;
        this.completions.clear();
        clearTimeout(this.refreshTimer);
        this.refreshTimer = undefined;
        this.closeWindow();
        this.eventStream.sync(new Set());
    }

    private static tasks = new Map<string, PluginTask>();
    private static localTasksLoaded = false;
    private static savedBindings = "";
    private static bindingRaw: unknown;
    private static bindingBlocked = false;
    private static bindingNotice: UserNotice | undefined;
    private static changeSequence = 0;
    private static taskChanges = new Map<string, number>();
    private static serverSync = new Map<string, ServerSyncState>();
    private static historyPage = 0;
    private static paging = false;
    private static pageError: unknown;
    private static pages = new Map<
        string,
        {
            cursors: (string | undefined)[];
            visible: Set<string>;
            next?: string;
            hasMore: boolean;
            total: number;
        }
    >();

    static getVisibleTasks(): PluginTask[] {
        return this.getTasks().filter((task) => {
            const page = this.pages.get(task.serverUrl);
            return (
                !page ||
                page.visible.has(task.taskId) ||
                ACTIVE_STATUSES.includes(task.status) ||
                task.canResume ||
                (task.source === "local" &&
                    canDownloadTaskResult(task) &&
                    ["pending", "importing", "failed"].includes(
                        task.importState,
                    ))
            );
        });
    }

    static getHistoryPage() {
        return {
            page: this.historyPage + 1,
            hasMore: [...this.pages.values()].some((page) => page.hasMore),
            total: [...this.pages.values()].reduce(
                (sum, page) => sum + page.total,
                0,
            ),
            loading: this.paging,
            supported: this.pages.size > 0,
        };
    }

    static async changeHistoryPage(direction: number): Promise<void> {
        if (
            this.paging ||
            (direction > 0
                ? !this.getHistoryPage().hasMore
                : this.historyPage === 0)
        )
            return;
        this.paging = true;
        this.pageError = undefined;
        const previousIndex = this.historyPage;
        const previousPages = new Map(
            [...this.pages].map(([url, page]) => [
                url,
                { ...page, cursors: [...page.cursors] },
            ]),
        );
        if (direction > 0)
            for (const page of this.pages.values())
                page.cursors[this.historyPage + 1] = page.next;
        this.historyPage += direction > 0 ? 1 : -1;
        this.notifyTaskListeners();
        try {
            await this.refreshTasks();
            if (this.pageError) {
                const error = this.pageError;
                this.historyPage = previousIndex;
                this.pages = previousPages;
                await this.refreshTasks();
                throw error;
            }
        } finally {
            this.paging = false;
            this.notifyTaskListeners();
        }
    }

    private static loadLocalTasks(): void {
        if (this.localTasksLoaded) return;
        this.localTasksLoaded = true;
        try {
            const raw = Zotero.Prefs.get(
                `${config.prefsPrefix}.taskBindings`,
                true,
            );
            const tasks = this.parseBindings(raw);
            if (
                raw === undefined &&
                Zotero.Prefs.get(
                    `${config.prefsPrefix}.taskBindingsBackup`,
                    true,
                ) !== undefined
            )
                throw new Error("Missing primary task bindings");
            this.bindingRaw = raw;
            for (const task of tasks) {
                if (task.importState === "importing")
                    task.importState = "pending";
                this.tasks.set(task.taskId, this.normalizeImportState(task));
            }
        } catch (error) {
            this.blockBindings();
            ztoolkit.log("读取任务附件关联失败", error);
        }
    }

    private static parseBindings(raw: unknown): PluginTask[] {
        if (raw === undefined) return [];
        if (typeof raw !== "string") throw new Error("Invalid task bindings");
        const tasks = JSON.parse(raw);
        const ids = new Set<string>();
        if (
            !Array.isArray(tasks) ||
            tasks.some((task) => {
                if (
                    !task ||
                    typeof task !== "object" ||
                    typeof task.taskId !== "string" ||
                    !task.taskId ||
                    ids.has(task.taskId) ||
                    !Number.isSafeInteger(task.itemID) ||
                    task.itemID <= 0 ||
                    typeof task.serverUrl !== "string" ||
                    !task.serverUrl ||
                    task.source !== "local" ||
                    ![
                        "queued",
                        "paused",
                        "running",
                        "cancelling",
                        "cancelled",
                        "failed",
                        "completed",
                        "incomplete",
                    ].includes(task.status) ||
                    ![
                        "pending",
                        "importing",
                        "imported",
                        "failed",
                        "none",
                    ].includes(task.importState) ||
                    !Array.isArray(task.outputModes) ||
                    task.outputModes.some(
                        (mode: unknown) =>
                            !["mono", "dual"].includes(mode as string),
                    ) ||
                    (task.importedOutputs !== undefined &&
                        (!Array.isArray(task.importedOutputs) ||
                            task.importedOutputs.some(
                                (value: unknown) => typeof value !== "string",
                            )))
                )
                    return true;
                ids.add(task.taskId);
                return false;
            })
        )
            throw new Error("Invalid task binding records");
        return tasks;
    }

    private static blockBindings(): void {
        this.bindingBlocked = true;
        this.bindingNotice = errorNotice(
            new NoticeError("task_bindings_unavailable"),
            getString,
        );
    }

    private static normalizeImportState(task: PluginTask): PluginTask {
        if (
            task.importState !== "imported" &&
            ["cancelled", "failed"].includes(task.status)
        )
            return { ...task, importState: "none", importError: undefined };
        return task;
    }

    private static requireBindings(): void {
        this.loadLocalTasks();
        this.saveLocalTasks();
        if (this.bindingNotice)
            throw new NoticeError(
                this.bindingBlocked
                    ? "task_bindings_unavailable"
                    : "task_bindings_write_failed",
            );
    }

    private static saveLocalTasks(): void {
        if (!this.localTasksLoaded || this.bindingBlocked) return;
        const prefKey = `${config.prefsPrefix}.taskBindings`;
        try {
            if (Zotero.Prefs.get(prefKey, true) !== this.bindingRaw) {
                this.blockBindings();
                return;
            }
        } catch {
            this.blockBindings();
            return;
        }
        const tasks = [...this.tasks.values()].filter(
            (task) => task.source === "local" && task.itemID,
        );
        const signature = JSON.stringify(
            tasks.map((task) => [
                task.taskId,
                task.itemID,
                task.sourceLang,
                task.targetLang,
                task.model,
                task.requestedConfiguration,
                task.effectiveConfiguration,
                task.serverUrl,
                task.attempt,
                task.importState,
                task.importedOutputs,
                task.qualitySummary,
                task.canDownloadResult,
                task.status,
                task.recoveryState,
                task.timing?.finishedAt,
                task.resultFiles,
                task.translationSummary,
                task.translationMemorySync,
            ]),
        );
        if (signature === this.savedBindings) return;
        try {
            // Retain the last known-good snapshot. Never replace it with an
            // unreadable primary or automatically replay stale associations.
            if (typeof this.bindingRaw === "string")
                Zotero.Prefs.set(
                    `${config.prefsPrefix}.taskBindingsBackup`,
                    this.bindingRaw,
                    true,
                );
            const encoded = JSON.stringify(tasks);
            Zotero.Prefs.set(prefKey, encoded, true);
            this.bindingRaw = encoded;
            this.savedBindings = signature;
            this.bindingNotice = undefined;
        } catch (error) {
            this.bindingNotice = errorNotice(
                new NoticeError("task_bindings_write_failed"),
                getString,
            );
            ztoolkit.log("保存任务附件关联失败", error);
        }
    }
    private static pollPromise: Promise<void> | null = null;
    private static refreshTimer: ReturnType<typeof setTimeout> | undefined;

    private static scheduleFallbackRefresh(): void {
        if (this.stopped || this.refreshTimer !== undefined) return;
        this.refreshTimer = setTimeout(() => {
            this.refreshTimer = undefined;
            if (this.stopped) return;
            try {
                this.ensureEventStreams();
                // Do not queue another refresh behind a slow in-flight request.
                if (!this.pollPromise) {
                    void this.refreshTasks().catch((error) =>
                        ztoolkit.log(error),
                    );
                }
            } finally {
                this.scheduleFallbackRefresh();
            }
        }, 5000);
    }

    private static refreshAgain = false;
    private static taskListeners = new Set<() => void>();
    private static dialogWindow: Window | undefined;
    private static eventStream = new TaskEventStream({
        onTaskEvent: (serverUrl, event) =>
            PDF2zhTaskManager.handleServerTaskEvent(serverUrl, event),
        onStateChange: (serverUrl, state) => {
            recordDiagnostic("stream_" + state);
            PDF2zhTaskManager.notifyTaskListeners();
            if (state === "open") {
                PDF2zhTaskManager.syncState(serverUrl).generation += 1;
                void PDF2zhTaskManager.refreshTasks();
            }
        },
    });
    private static importer = new ZoteroTaskImporter({
        getTask: (taskId) =>
            PDF2zhTaskManager.stopped || PDF2zhTaskManager.bindingNotice
                ? undefined
                : PDF2zhTaskManager.tasks.get(taskId),
        updateTask: (taskId, patch) =>
            PDF2zhTaskManager.updateLocalTask(taskId, patch),
        onTaskImported: (taskId) =>
            PDF2zhTaskManager.notifyTranslationCompleted(taskId),
    });

    private static submitting = false;
    private static submission: SubmissionFeedback | undefined;
    private static taskStoreErrors = new Map<string, UserNotice>();
    private static completions = new Map<string, PluginTask>();
    private static notifiedImports = new Set<string>();
    private static completionTimer: ReturnType<typeof setTimeout> | undefined;

    static async processWorker() {
        if (this.submitting) {
            this.openWindow();
            return;
        }
        this.submitting = true;
        this.loadLocalTasks();
        const feedback: SubmissionFeedback = {
            notice: { id: "submission", level: "info", text: "" },
            errors: [],
            warnings: [],
        };
        this.submission = feedback;
        let submitted = 0;
        try {
            const selectedItems = ztoolkit
                .getGlobal("ZoteroPane")
                .getSelectedItems();
            if (!selectedItems.length) throw new NoticeError("no_selection");
            const serverConfig = PDF2zhHelperFactory.getServerConfig();
            if (!serverConfig.apiConfig) throw new NoticeError("no_profile");
            if (!serverConfig.outputModes.length)
                throw new NoticeError("no_output");
            serverConfig.glossaryPacks = await resolveSelectedGlossaryPacks(
                serverConfig.serverUrl,
                serverConfig.sourceLang,
                serverConfig.targetLang,
            );
            this.openWindow();
            for (let index = 0; index < selectedItems.length; index++) {
                if (this.stopped) break;
                feedback.notice = {
                    id: "submission",
                    level: "info",
                    text: getString("notice-submitting", {
                        args: {
                            current: index + 1,
                            total: selectedItems.length,
                        },
                    }),
                };
                this.notifyTaskListeners();
                const item = selectedItems[index];
                try {
                    await this.submitTask(item, serverConfig, () => {
                        const warning = getString("notice-api-compatibility");
                        if (!feedback.warnings.includes(warning))
                            feedback.warnings.push(warning);
                    });
                    submitted++;
                } catch (error) {
                    feedback.errors.push({
                        fileName: String(item.getField("title") || item.id),
                        text: errorNotice(error, getString).text,
                    });
                }
            }
            feedback.notice = {
                id: "submission",
                level:
                    feedback.errors.length || feedback.warnings.length
                        ? "warning"
                        : "success",
                text: getString("notice-submitted", {
                    args: {
                        success: submitted,
                        failed: feedback.errors.length,
                    },
                }),
            };
            // A refresh failure cannot change the already accepted submission count.
            void this.refreshTasks().catch((error) => ztoolkit.log(error));
        } catch (error) {
            feedback.notice = errorNotice(error, getString);
            this.openWindow();
        } finally {
            this.submitting = false;
            this.notifyTaskListeners();
        }
    }

    static openWindow() {
        this.loadLocalTasks();
        if (this.dialogWindow && !this.dialogWindow.closed) {
            this.dialogWindow.focus();
            return;
        }

        const windowArgs: TaskDialogArgs = {
            message: getString,
            locale: Zotero.locale,
            errorNotice: (error) => errorNotice(error, getString),
            renderNotice,
            noticeAction: runNoticeAction,
            getSubmission: () => this.submission,
            getServerNotice: () =>
                this.bindingNotice ||
                this.taskStoreErrors.values().next().value,
            _initPromise: Zotero.Promise.defer(),
            getTasks: () => this.getVisibleTasks(),
            getHistoryPage: () => this.getHistoryPage(),
            changeHistoryPage: (direction) => this.changeHistoryPage(direction),
            hasActiveTasks: () => this.hasActiveTasks(),
            hasFailedTasks: () =>
                this.getTasks().some((task) => task.status === "failed"),
            onTasksChanged: (listener: () => void) =>
                this.onTasksChanged(listener),
            refreshTasks: () => this.refreshTasks(),
            cancelTask: (taskId: string) => this.cancelTask(taskId),
            exportDiagnostics: async (taskId: string) => {
                const task = this.tasks.get(taskId);
                if (task) await exportDiagnostics(task.serverUrl, { ...task });
            },
            retryTask: (taskId: string) => this.retryTask(taskId),
            repairTask: (taskId: string) => this.repairTask(taskId),
            reloginChatGPT: (taskId: string) => {
                const task = this.tasks.get(taskId);
                const current = String(getPref("new_serverip") || "").replace(
                    /\/+$/,
                    "",
                );
                if (!task || task.serverUrl.replace(/\/+$/, "") !== current)
                    throw new Error(
                        "请先在设置中连接此任务使用的本机服务，再重新登录。",
                    );
                addon.hooks.onDialogEvents("reloginChatGPT");
            },
            retryFailedTasks: () => this.retryFailedTasks(),
            resumeTasks: () => this.resumeTasks(),
            resumeTask: (taskId: string) => this.resumeTask(taskId),
            retryMemorySync: (taskId: string) => this.retryMemorySync(taskId),
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
                (canDownloadTaskResult(task) &&
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
        if (this.stopped) return;
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
        this.requireBindings();
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
        this.requireBindings();
        const task = this.tasks.get(taskId);
        if (!task) {
            throw new Error("任务不存在");
        }
        if (task.canResume) return this.resumeTask(taskId);

        if (canDownloadTaskResult(task) && task.importState === "failed") {
            this.updateLocalTask(taskId, {
                importState: "pending",
                importError: undefined,
            });
            await this.importer.importTaskOutputs(taskId);
            return;
        }
        if (task.status === "incomplete") return this.repairTask(taskId);

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
        const failedTasks = this.getVisibleTasks().filter(
            (task) =>
                task.status === "failed" ||
                task.status === "incomplete" ||
                (canDownloadTaskResult(task) && task.importState === "failed"),
        );
        for (const task of failedTasks) {
            await this.retryTask(task.taskId);
        }
    }

    static async resumeTask(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task?.canResume) return;
        const generation = this.syncState(task.serverUrl).generation;
        const snapshot = await ServerTaskClient.resumeTask(
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

    static async retryMemorySync(taskId: string): Promise<void> {
        const task = this.tasks.get(taskId);
        if (!task) return;
        const generation = this.syncState(task.serverUrl).generation;
        const snapshot = await ServerTaskClient.retryMemorySync(
            task.serverUrl,
            taskId,
        );
        if (
            snapshot &&
            this.canApplyResponse(task.serverUrl, generation, snapshot)
        )
            this.upsertTask(snapshot, task.serverUrl);
    }

    static async resumeTasks(): Promise<void> {
        const tasks = this.getTasks()
            .filter((task) => task.canResume)
            .sort((a, b) =>
                (a.timing?.queuedAt || a.createdAt).localeCompare(
                    b.timing?.queuedAt || b.createdAt,
                ),
            );
        for (const task of tasks) await this.resumeTask(task.taskId);
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

    private static async submitTask(
        item: Zotero.Item,
        serverConfig: ServerConfig,
        onWarning?: (message: string) => void,
    ) {
        this.requireBindings();
        const fileData = await PDF2zhHelperFactory.prepareFileData(item, true);
        const requestBody: Record<string, unknown> =
            PDF2zhHelperFactory.buildTaskRequestBody(fileData, serverConfig);
        const model = serverConfig.apiConfig?.model || "";

        // Store only digests and random IDs, never PDFs or API credentials.
        const prefKey = `${config.prefsPrefix}.submissionBindings`;
        const crypto = Zotero.getMainWindow().crypto;
        const stored = Zotero.Prefs.get(prefKey, true);
        let bindings: {
            version?: number;
            clientId: string;
            pending: Record<string, string>;
            legacyPending?: Record<string, string>;
        } =
            typeof stored === "string"
                ? JSON.parse(stored)
                : { version: 2, clientId: crypto.randomUUID(), pending: {} };
        if (bindings.version !== 2) {
            bindings = {
                ...bindings,
                version: 2,
                legacyPending: bindings.pending,
                pending: {},
            };
            Zotero.Prefs.set(prefKey, JSON.stringify(bindings), true);
        }
        // Establish the installation identity before asynchronous hashing can interleave submissions.
        if (typeof stored !== "string")
            Zotero.Prefs.set(prefKey, JSON.stringify(bindings), true);
        requestBody.dedupScope = `${bindings.clientId}:${item.libraryID}:${item.key || item.id}`;
        let legacyFingerprint: string | undefined;
        if (fileData.file && Object.keys(bindings.legacyPending || {}).length) {
            // A pre-binary uncertain submission must retain its request ID after
            // upgrading. Only this migration path materializes legacy base64.
            const legacyBody = {
                ...requestBody,
                fileContent: await PDF2zhHelperFactory.blobToBase64(
                    fileData.file,
                ),
            };
            const legacyHash = await crypto.subtle.digest(
                "SHA-256",
                new TextEncoder().encode(
                    JSON.stringify([serverConfig.serverUrl, legacyBody]),
                ),
            );
            legacyFingerprint = Array.from(new Uint8Array(legacyHash), (byte) =>
                byte.toString(16).padStart(2, "0"),
            ).join("");
        }
        const hash = await crypto.subtle.digest(
            "SHA-256",
            new TextEncoder().encode(
                JSON.stringify([
                    serverConfig.serverUrl,
                    requestBody,
                    fileData.fingerprint,
                ]),
            ),
        );
        const fingerprint = Array.from(new Uint8Array(hash), (byte) =>
            byte.toString(16).padStart(2, "0"),
        ).join("");
        bindings = JSON.parse(Zotero.Prefs.get(prefKey, true) as string);
        requestBody.requestId =
            bindings.pending[fingerprint] ||
            (legacyFingerprint &&
                bindings.legacyPending?.[legacyFingerprint]) ||
            crypto.randomUUID();
        bindings.pending[fingerprint] = requestBody.requestId as string;
        if (legacyFingerprint && bindings.legacyPending)
            delete bindings.legacyPending[legacyFingerprint];
        Zotero.Prefs.set(prefKey, JSON.stringify(bindings), true);
        const releaseSubmission = () => {
            bindings = JSON.parse(Zotero.Prefs.get(prefKey, true) as string);
            if (bindings.pending[fingerprint] === requestBody.requestId)
                delete bindings.pending[fingerprint];
            Zotero.Prefs.set(prefKey, JSON.stringify(bindings), true);
        };

        const generation = this.syncState(serverConfig.serverUrl).generation;
        let task: ServerTaskSnapshot;
        try {
            task = await ServerTaskClient.createTask(
                serverConfig.serverUrl,
                requestBody,
                onWarning,
                fileData.file,
            );
        } catch (error) {
            // A definitive conflict (including a deleted task) must allow a fresh manual submission.
            if (
                error instanceof Error &&
                "submissionConflict" in error &&
                error.submissionConflict
            )
                releaseSubmission();
            throw error;
        }
        if (this.canApplyResponse(serverConfig.serverUrl, generation, task)) {
            this.upsertTask(task, serverConfig.serverUrl, {
                itemID: item.id,
                sourceLang: serverConfig.sourceLang,
                targetLang: serverConfig.targetLang,
                model,
                source: "local",
                importState:
                    this.tasks.get(task.taskId)?.importState === "none"
                        ? "pending"
                        : this.tasks.get(task.taskId)?.importState || "pending",
            });
        } else {
            // A restarted server can already have restored this submission.
            // Its old response can supply the binding, never its old state.
            const current = this.tasks.get(task.taskId);
            if (
                current?.serverUrl === serverConfig.serverUrl &&
                !current.itemID
            ) {
                this.updateLocalTask(task.taskId, {
                    itemID: item.id,
                    sourceLang: serverConfig.sourceLang,
                    targetLang: serverConfig.targetLang,
                    model,
                    source: "local",
                    importState:
                        current.importState === "none"
                            ? "pending"
                            : current.importState,
                });
            }
        }
        // Keep uncertain submissions across plugin restarts until the attachment binding is saved.
        this.requireBindings();
        const saved = Zotero.Prefs.get(
            `${config.prefsPrefix}.taskBindings`,
            true,
        );
        if (
            typeof saved === "string" &&
            JSON.parse(saved).some(
                (entry: PluginTask) =>
                    entry.taskId === task.taskId && entry.itemID === item.id,
            )
        ) {
            releaseSubmission();
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
                list = await ServerTaskClient.listTasks(serverUrl, {
                    cursor: this.pages.get(serverUrl)?.cursors[
                        this.historyPage
                    ],
                    tracked: [...this.tasks.values()]
                        .filter(
                            (task) =>
                                task.serverUrl === serverUrl &&
                                (ACTIVE_STATUSES.includes(task.status) ||
                                    task.canResume ||
                                    this.pages
                                        .get(serverUrl)
                                        ?.visible.has(task.taskId) ||
                                    (task.source === "local" &&
                                        [
                                            "pending",
                                            "importing",
                                            "failed",
                                        ].includes(task.importState) &&
                                        (!state.instanceId ||
                                            task.serverInstanceId !==
                                                state.instanceId ||
                                            canDownloadTaskResult(task)))),
                        )
                        .map((task) => task.taskId),
                });
            } catch (error) {
                if (this.stopped || generation !== state.generation) continue;
                if (
                    (error as { code?: string })?.code ===
                        "invalid_task_page" &&
                    this.historyPage > 0
                ) {
                    this.historyPage = 0;
                    this.pages.clear();
                    this.refreshAgain = true;
                } else if (this.paging) this.pageError = error;
                if (
                    (error as { code?: string })?.code ===
                        "task_store_unavailable" &&
                    !this.taskStoreErrors.has(serverUrl)
                ) {
                    this.taskStoreErrors.set(
                        serverUrl,
                        errorNotice(error, getString),
                    );
                    this.notifyTaskListeners();
                }
                continue;
            }
            if (this.stopped) return;
            if (generation !== state.generation) {
                this.refreshAgain = true;
                continue;
            }
            if (!this.acceptInstance(serverUrl, list)) continue;
            if (this.taskStoreErrors.delete(serverUrl))
                this.notifyTaskListeners();
            const versioned =
                list.serverInstanceId !== undefined &&
                list.revision !== undefined;
            if (versioned && list.revision! < state.watermark) continue;
            const snapshots = list.tasks;
            if (
                list.scope === "dashboard" &&
                list.pagination &&
                list.visibleTaskIds
            ) {
                const oldPage = this.pages.get(serverUrl);
                this.pages.set(serverUrl, {
                    cursors: oldPage?.cursors || [undefined],
                    visible: new Set(list.visibleTaskIds),
                    next: list.pagination.nextCursor || undefined,
                    hasMore: list.pagination.hasMore,
                    total: list.pagination.totalHistory,
                });
            } else this.pages.delete(serverUrl);
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
                    (list.scope !== "dashboard" ||
                        list.missingTaskIds?.includes(task.taskId)) &&
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
            // Remote history can be fetched again. Keep local attachment bindings
            // even when their history page is not loaded.
            const page = this.pages.get(serverUrl);
            if (page)
                for (const task of this.tasks.values()) {
                    if (
                        task.serverUrl === serverUrl &&
                        task.source !== "local" &&
                        !page.visible.has(task.taskId) &&
                        !ACTIVE_STATUSES.includes(task.status) &&
                        !task.canResume
                    )
                        this.tasks.delete(task.taskId);
                }
            this.notifyTaskListeners();
        }

        await this.importCompletedLocalTasks();
        this.ensureEventStreams();
    }

    private static async importCompletedLocalTasks(): Promise<void> {
        if (this.stopped || this.bindingNotice) return;
        for (const task of this.getTasks()) {
            if (
                task.source === "local" &&
                canDownloadTaskResult(task) &&
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
            // Timers advance without a state revision while a task waits in the queue.
            if (
                fromList &&
                sameInstance &&
                existing &&
                snapshot.revision === existing.revision &&
                snapshot.timing &&
                snapshot.timing.totalSeconds >=
                    (existing.timing?.totalSeconds || 0)
            ) {
                this.updateLocalTask(snapshot.taskId, {
                    timing: snapshot.timing,
                });
            }
            // The creation response may arrive after its first SSE events.
            if (overrides.itemID && existing && !existing.itemID) {
                this.updateLocalTask(snapshot.taskId, {
                    itemID: overrides.itemID,
                    sourceLang: overrides.sourceLang,
                    targetLang: overrides.targetLang,
                    model: overrides.model,
                    source: "local",
                    importState:
                        existing.importState === "none"
                            ? "pending"
                            : existing.importState,
                });
            }
            return;
        }
        if (
            !existing ||
            existing.status !== snapshot.status ||
            existing.stageCurrent !== snapshot.stageCurrent
        ) {
            recordDiagnostic("task_state", { ...snapshot });
        }
        const nextTask: PluginTask = {
            serverInstanceId: snapshot.serverInstanceId,
            revision: snapshot.revision,
            taskId: snapshot.taskId,
            fileName: snapshot.fileName,
            service: snapshot.service,
            requestedConfiguration: snapshot.requestedConfiguration,
            effectiveConfiguration: snapshot.effectiveConfiguration,
            outputModes: snapshot.outputModes,
            status: snapshot.status,
            stage: snapshot.stage,
            currentPage: snapshot.currentPage,
            selectedPage: snapshot.selectedPage,
            completedPages: snapshot.completedPages,
            totalPages: snapshot.totalPages,
            operation: snapshot.operation,
            lastProgressAt: snapshot.lastProgressAt,
            heartbeatAt: snapshot.heartbeatAt,
            idleSeconds: snapshot.idleSeconds,
            stalled: snapshot.stalled,
            cancelPhase: snapshot.cancelPhase,
            cancelReason: snapshot.cancelReason,
            queueBlocked: snapshot.queueBlocked,
            fontParseCount: snapshot.fontParseCount,
            fontCacheHits: snapshot.fontCacheHits,
            stageElapsedSeconds: snapshot.stageElapsedSeconds,
            lastRequestAt: snapshot.lastRequestAt,
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
            boundedCancellation: snapshot.boundedCancellation,
            recoveryState: snapshot.recoveryState,
            canResume: snapshot.canResume,
            timing: snapshot.timing,
            metrics: snapshot.metrics,
            canRepair: snapshot.canRepair,
            canDownloadResult: snapshot.canDownloadResult,
            translationSummary: snapshot.translationSummary,
            translationMemorySync: snapshot.translationMemorySync,
            canRetryMemorySync: snapshot.canRetryMemorySync,
            qualitySummary: snapshot.qualitySummary,
            failedParagraphs: snapshot.failedParagraphs,
            importedOutputs: existing?.importedOutputs,
            serverUrl,
            source: existing?.source || "remote",
            importState: existing?.importState || "none",
            itemID: existing?.itemID,
            sourceLang: existing?.sourceLang,
            targetLang: existing?.targetLang,
            model: existing?.model,
            importError: existing?.importError,
            ...overrides,
        };
        if (existing && (snapshot.attempt || 1) > (existing.attempt || 1)) {
            nextTask.importState = nextTask.itemID ? "pending" : "none";
            nextTask.importError = undefined;
            nextTask.importedOutputs = [];
        }
        this.tasks.set(snapshot.taskId, this.normalizeImportState(nextTask));
        this.taskChanges.set(snapshot.taskId, ++this.changeSequence);
        this.notifyTasksChanged();
    }

    private static updateLocalTask(
        taskId: string,
        patch: Partial<PluginTask>,
    ): void {
        if (patch.importState)
            recordDiagnostic("import_state", {
                taskId,
                importState: patch.importState,
            });
        const current = this.tasks.get(taskId);
        if (!current) {
            return;
        }
        this.tasks.set(
            taskId,
            this.normalizeImportState({
                ...current,
                ...patch,
            }),
        );
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
        if (this.stopped) return;
        this.loadLocalTasks();
        const serverUrls = new Set<string>();
        const activeServerUrls = new Set<string>();
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
            if (ACTIVE_STATUSES.includes(task.status) && task.serverUrl) {
                activeServerUrls.add(task.serverUrl);
            }
            const shouldTrackTaskServer =
                dialogOpen ||
                ACTIVE_STATUSES.includes(task.status) ||
                (canDownloadTaskResult(task) &&
                    (task.importState === "pending" ||
                        task.importState === "importing"));
            if (task.serverUrl && shouldTrackTaskServer) {
                serverUrls.add(task.serverUrl);
            }
        }

        this.eventStream.sync(serverUrls, activeServerUrls);
    }

    private static handleServerTaskEvent(
        serverUrl: string,
        event: ServerTaskEvent,
    ) {
        if (this.stopped || !this.acceptInstance(serverUrl, event)) return;
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
        const identity = JSON.stringify([
            task.taskId,
            task.importedOutputs || [],
            task.status,
        ]);
        if (this.notifiedImports.has(identity)) return;
        this.notifiedImports.add(identity);
        this.completions.set(taskId, task);
        if (!this.completionTimer)
            this.completionTimer = setTimeout(
                () => this.flushCompletions(),
                2000,
            );
    }

    private static flushCompletions() {
        this.completionTimer = undefined;
        const tasks = [...this.completions.values()];
        this.completions.clear();
        if (
            !tasks.length ||
            this.stopped ||
            !PDF2zhHelperFactory.isTrue(getPref("notifyOnTranslationComplete"))
        )
            return;
        const task = tasks[0];
        const incomplete = tasks.filter(
            (t) => t.status === "incomplete",
        ).length;
        try {
            const alertsService = Cc[
                "@mozilla.org/alerts-service;1"
            ].getService(Ci.nsIAlertsService);
            alertsService.showAlertNotification(
                `chrome://${config.addonRef}/content/icons/favicon.svg`,
                tasks.length > 1
                    ? getString("notice-imported-title")
                    : task.status === "incomplete"
                      ? getString("notice-incomplete-title")
                      : getString("translation-complete-title"),
                tasks.length > 1
                    ? getString("notice-imported-batch", {
                          args: { count: tasks.length, incomplete },
                      })
                    : task.status === "incomplete"
                      ? getString("notice-incomplete-body", {
                            args: {
                                fileName: task.fileName,
                                count:
                                    (task.translationSummary?.failed || 0) +
                                    (task.translationSummary?.pending || 0),
                            },
                        })
                      : getString("translation-complete-body", {
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
