import { config } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import {
    DEFAULT_TITLE_TEMPLATE,
    TITLE_VARIABLES,
    renderAttachmentTitle,
    type TitleContext,
} from "./attachmentNaming";
import {
    defaultTitleLayout,
    parseTitleLayout,
    migrateTitleTemplate,
    renderTitleLayout,
    hasTitleContent,
    validTitleLayout,
    type TitleLayout,
} from "./attachmentTitleLayout";

export function registerAttachmentNamingPreferences(window: Window) {
    const doc = window.document;
    const el = (id: string) =>
        doc.getElementById(`zotero-prefpane-${config.addonRef}-${id}`)!;
    const toggle = el("rename") as unknown as XUL.Checkbox;
    // Zotero may bind preference controls after our pane initialization.
    // Seed the checkbox before the first render from the persisted preference.
    toggle.checked = getPref("rename") === true;
    const editor = el("attachmentTitleEditor");
    const list = el("attachmentTitleBlocks");
    const fields = el("attachmentTitleFields");
    const separators = el("attachmentTitleSeparators");
    const custom = el("attachmentTitleSeparatorCustom") as HTMLInputElement;
    const error = el("attachmentTitleError");
    const oldTemplate = String(
        getPref("attachmentTitleTemplate") || DEFAULT_TITLE_TEMPLATE,
    );
    const saved = getPref("attachmentTitleLayout");
    let layout: TitleLayout | undefined = saved
        ? parseTitleLayout(saved)
        : migrateTitleTemplate(oldTemplate);
    if (!saved && layout)
        setPref("attachmentTitleLayout", JSON.stringify(layout));
    let dragging: number | undefined;
    const make = <K extends keyof HTMLElementTagNameMap>(tag: K) =>
        doc.createElementNS(
            "http://www.w3.org/1999/xhtml",
            tag,
        ) as HTMLElementTagNameMap[K];
    const label = (key: string) => getString(`attachment-builder-${key}`);
    const button = (text: string, fn: () => void) => {
        const b = make("button");
        b.type = "button";
        b.textContent = text;
        b.addEventListener("click", fn);
        return b;
    };
    const update = () => {
        editor.hidden = !toggle.checked;
        el("attachmentTitleDisabled").hidden = toggle.checked;
        const valid =
            layout && validTitleLayout(layout) && hasTitleContent(layout);
        error.textContent = !layout
            ? label("legacy")
            : valid
              ? ""
              : label("empty");
        error.hidden = Boolean(valid);
        if (valid) setPref("attachmentTitleLayout", JSON.stringify(layout));
        for (const mode of ["mono", "dual"] as const) {
            const context: TitleContext = {
                title: "Attention Is All You Need",
                fullTitle: "Attention Is All You Need",
                author: "Vaswani et al.",
                year: "2017",
                sourceLang: "en",
                targetLang: "zh-CN",
                service: "openai",
                model: "gpt-4.1-mini",
                type: getString(`attachment-type-${mode}`),
            };
            el(`attachmentTitlePreview-${mode}`).textContent =
                layout && valid
                    ? renderTitleLayout(layout, context)
                    : !layout
                      ? renderAttachmentTitle(oldTemplate, context)
                      : label("empty");
        }
        fields
            .querySelectorAll<HTMLButtonElement>("button[data-field]")
            .forEach((b) => {
                b.disabled =
                    !layout ||
                    layout.blocks.length >= 100 ||
                    layout.blocks.some(
                        (v) =>
                            v.kind === "field" && v.field === b.dataset.field,
                    );
            });
        separators
            .querySelectorAll<HTMLButtonElement>("button")
            .forEach((b) =>
                b.setAttribute(
                    "aria-pressed",
                    String(b.dataset.separator === layout?.separator),
                ),
            );
        custom.disabled = !layout;
        el("attachmentTitleAddText").toggleAttribute(
            "disabled",
            !layout || layout.blocks.length >= 100,
        );
    };
    const move = (from: number, to: number) => {
        if (!layout || to < 0 || to >= layout.blocks.length || from === to)
            return;
        const [block] = layout.blocks.splice(from, 1);
        layout.blocks.splice(to, 0, block);
        render();
        list.children[to]
            ?.querySelector<HTMLButtonElement>(".title-block-handle")
            ?.focus();
    };
    const clearDrop = () =>
        list
            .querySelectorAll(".drop-before,.drop-after")
            .forEach((n) => n.classList.remove("drop-before", "drop-after"));
    const render = () => {
        list.replaceChildren();
        layout?.blocks.forEach((block, index) => {
            const item = make("div");
            item.className = "title-block";
            item.dataset.index = String(index);
            const handle = button("⠿", () => {});
            handle.className = "title-block-handle";
            handle.draggable = true;
            handle.setAttribute("aria-label", label("move"));
            handle.title = label("keyboard");
            handle.addEventListener("keydown", (e) => {
                if (
                    e.altKey &&
                    [
                        "ArrowLeft",
                        "ArrowRight",
                        "ArrowUp",
                        "ArrowDown",
                    ].includes(e.key)
                ) {
                    e.preventDefault();
                    move(
                        index,
                        index +
                            (["ArrowLeft", "ArrowUp"].includes(e.key) ? -1 : 1),
                    );
                }
            });
            handle.addEventListener("dragstart", (e) => {
                dragging = index;
                e.dataTransfer?.setData("text/plain", String(index));
                if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
            });
            handle.addEventListener("dragend", () => {
                dragging = undefined;
                clearDrop();
            });
            item.addEventListener("dragover", (e) => {
                if (dragging === undefined) return;
                e.preventDefault();
                clearDrop();
                const r = item.getBoundingClientRect();
                item.classList.add(
                    e.clientX < r.left + r.width / 2
                        ? "drop-before"
                        : "drop-after",
                );
            });
            item.addEventListener("drop", (e) => {
                if (dragging === undefined) return;
                e.preventDefault();
                e.stopPropagation();
                const r = item.getBoundingClientRect();
                let target =
                    index + (e.clientX >= r.left + r.width / 2 ? 1 : 0);
                if (dragging < target) target--;
                const from = dragging;
                dragging = undefined;
                clearDrop();
                move(from, target);
            });
            item.append(handle);
            if (block.kind === "field") {
                const name = make("span");
                name.textContent = label(block.field);
                item.append(name);
            } else {
                const input = make("input");
                input.type = "text";
                input.value = block.text;
                input.maxLength = 1000;
                input.placeholder = label("text-placeholder");
                input.setAttribute("aria-label", label("text"));
                input.addEventListener("input", () => {
                    block.text = input.value;
                    update();
                });
                item.append(input);
            }
            const previous = button("←", () => move(index, index - 1));
            previous.disabled = index === 0;
            previous.title = label("previous");
            previous.setAttribute("aria-label", label("previous"));
            const next = button("→", () => move(index, index + 1));
            next.disabled = index === layout!.blocks.length - 1;
            next.title = label("next");
            next.setAttribute("aria-label", label("next"));
            const remove = button("×", () => {
                layout!.blocks.splice(index, 1);
                render();
            });
            remove.title = label("remove");
            remove.setAttribute("aria-label", label("remove"));
            item.append(previous, next, remove);
            list.append(item);
        });
        custom.value = layout?.separator || "";
        update();
    };
    fields.replaceChildren();
    for (const field of TITLE_VARIABLES) {
        const b = button(label(field), () => {
            if (
                !layout ||
                layout.blocks.length >= 100 ||
                layout.blocks.some(
                    (v) => v.kind === "field" && v.field === field,
                )
            )
                return;
            layout.blocks.push({ kind: "field", field });
            render();
        });
        b.dataset.field = field;
        fields.append(b);
    }
    separators.replaceChildren();
    for (const [name, value] of [
        ["dot", " · "],
        ["dash", " - "],
        ["underscore", "_"],
        ["space", " "],
        ["none", ""],
    ]) {
        const b = button(label(name), () => {
            if (!layout) return;
            layout.separator = value;
            render();
        });
        b.dataset.separator = value;
        separators.append(b);
    }
    custom.maxLength = 30;
    custom.addEventListener("input", () => {
        if (layout) {
            layout.separator = custom.value;
            update();
        }
    });
    el("attachmentTitleAddText").addEventListener("click", () => {
        if (!layout || layout.blocks.length >= 100) return;
        layout.blocks.push({ kind: "text", text: "" });
        render();
        list.lastElementChild?.querySelector("input")?.focus();
    });
    el("attachmentTitleReset").addEventListener("click", () => {
        layout = defaultTitleLayout();
        render();
    });
    toggle.addEventListener("command", () => {
        setPref("rename", toggle.checked);
        update();
    });
    render();
}
