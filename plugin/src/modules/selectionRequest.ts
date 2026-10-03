import { recordDiagnostic } from "./diagnostics";

type RequestScope = {
    fetch: typeof fetch;
    AbortController: typeof AbortController;
    crypto?: Crypto;
};

/** Resolve fetch and AbortSignal from the same Zotero realm. */
export function createSelectionRequest() {
    let scope: RequestScope | undefined;
    try {
        const win = Zotero.getMainWindow();
        if (
            typeof win?.fetch === "function" &&
            typeof win.AbortController === "function"
        )
            scope = win as unknown as RequestScope;
    } catch {
        /* The main window may be closing. */
    }
    if (
        !scope &&
        typeof globalThis.fetch === "function" &&
        typeof globalThis.AbortController === "function"
    )
        scope = globalThis;
    if (!scope)
        throw new Error(
            "当前 Zotero 环境无法发起翻译请求，请重启 Zotero 后重试。",
        );
    const runtime = scope;
    const controller = new runtime.AbortController();
    let cancelActive: (() => void) | undefined;
    const abort = () => {
        cancelActive?.();
        controller.abort();
    };
    return {
        abort,
        async post(
            url: string,
            body: unknown,
            timeoutMs: number,
            cancellation?: { cancelUrl: string },
        ): Promise<{ ok: boolean; data: unknown }> {
            if (controller.signal.aborted) throw new Error("请求已取消。");
            let cancel: (() => void) | undefined;
            if (cancellation) {
                const crypto = runtime.crypto || globalThis.crypto;
                if (!crypto?.getRandomValues)
                    throw new Error(
                        "无法生成安全的翻译请求标识，请升级 Zotero 后重试。",
                    );
                let requestId: string;
                if (typeof crypto.randomUUID === "function")
                    requestId = crypto.randomUUID();
                else {
                    const bytes = new Uint8Array(16);
                    crypto.getRandomValues(bytes);
                    requestId = Array.from(bytes, (byte) =>
                        byte.toString(16).padStart(2, "0"),
                    ).join("");
                }
                body = { ...(body as Record<string, unknown>), requestId };
                let sent = false;
                cancel = () => {
                    if (sent) return;
                    sent = true;
                    // Cancellation must survive aborting the original fetch.
                    const cancellationController =
                        new runtime.AbortController();
                    const cancellationTimer = setTimeout(
                        () => cancellationController.abort(),
                        2000,
                    );
                    void (async () => {
                        try {
                            await runtime.fetch.call(
                                runtime,
                                cancellation.cancelUrl,
                                {
                                    method: "POST",
                                    headers: {
                                        "Content-Type": "application/json",
                                    },
                                    body: JSON.stringify({ requestId }),
                                    signal: cancellationController.signal,
                                },
                            );
                        } catch {
                            // The server's own deadline remains the fallback.
                        } finally {
                            clearTimeout(cancellationTimer);
                        }
                    })();
                };
                cancelActive = cancel;
            }
            let timedOut = false;
            const timer = setTimeout(() => {
                timedOut = true;
                abort();
            }, timeoutMs);
            try {
                const response = await runtime.fetch.call(runtime, url, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                    signal: controller.signal,
                });
                recordDiagnostic("selection_http_response", {
                    statusCode: response.status,
                });
                const data: unknown = await response.json();
                if (controller.signal.aborted) throw new Error("请求已取消。");
                return { ok: response.ok, data };
            } catch {
                recordDiagnostic(
                    timedOut
                        ? "selection_http_timeout"
                        : controller.signal.aborted
                          ? "selection_http_cancelled"
                          : "selection_http_failure",
                );
                throw new Error(
                    timedOut
                        ? "翻译请求超时，请重新划选重试。"
                        : "请求未完成，请检查本地服务后重试。",
                );
            } finally {
                clearTimeout(timer);
                if (cancelActive === cancel) cancelActive = undefined;
            }
        },
    };
}
export type SelectionRequest = ReturnType<typeof createSelectionRequest>;
