/** User-facing feedback. Keep raw diagnostics out of these messages. */
export type NoticeLevel = "info" | "success" | "warning" | "error";
export type NoticeAction = "profiles" | "connection" | "selection" | "tasks";
export type NoticeTranslator = (
    key: string,
    options?: { args?: Record<string, unknown> },
) => string;
export interface UserNotice {
    id: string;
    level: NoticeLevel;
    text: string;
    action?: NoticeAction;
    actionLabel?: string;
}

export class NoticeError extends Error {
    constructor(public code: string) {
        super(code);
    }
}

export function errorNotice(error: unknown, t: NoticeTranslator): UserNotice {
    const value = error as {
        code?: string;
        message?: string;
        diagnostics?: { code?: string }[];
    } | null;
    const codes = [
        value?.code,
        ...(Array.isArray(value?.diagnostics) ? value.diagnostics : []).map(
            (d) => d?.code,
        ),
    ];
    const known: Record<string, [string, NoticeAction?]> = {
        stream_unsupported: ["selection-error-stream"],
        provider_timeout: ["selection-error-timeout"],
        provider_error: ["selection-error-request"],
        invalid_output: ["selection-error-format"],
        selection_cancelled: ["selection-error-cancelled"],
        chatgpt_network_error: ["network"],
        no_selection: ["select-item"],
        no_profile: ["select-profile", "profiles"],
        no_output: ["select-output", "connection"],
        server_unreachable: ["server", "connection"],
        server_capability: ["upgrade", "connection"],
        server_maintenance: ["maintenance"],
        task_store_unavailable: ["task-store", "connection"],
        task_persistence_failed: ["task-save"],
        task_bindings_unavailable: ["task-bindings"],
        task_bindings_write_failed: ["task-bindings-save"],
        favorites_protected: ["favorites-protected"],
        chatgpt_sign_in_required: ["login", "profiles"],
        chatgpt_usage_limit: ["quota", "profiles"],
        llm_auth: ["auth", "profiles"],
        llm_model: ["model", "profiles"],
        llm_parameters: ["parameters", "profiles"],
        llm_protocol: ["protocol", "profiles"],
        llm_rate_limit: ["rate-limit"],
        network_connectivity: ["network"],
        network_tls: ["certificate"],
        output_missing: ["output"],
        invalid_page_range: ["pages", "connection"],
        import_failed: ["import"],
        unchanged_translation: ["unchanged-translation"],
        target_language_missing: ["translation-language"],
        placeholder_mismatch: ["translation-placeholders"],
    };
    let match = codes.map((code) => code && known[code]).find(Boolean) as
        [string, NoticeAction?] | undefined;
    // Older services may only return text. Match specific causes, never echo it.
    if (!match) {
        const text = typeof error === "string" ? error : value?.message || "";
        const rules: [RegExp, string, NoticeAction?][] = [
            [/收藏(?:文件|字段|选区).*无效|不支持的收藏文件/, "favorite-file"],
            [/原附件已删除|找不到当前 PDF/, "attachment-missing"],
            [/附件尚未下载/, "attachment-download"],
            [/此结果已导入.*附件已删除/, "import-deleted"],
            [/此结果已导入.*文件不可用/, "import-file"],
            [/无法访问该群组库|找不到所属文献库/, "library"],
            [/暂时无法续期|temporarily.*renew/i, "network"],
            [
                /chatgpt_sign_in_required|登录已失效|授权已失效|请先登录 ChatGPT/i,
                "login",
                "profiles",
            ],
            [
                /用量已达上限|insufficient_quota|chatgpt_usage_limit/i,
                "quota",
                "profiles",
            ],
            [
                /invalid_api_key|AuthenticationError|密钥验证失败|HTTP.?401/i,
                "auth",
                "profiles",
            ],
            [
                /请先.*选择翻译配置|配置已删除|配置不可用|Codex 配置已停用/i,
                "select-profile",
                "profiles",
            ],
            [/升级.*服务|服务端.*升级|不支持.*协议/i, "upgrade", "connection"],
            [/维护|server_maintenance/i, "maintenance"],
            [
                /本地服务.*(?:连接|地址)|无法连接本地服务|本机服务/i,
                "server",
                "connection",
            ],
            [/TimeoutError|超时|timed?\s*out/i, "timeout"],
            [/RateLimit|Too Many Requests|频率|HTTP.?429/i, "rate-limit"],
            [/TLS|certificate|证书/i, "certificate"],
            [
                /NetworkError|Failed to fetch|APIConnectionError|无法连接翻译服务/i,
                "network",
            ],
            [/模型.*不可用|model_not_found/i, "model", "profiles"],
            [/unchanged_translation/i, "unchanged-translation"],
            [/target_language_missing/i, "translation-language"],
            [/placeholder_mismatch/i, "translation-placeholders"],
            [/InvalidTranslation/i, "translation-validation"],
            [/没有.*PDF|未找到.*PDF|不是.*PDF/i, "pdf"],
        ];
        const rule = rules.find(([pattern]) => pattern.test(text));
        if (rule) match = [rule[1], rule[2]];
    }
    const [key, action] = match || ["unknown"];
    return {
        id: `error-${key}`,
        level: "error",
        text: t(
            key.startsWith("selection-error-") ? key : `notice-error-${key}`,
        ),
        action,
        actionLabel: action ? t(`notice-action-${action}`) : undefined,
    };
}

type Slot = { fingerprint: string; cancel: () => void };
const slots = new WeakMap<Element, Slot>();

/** A single status slot per surface. Repeated polling must not re-announce it. */
export function renderNotice(
    node: HTMLElement,
    notice: UserNotice | null,
    onAction?: (action: NoticeAction) => void,
    restore?: () => void,
) {
    const fingerprint = JSON.stringify(notice);
    if (slots.get(node)?.fingerprint === fingerprint) return;
    slots.get(node)?.cancel();
    const win = node.ownerDocument.defaultView!;
    let timer: number | undefined;
    const cancel = () => {
        if (timer !== undefined) win.clearTimeout(timer);
        win.removeEventListener("unload", cancel);
    };
    slots.set(node, { fingerprint, cancel });
    win.addEventListener("unload", cancel, { once: true });
    node.removeAttribute("data-l10n-id");
    node.removeAttribute("data-l10n-args");
    node.setAttribute("role", notice?.level === "error" ? "alert" : "status");
    node.setAttribute("aria-atomic", "true");
    node.setAttribute(
        "aria-live",
        notice?.level === "error" ? "assertive" : "polite",
    );
    node.dataset.level = notice?.level || "info";
    node.style.overflowWrap = "anywhere";
    node.textContent = notice?.text || "";
    node.hidden = !notice;
    if (notice?.action && onAction) {
        const button = node.ownerDocument.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "button",
        );
        button.textContent = notice.actionLabel || notice.action;
        button.style.marginInlineStart = "8px";
        button.addEventListener("click", () => onAction(notice.action!));
        node.append(button);
    }
    if (notice?.level === "success") {
        timer = win.setTimeout(() => {
            if (slots.get(node)?.fingerprint !== fingerprint) return;
            renderNotice(node, null);
            if (node.isConnected) restore?.();
        }, 4000);
    }
}
