import { ServerTaskEvent } from "./pdf2zhTypes";

export type TaskEventStreamState = "connecting" | "open" | "error" | "closed";

type TaskEventStreamEntry = {
    source: EventSource;
    state: TaskEventStreamState;
    lastActivityAt: number;
    activityExpectedSince?: number;
};

const STREAM_STALE_MS = 30_000;

type TaskEventStreamCallbacks = {
    onTaskEvent: (serverUrl: string, event: ServerTaskEvent) => void;
    onStateChange?: (serverUrl: string, state: TaskEventStreamState) => void;
};

export class TaskEventStream {
    private streams = new Map<string, TaskEventStreamEntry>();
    private states = new Map<string, TaskEventStreamState>();

    constructor(private callbacks: TaskEventStreamCallbacks) {}

    sync(serverUrls: Set<string>, activeServerUrls = new Set<string>()): void {
        for (const [serverUrl, entry] of this.streams) {
            if (!serverUrls.has(serverUrl)) {
                entry.source.close();
                this.streams.delete(serverUrl);
                this.setState(serverUrl, "closed");
            }
        }

        for (const serverUrl of serverUrls) {
            const entry = this.streams.get(serverUrl);
            const now = Date.now();
            if (entry) {
                entry.activityExpectedSince = activeServerUrls.has(serverUrl)
                    ? (entry.activityExpectedSince ?? now)
                    : undefined;
                // SSE comments are invisible to EventSource. Only expect task
                // messages while work is active; idle subscriptions may be quiet.
                const expectedSince =
                    entry.state === "open"
                        ? entry.activityExpectedSince
                        : entry.lastActivityAt;
                if (
                    expectedSince !== undefined &&
                    now - Math.max(expectedSince, entry.lastActivityAt) >=
                        STREAM_STALE_MS
                ) {
                    entry.source.close();
                    this.streams.delete(serverUrl);
                    this.setState(serverUrl, "error");
                }
            }
            if (!this.streams.has(serverUrl)) {
                this.open(serverUrl);
            }
        }
    }

    getSummary(): { connected: number; total: number; hasErrors: boolean } {
        let connected = 0;
        let hasErrors = false;

        for (const [serverUrl] of this.streams) {
            const state = this.states.get(serverUrl);
            if (state === "open") {
                connected += 1;
            }
            if (state === "error") {
                hasErrors = true;
            }
        }

        return {
            connected,
            total: this.streams.size,
            hasErrors,
        };
    }

    private open(serverUrl: string): void {
        const mainWindow = Zotero.getMainWindow() as Window & {
            EventSource?: typeof EventSource;
        };
        const EventSourceConstructor =
            typeof EventSource === "undefined"
                ? mainWindow.EventSource
                : EventSource;
        if (!EventSourceConstructor) {
            return;
        }

        let source: EventSource;
        try {
            source = new EventSourceConstructor(`${serverUrl}/tasks/events`);
        } catch (error) {
            this.setState(serverUrl, "error");
            ztoolkit.log("创建任务进度事件连接失败", error);
            return;
        }
        this.streams.set(serverUrl, {
            source,
            state: "connecting",
            lastActivityAt: Date.now(),
        });

        source.onopen = () => {
            if (this.streams.get(serverUrl)?.source !== source) return;
            this.streams.get(serverUrl)!.lastActivityAt = Date.now();
            this.setState(serverUrl, "open");
        };
        source.onmessage = (message) => {
            if (this.streams.get(serverUrl)?.source !== source) return;
            let event: ServerTaskEvent;
            try {
                event = JSON.parse(message.data) as ServerTaskEvent;
            } catch (_error) {
                return;
            }
            this.streams.get(serverUrl)!.lastActivityAt = Date.now();
            this.callbacks.onTaskEvent(serverUrl, event);
        };
        source.onerror = () => {
            if (this.streams.get(serverUrl)?.source !== source) return;
            this.setState(serverUrl, "error");
            ztoolkit.log(`任务进度事件连接异常: ${serverUrl}`);
        };
        this.setState(serverUrl, "connecting");
    }

    private setState(serverUrl: string, state: TaskEventStreamState): void {
        this.states.set(serverUrl, state);
        const entry = this.streams.get(serverUrl);
        if (entry) {
            entry.state = state;
        }
        this.callbacks.onStateChange?.(serverUrl, state);
    }
}
