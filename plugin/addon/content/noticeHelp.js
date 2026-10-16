/* global window, document */
/* eslint-disable no-restricted-globals -- Native help dialog. */
"use strict";
window.addEventListener("DOMContentLoaded", () => {
    const api = window.arguments[0];
    const t = api.message;
    const create = (tag, text) => {
        const node = document.createElement(tag);
        if (text) node.textContent = text;
        return node;
    };
    const button = (label, action) => {
        const node = create("button", t(label));
        node.addEventListener("click", action);
        return node;
    };
    document.title = t("notice-help-title");
    document.getElementById("title").textContent = document.title;
    const updates = document.getElementById("updates");
    for (const release of api.releases) {
        updates.append(create("h2", `v${release.version}`));
        const list = create("ul");
        for (const note of release.notes) {
            const item = create("li", t(note.message));
            if (note.action)
                item.append(
                    button(`notice-action-${note.action}`, () =>
                        api.action(note.action),
                    ),
                );
            list.append(item);
        }
        updates.append(
            release.notes.length ? list : create("p", t("notice-no-news")),
        );
    }
    updates.append(button("notice-changelog", api.changelog));
    const help = document.getElementById("help");
    help.append(create("h2", t("notice-help-heading")));
    const list = create("ul");
    for (const [key, anchor, action] of [
        ["pdf", "usage", "tasks"],
        ["selection", "selection-translation", "selection"],
        ["profiles", "api-configuration", "profiles"],
        ["troubleshooting", "troubleshooting", "connection"],
    ]) {
        const item = create("li", t(`notice-help-${key}`));
        item.append(
            button(`notice-action-${action}`, () => api.action(action)),
        );
        item.append(button("notice-guide", () => api.guide(anchor)));
        list.append(item);
    }
    help.append(list);
    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") window.close();
    });
});
