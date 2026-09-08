/* eslint-disable no-restricted-globals -- These scripts run in their own native dialog window. */
/* global window, document */
"use strict";
const args = window.arguments[0];
const $ = (id) => document.getElementById(id);
let selection = "";
function refresh() {
    const entries = args.list();
    if (!entries.some((entry) => entry.key === selection)) selection = "";
    $("list").replaceChildren();
    if (!entries.length) $("list").textContent = "还没有配置，点击新增。";
    for (const entry of entries) {
        const label = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "label",
        );
        const radio = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "input",
        );
        radio.type = "radio";
        radio.name = "profile";
        radio.value = entry.key;
        radio.checked = entry.key === selection;
        radio.addEventListener("change", () => {
            selection = entry.key;
            buttons();
        });
        const text = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "span",
        );
        text.textContent = entry.label;
        const url = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "span",
        );
        url.className = "url";
        url.textContent = entry.apiUrl;
        text.append(url);
        const current = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "span",
        );
        current.className = "current";
        current.textContent = entry.current ? "当前使用" : "";
        label.append(radio, text, current);
        $("list").append(label);
    }
    buttons();
}
function buttons() {
    for (const id of ["edit", "copy", "remove", "top"])
        $(id).disabled = !selection;
}
for (const id of ["add", "edit", "copy", "remove", "top"]) {
    $(id).addEventListener("click", async () => {
        try {
            if (id === "add") await args.edit();
            if (id === "edit") await args.edit(selection);
            if (id === "copy") await args.edit(selection, true);
            if (id === "remove") args.remove(selection);
            if (id === "top") args.top(selection);
            if (!window.closed) refresh();
        } catch (error) {
            $("status").textContent = error.message;
        }
    });
}
$("close").addEventListener("click", () => window.close());
window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") window.close();
});
window.addEventListener("profiles-changed", refresh);
// Re-read saved profiles when returning from the editor, including when the
// preferences pane that opened this manager is no longer mounted.
window.addEventListener("focus", refresh);
refresh();
