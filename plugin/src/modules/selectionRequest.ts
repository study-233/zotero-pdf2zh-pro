import { recordDiagnostic } from "./diagnostics";

type RequestScope = {
    fetch: typeof fetch;
    AbortController: typeof AbortController;
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
    return {
        abort: () => controller.abort(),
        async post(
            url: string,
            body: unknown,
            timeoutMs: number,
        ): Promise<{ ok: boolean; data: unknown }> {
            if (controller.signal.aborted) throw new Error("请求已取消。");
            let timedOut = false;
            const timer = setTimeout(() => {
                timedOut = true;
                controller.abort();
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
            }
        },
    };
}
export type SelectionRequest = ReturnType<typeof createSelectionRequest>;
