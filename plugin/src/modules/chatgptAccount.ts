import axios from "axios";
import { getPref } from "../utils/prefs";

export async function chatgptAccountRequest(
    action: "status" | "usage" | "login" | "login/cancel" | "logout",
    body?: Record<string, unknown>,
    signal?: AbortSignal,
) {
    const address = String(getPref("new_serverip") || "").replace(/\/+$/, "");
    const parsed = new URL(address);
    if (!["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname))
        throw new Error("ChatGPT 登录与用量管理需要连接本机服务。");
    try {
        const response = await axios.request({
            url: `${address}/chatgpt/${action}`,
            method: ["status", "usage"].includes(action) ? "GET" : "POST",
            headers: { "X-PDF2ZH-Client": "zotero" },
            data: body || {},
            timeout: 35000,
            signal,
        });
        if (!response.data || response.data.status !== "ok")
            throw new Error("ChatGPT 状态响应无效，请升级服务端。");
        return response.data;
    } catch (error) {
        const message =
            axios.isAxiosError(error) && error.response?.data?.message;
        throw new Error(
            typeof message === "string"
                ? message
                : "无法读取 ChatGPT 登录状态，请检查本地服务。",
        );
    }
}

export function openChatGPTPage(url: string) {
    const parsed = new URL(url);
    const login =
        parsed.protocol === "http:" &&
        parsed.hostname === "127.0.0.1" &&
        /^\/start\/[A-Za-z0-9_-]+$/.test(parsed.pathname);
    const usage = url === "https://chatgpt.com/settings/usage";
    if (
        (!login && !usage) ||
        parsed.username ||
        parsed.password ||
        parsed.search
    )
        throw new Error("ChatGPT 登录地址无效。");
    Zotero.launchURL(url);
}
