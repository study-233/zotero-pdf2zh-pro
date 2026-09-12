import { PDF2zhHelperFactory, TaskOutputResponse } from "./pdf2zhHelper";
import { PluginTask } from "./pdf2zhTypes";
import { ServerTaskClient } from "./serverTaskClient";

type TaskImporterCallbacks = {
    getTask: (taskId: string) => PluginTask | undefined;
    updateTask: (taskId: string, patch: Partial<PluginTask>) => void;
    onTaskImported: (taskId: string) => void;
};

export class ZoteroTaskImporter {
    constructor(private callbacks: TaskImporterCallbacks) {}

    async importTaskOutputs(taskId: string): Promise<void> {
        const task = this.callbacks.getTask(taskId);
        if (
            !task ||
            task.status !== "completed" ||
            task.translationSummary?.failed ||
            task.translationSummary?.pending ||
            task.importState !== "pending"
        ) {
            return;
        }

        if (!task.itemID) {
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError: "无法找到原始条目",
            });
            return;
        }

        const item = Zotero.Items.get(task.itemID);
        if (!item) {
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError: "原始条目已不存在",
            });
            return;
        }

        const attempt = task.attempt || 1;
        const isCurrent = (importState = "importing") => {
            const current = this.callbacks.getTask(taskId);
            return Boolean(
                current &&
                (current.attempt || 1) === attempt &&
                current.itemID === item.id &&
                current.status === "completed" &&
                !current.translationSummary?.failed &&
                !current.translationSummary?.pending &&
                current.importState === importState,
            );
        };

        this.callbacks.updateTask(taskId, {
            importState: "importing",
            importError: undefined,
        });

        try {
            const importedOutputs = [...(task.importedOutputs || [])];
            for (const outputMode of task.outputModes) {
                if (!isCurrent()) return;
                const outputKey = `${attempt}:${outputMode}`;
                if (importedOutputs.includes(outputKey)) continue;
                const bytes = await ServerTaskClient.fetchResult(
                    task.serverUrl,
                    task.taskId,
                    outputMode,
                );
                if (!isCurrent()) return;
                const fileName =
                    task.resultFiles[outputMode] ||
                    `${task.fileName}.${outputMode}.pdf`;
                const output: TaskOutputResponse = {
                    fileName,
                    outputMode,
                    bytes,
                };
                await PDF2zhHelperFactory.handleOutputResponse(
                    output,
                    item,
                    {
                        ...PDF2zhHelperFactory.getServerConfig(false),
                        service: task.service,
                        outputModes: task.outputModes,
                    },
                    isCurrent,
                );
                if (!isCurrent()) return;
                importedOutputs.push(outputKey);
                this.callbacks.updateTask(taskId, {
                    importedOutputs: [...importedOutputs],
                });
            }

            if (!isCurrent()) return;
            this.callbacks.updateTask(taskId, {
                importState: "imported",
                importError: undefined,
            });
            try {
                if (isCurrent("imported")) {
                    this.callbacks.onTaskImported(taskId);
                }
            } catch (error) {
                ztoolkit.log("翻译完成回调执行失败:", error);
            }
        } catch (error) {
            if (!isCurrent()) return;
            this.callbacks.updateTask(taskId, {
                importState: "failed",
                importError:
                    error instanceof Error ? error.message : String(error),
            });
        }
    }
}
