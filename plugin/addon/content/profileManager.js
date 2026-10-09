/* eslint-disable no-restricted-globals -- Native configuration dialog. */
/* global window, document */
"use strict";
(() => {
    const args = window.arguments[0];
    const $ = (id) => document.getElementById(id);
    const t = args.message;
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const make = (tag, text, className) => {
        const node = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            tag,
        );
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    document.title = t("title");
    for (const node of document.querySelectorAll("[data-i18n]"))
        node.textContent = t(node.dataset.i18n);
    for (const node of document.querySelectorAll("[data-placeholder]"))
        node.setAttribute("placeholder", t(node.dataset.placeholder));
    for (const node of document.querySelectorAll("[data-label]"))
        node.setAttribute("aria-label", t(node.dataset.label));
    const records = new Map();
    let active,
        loaded = false,
        newSequence = 0,
        category = "relay",
        menuAnchor,
        menuKey;
    const signature = (state) =>
        JSON.stringify([state.presetId, state.values, state.tested]);
    const editor = window.createProfileEditor(args, () => {
        stash();
        renderList();
    });
    function recordFor(api, id = api.key) {
        const presetId = args.resolvePreset(api);
        const state = { profile: clone(api), presetId };
        return {
            id,
            profile: clone(api),
            presetId,
            states: new Map([[presetId || "legacy", state]]),
            initial: undefined,
            dirty: !api.key,
        };
    }
    function stash() {
        if (!active || !loaded) return;
        const record = records.get(active);
        if (!record) return;
        const state = editor.snapshot();
        record.states.set(record.presetId || "legacy", state);
        if (record.initial === undefined && record.profile.key)
            record.initial = signature(state);
        record.dirty =
            !record.profile.key || signature(state) !== record.initial;
    }
    function currentState(record) {
        return record.states.get(record.presetId || "legacy");
    }
    function displayName(record) {
        return (
            currentState(record)?.values?.name ||
            record.profile.name ||
            t("new-profile")
        );
    }
    function renderList() {
        const scrollTop = $("list").scrollTop;
        const saved = args.list();
        for (const api of saved)
            if (!records.has(api.key)) records.set(api.key, recordFor(api));
        // External deletion removes clean rows, while unsaved drafts remain recoverable.
        for (const [id, record] of records)
            if (
                record.profile.key &&
                !saved.some((api) => api.key === id) &&
                !record.dirty &&
                id !== active
            )
                records.delete(id);
        const ordered = [
            ...saved.map((api) => records.get(api.key)),
            ...[...records.values()].filter(
                (r) => !saved.some((api) => api.key === r.id),
            ),
        ];
        const query = $("search").value.trim().toLowerCase();
        $("list").replaceChildren();
        $("compact-list").replaceChildren();
        for (const record of ordered) {
            const state = currentState(record);
            const preset = args.presets.find(
                (item) => item.id === record.presetId,
            );
            const model = state?.values?.model ?? record.profile.model;
            const description = [preset?.label || record.profile.service, model]
                .filter(Boolean)
                .join(" · ");
            const name = displayName(record);
            const badges = [
                record.id === args.current() ? t("current") : "",
                !preset && record.profile.service ? t("retired-short") : "",
            ]
                .filter(Boolean)
                .join(" · ");
            const option = make(
                "option",
                name +
                    (record.dirty ? " · " + t("unsaved") : "") +
                    (badges ? " · " + badges : ""),
            );
            option.value = record.id;
            $("compact-list").append(option);
            if (
                query &&
                !(name + " " + description).toLowerCase().includes(query)
            )
                continue;
            const row = make(
                "div",
                undefined,
                "profile-row" + (active === record.id ? " selected" : ""),
            );
            const button = make("button", undefined, "profile-select");
            button.setAttribute("aria-pressed", String(active === record.id));
            button.dataset.key = record.id;
            const title = make("span", undefined, "row-title");
            title.append(make("span", name, "name"));
            if (record.dirty) {
                const dirty = make("span", "•", "badge dirty");
                dirty.title = t("unsaved");
                dirty.setAttribute("aria-label", t("unsaved"));
                title.append(dirty);
            }
            if (record.id === args.current())
                title.append(make("span", t("current"), "badge"));
            button.append(title, make("span", description, "description"));
            if (!preset && record.profile.service)
                button.append(
                    make("span", t("retired-short"), "badge retired-badge"),
                );
            button.title = [
                name,
                description,
                badges,
                record.dirty ? t("unsaved") : "",
            ]
                .filter(Boolean)
                .join("\n");
            button.addEventListener("click", () => openRecord(record.id));
            const more = make("button", "⋯", "more");
            more.setAttribute("aria-label", t("more"));
            more.title = t("more");
            more.setAttribute("aria-haspopup", "menu");
            more.addEventListener("click", () => openMenu(more, record.id));
            row.append(button, more);
            $("list").append(row);
        }
        if (!$("list").children.length)
            $("list").append(
                make("p", t(query ? "search-empty" : "empty-hint"), "hint"),
            );
        else if (!saved.length && !query)
            $("list").prepend(make("p", t("empty-hint"), "hint"));
        $("compact-list").value = active || "";
        $("compact-more").disabled = !active;
        $("list").scrollTop = scrollTop;
    }
    function openRecord(id) {
        closeMenu(false);
        editor.cancel();
        stash();
        active = id;
        const record = records.get(id);
        const state = currentState(record);
        if (!record.presetId && !record.profile.service) {
            loaded = false;
            showPicker();
        } else {
            editor.load(state);
            loaded = true;
            $("profile-form").hidden = false;
            $("platform-picker").hidden = true;
            stash();
        }
        renderList();
    }
    function add() {
        editor.cancel();
        stash();
        category = "relay";
        const id = "draft-" + ++newSequence;
        records.set(
            id,
            recordFor(
                {
                    key: "",
                    name: "",
                    service: "",
                    apiKey: "",
                    apiUrl: "",
                    model: "",
                },
                id,
            ),
        );
        openRecord(id);
    }
    function showPicker() {
        editor.cancel();
        stash();
        $("profile-form").hidden = true;
        $("platform-picker").hidden = false;
        $("picker-back").hidden = !loaded;
        renderPlatforms();
    }
    function renderPlatforms() {
        $("categories").replaceChildren();
        for (const id of ["relay", "official", "local", "translation"]) {
            const tab = make("button", t("category-" + id));
            tab.id = "category-" + id;
            tab.setAttribute("role", "tab");
            tab.setAttribute("aria-selected", String(id === category));
            tab.setAttribute("aria-controls", "platforms");
            tab.tabIndex = id === category ? 0 : -1;
            tab.addEventListener("click", () => {
                category = id;
                renderPlatforms();
                $(tab.id).focus();
            });
            tab.addEventListener("keydown", (event) => {
                const ids = ["relay", "official", "local", "translation"];
                if (
                    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                        event.key,
                    )
                )
                    return;
                event.preventDefault();
                category =
                    event.key === "Home"
                        ? ids[0]
                        : event.key === "End"
                          ? ids[3]
                          : ids[
                                (ids.indexOf(category) +
                                    (event.key === "ArrowRight" ? 1 : 3)) %
                                    4
                            ];
                renderPlatforms();
                $("category-" + category).focus();
            });
            $("categories").append(tab);
        }
        $("platforms").setAttribute("aria-labelledby", "category-" + category);
        $("platforms").replaceChildren();
        for (const preset of args.presets.filter(
            (item) => item.group === category,
        )) {
            const button = make("button", preset.label);
            button.dataset.preset = preset.id;
            button.addEventListener("click", () => {
                stash();
                const record = records.get(active);
                const name =
                    currentState(record)?.values?.name ??
                    record.profile.name ??
                    "";
                if (!record.states.has(preset.id))
                    record.states.set(preset.id, {
                        presetId: preset.id,
                        profile: {
                            key: record.profile.key,
                            name,
                            service: preset.service,
                            providerPreset: preset.id,
                            apiUrl: preset.url || "",
                            apiKey: "",
                            model: "",
                            apiProtocol: "chat_completions",
                            needsTest: true,
                        },
                    });
                record.presetId = preset.id;
                // Do not stash the old form again under the new platform ID.
                loaded = false;
                openRecord(active);
                $("name").focus();
            });
            $("platforms").append(button);
        }
    }
    function save(use) {
        try {
            editor.cancel();
            const value = editor.read();
            const saved = args.save(value, use);
            records.delete(active);
            records.set(saved.key, recordFor(saved));
            active = undefined;
            loaded = false;
            openRecord(saved.key);
            editor.message(t(use ? "saved-use" : "saved"));
        } catch (error) {
            editor.message(error.message || t("failed"), true);
        }
    }
    function closeMenu(focus = true) {
        if ($("profile-menu").hidden) return;
        $("profile-menu").hidden = true;
        menuAnchor?.setAttribute("aria-expanded", "false");
        if (focus && menuAnchor?.isConnected) menuAnchor.focus();
    }
    function openMenu(anchor, id) {
        closeMenu(false);
        menuKey = id;
        menuAnchor = anchor;
        anchor.setAttribute("aria-expanded", "true");
        const menu = $("profile-menu");
        menu.hidden = false;
        menu.querySelector('[data-action="top"]').disabled =
            !records.get(id)?.profile.key;
        const rect = anchor.getBoundingClientRect();
        menu.style.left =
            Math.max(
                8,
                Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8),
            ) + "px";
        menu.style.top =
            Math.max(
                8,
                Math.min(
                    rect.bottom,
                    window.innerHeight - menu.offsetHeight - 8,
                ),
            ) + "px";
        menu.querySelector("button").focus();
    }
    $("profile-menu").addEventListener("keydown", (event) => {
        const buttons = [
            ...$("profile-menu").querySelectorAll("button:not(:disabled)"),
        ];
        if (event.key === "Escape") {
            event.stopPropagation();
            closeMenu();
        }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const index = buttons.indexOf(document.activeElement);
            buttons[
                event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? buttons.length - 1
                      : (index +
                            (event.key === "ArrowDown"
                                ? 1
                                : buttons.length - 1)) %
                        buttons.length
            ].focus();
        }
        if (event.key === "Tab") closeMenu(false);
    });
    for (const button of $("profile-menu").querySelectorAll("button"))
        button.addEventListener("click", () => {
            const id = menuKey,
                action = button.dataset.action;
            closeMenu();
            stash();
            const record = records.get(id);
            if (!record) return;
            if (action === "copy") {
                const copy = clone(
                    currentState(record) || { profile: record.profile },
                );
                copy.profile.key = "";
                copy.profile.needsTest = true;
                copy.tested = "";
                copy.status = t("untested");
                copy.error = false;
                const name = displayName(record) + " " + t("copy-suffix");
                copy.profile.name = name;
                if (copy.values) copy.values.name = name;
                const key = "draft-" + ++newSequence;
                const next = recordFor(copy.profile, key);
                next.presetId = record.presetId;
                next.states = new Map([[next.presetId || "legacy", copy]]);
                records.set(key, next);
                openRecord(key);
            }
            if (action === "top") {
                args.top(id);
                renderList();
            }
            if (
                action === "remove" &&
                window.confirm(
                    t("remove-confirm", { name: displayName(record) }),
                )
            ) {
                if (id === active) {
                    editor.cancel();
                    loaded = false;
                    active = undefined;
                }
                if (record.profile.key) args.remove(record.profile.key);
                records.delete(id);
                if (!active) {
                    const next =
                        args.list()[0]?.key || records.keys().next().value;
                    if (next) openRecord(next);
                    else add();
                } else renderList();
            }
        });
    document.addEventListener("pointerdown", (event) => {
        if (
            !$("profile-menu").contains(event.target) &&
            event.target !== menuAnchor
        )
            closeMenu(false);
    });
    window.addEventListener("resize", () => closeMenu(false));
    $("add").addEventListener("click", add);
    $("search").addEventListener("input", () => {
        $("list").scrollTop = 0;
        renderList();
    });
    $("list").addEventListener("keydown", (event) => {
        if (
            !event.target.classList.contains("profile-select") ||
            !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)
        )
            return;
        event.preventDefault();
        const buttons = [...$("list").querySelectorAll(".profile-select")];
        const index = buttons.indexOf(event.target);
        const next =
            event.key === "Home"
                ? 0
                : event.key === "End"
                  ? buttons.length - 1
                  : (index +
                        (event.key === "ArrowDown" ? 1 : buttons.length - 1)) %
                    buttons.length;
        const id = buttons[next].dataset.key;
        openRecord(id);
        [...$("list").querySelectorAll(".profile-select")]
            .find((button) => button.dataset.key === id)
            ?.focus();
    });
    $("compact-list").addEventListener("change", () =>
        openRecord($("compact-list").value),
    );
    $("compact-more").addEventListener("click", () =>
        openMenu($("compact-more"), active),
    );
    $("change-platform").addEventListener("click", showPicker);
    $("picker-back").addEventListener("click", () => {
        $("platform-picker").hidden = true;
        $("profile-form").hidden = false;
    });
    $("save-only").addEventListener("click", () => save(false));
    $("profile-form").addEventListener("submit", (event) => {
        event.preventDefault();
        save(true);
    });
    function mayClose() {
        stash();
        // An untouched new-platform chooser is not an unsaved configuration.
        const dirty = [...records.values()].some(
            (r) => r.dirty && (r.presetId || r.profile.service),
        );
        return !dirty || window.confirm(t("discard-confirm"));
    }
    window.addEventListener("close", (event) => {
        if (!mayClose()) event.preventDefault();
    });
    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            if (!$("platform-picker").hidden && loaded) {
                $("picker-back").click();
                return;
            }
            window.close();
        }
    });
    window.addEventListener("unload", () => editor.cancel());
    window.addEventListener("profiles-changed", renderList);
    renderList();
    const first =
        args.list().find((api) => api.key === args.current()) || args.list()[0];
    if (first) openRecord(first.key);
    else add();
})();
