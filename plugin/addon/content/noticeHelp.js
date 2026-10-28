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
    const link = (label, url) => {
        const node = create("a", t(label));
        node.href = url;
        node.addEventListener("click", (event) => {
            event.preventDefault();
            api.openLink(node.href);
        });
        return node;
    };
    const notes = (release) => {
        if (!release?.notes.length) return create("p", t("notice-no-news"));
        const list = create("ul");
        list.className = "release-notes";
        for (const note of release.notes)
            list.append(create("li", t(note.message)));
        return list;
    };
    document.title = t("notice-help-title");
    document.getElementById("title").textContent = document.title;
    const updates = document.getElementById("updates");
    const heading = create("div");
    heading.className = "release-heading";
    const title = create("h2", t("notice-updates-heading"));
    title.id = "updates-title";
    heading.append(title, create("span", `v${api.version}`));
    updates.append(
        heading,
        notes(api.releases.find((release) => release.version === api.version)),
    );
    const help = document.getElementById("help");
    const helpTitle = create("h2", t("notice-help-heading"));
    helpTitle.id = "help-title";
    help.append(helpTitle);
    const list = create("ul");
    list.className = "help-topics";
    for (const [key, anchor] of [
        ["pdf", "usage"],
        ["selection", "selection-translation"],
        ["profiles", "api-configuration"],
        ["troubleshooting", "troubleshooting"],
    ]) {
        const item = create("li");
        const topic = link(
            `notice-help-${key}-title`,
            `${api.guideUrl}#${anchor}`,
        );
        const label = create("span", topic.textContent);
        label.className = "help-topic-title";
        topic.replaceChildren(label, create("p", t(`notice-help-${key}`)));
        item.append(topic);
        list.append(item);
    }
    help.append(list);
    const history = document.getElementById("history");
    document.getElementById("history-title").textContent = t("notice-history");
    for (const release of api.releases.filter(
        (release) => release.version !== api.version,
    )) {
        const article = create("article");
        article.append(create("h3", `v${release.version}`), notes(release));
        document.getElementById("history-releases").append(article);
        history.hidden = false;
    }
    const documents = document.getElementById("documents");
    documents.setAttribute("aria-label", t("notice-documents"));
    documents.append(
        link("notice-guide", api.guideUrl),
        link("notice-changelog", api.changelogUrl),
    );
    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") window.close();
    });
});
