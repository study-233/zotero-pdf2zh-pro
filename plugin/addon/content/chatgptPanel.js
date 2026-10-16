/* eslint-disable no-restricted-globals -- Native configuration dialog. */
/* global window, document */
"use strict";
window.createChatGPTPanel = function (args, changed) {
    const $ = (id) => document.getElementById(id);
    const t = args.message;
    let active = false,
        generation = 0,
        timer,
        controller,
        account,
        lastState,
        openedUrl;
    function cancel() {
        generation++;
        controller?.abort();
        window.clearTimeout(timer);
    }
    function accountStatus(text) {
        const node = $("chatgpt-account-status");
        if (node.textContent !== text) node.textContent = text;
    }
    function draw(state) {
        lastState = state;
        account = state.account;
        const cancelling = state.login?.status === "cancelling";
        const pending = ["starting", "pending", "cancelling"].includes(
            state.login?.status,
        );
        const expired = account?.requires_login === true;
        accountStatus(
            cancelling
                ? t("chatgpt-cancelling")
                : pending
                  ? t("chatgpt-pending")
                  : expired
                    ? t("chatgpt-expired")
                    : account?.authenticated
                      ? `${account.account_label} · ${t(account.plan_usage_authorized ? "chatgpt-authorized" : "chatgpt-no-permission")}`
                      : state.login?.status === "failed"
                        ? t("chatgpt-login-failed")
                        : state.login?.status === "cancelled"
                          ? t("chatgpt-cancelled")
                          : t("chatgpt-signed-out"),
        );
        $("chatgpt-account-status").dataset.error = String(expired && !pending);
        $("chatgpt-login").textContent = t(
            expired ? "chatgpt-relogin" : "chatgpt-login",
        );
        $("chatgpt-login").disabled = $("chatgpt-switch").disabled = pending;
        $("chatgpt-logout").disabled =
            !(account?.authenticated || expired) || pending;
        $("chatgpt-cancel").hidden = !pending;
        $("chatgpt-cancel").disabled = cancelling;
        $("chatgpt-switch").hidden = !account?.authenticated;
        if (
            pending &&
            !cancelling &&
            state.login.loginUrl &&
            openedUrl !== state.login.loginUrl
        ) {
            openedUrl = state.login.loginUrl;
            args.openChatGPTPage(openedUrl);
        }
        return pending;
    }
    async function refresh(poll = false) {
        if (!active || !args.chatgptAccount) return;
        cancel();
        const seq = generation;
        controller = new window.AbortController();
        try {
            const state = await args.chatgptAccount(
                "status",
                {},
                controller.signal,
            );
            if (!active || seq !== generation || window.closed) return;
            const previous = [
                account?.account_fingerprint,
                account?.authenticated,
                account?.plan_usage_authorized,
            ].join(":");
            const pending = draw(state);
            if (
                previous !==
                [
                    account?.account_fingerprint,
                    account?.authenticated,
                    account?.plan_usage_authorized,
                ].join(":")
            )
                changed();
            if (pending) timer = window.setTimeout(() => refresh(true), 1500);
            else if (poll) changed();
        } catch (error) {
            if (seq === generation && active)
                accountStatus(args.errorMessage?.(error) || t("failed"));
        } finally {
            if (seq === generation) controller = undefined;
        }
    }
    async function action(kind, newAccount = false) {
        cancel();
        accountStatus("");
        $("chatgpt-notice").textContent = "";
        const seq = generation;
        controller = new window.AbortController();
        for (const id of ["login", "switch", "logout", "refresh", "cancel"])
            $("chatgpt-" + id).disabled = true;
        try {
            const result = await args.chatgptAccount(
                kind,
                { newAccount },
                controller.signal,
            );
            if (seq !== generation || !active || window.closed) return;
            if (kind === "login/cancel" && result.login)
                draw({ account, login: result.login });
            if (kind === "logout" && result.revocationConfirmed === false)
                $("chatgpt-notice").textContent = t(
                    "chatgpt-revocation-unknown",
                );
            changed();
            await refresh();
        } catch (error) {
            if (seq === generation && active) {
                if (lastState) draw(lastState);
                accountStatus(args.errorMessage?.(error) || t("failed"));
            }
        } finally {
            if (seq === generation) controller = undefined;
            if (active && !window.closed) $("chatgpt-refresh").disabled = false;
        }
    }
    $("chatgpt-login").addEventListener("click", () => action("login"));
    $("chatgpt-switch").addEventListener("click", () => action("login", true));
    $("chatgpt-cancel").addEventListener("click", () => action("login/cancel"));
    $("chatgpt-logout").addEventListener("click", () => action("logout"));
    $("chatgpt-refresh").addEventListener("click", () => {
        accountStatus("");
        void refresh();
    });
    $("chatgpt-manage-usage").addEventListener("click", () =>
        args.openChatGPTPage("https://chatgpt.com/settings/usage"),
    );
    window.addEventListener("unload", cancel);
    window.addEventListener("focus", () => {
        if (active && !controller) void refresh();
    });
    return {
        refresh,
        login: () => action("login"),
        signInMessage: () =>
            t(
                account?.requires_login
                    ? "chatgpt-expired"
                    : "chatgpt-sign-in-first",
            ),
        load(enabled) {
            active = enabled;
            cancel();
            $("chatgpt-account").hidden = !enabled;
            if (enabled) void refresh();
        },
        accountId() {
            return account?.authenticated && account.plan_usage_authorized
                ? account.account_fingerprint
                : undefined;
        },
    };
};
