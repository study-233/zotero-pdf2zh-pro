const HTML = "http://www.w3.org/1999/xhtml";
const SVG = "http://www.w3.org/2000/svg";

// Lucide icon geometry (ISC / Feather MIT); see addon/content/icons/selection/LICENSE.txt.
// Inline SVG avoids chrome:// image loads from the unprivileged Reader document.
const icons: Record<string, [string, Record<string, string>][]> = {
    ellipsis: [
        [
            "circle",
            {
                cx: "12",
                cy: "12",
                r: "1",
            },
        ],
        [
            "circle",
            {
                cx: "19",
                cy: "12",
                r: "1",
            },
        ],
        [
            "circle",
            {
                cx: "5",
                cy: "12",
                r: "1",
            },
        ],
    ],
    "external-link": [
        [
            "path",
            {
                d: "M15 3h6v6",
            },
        ],
        [
            "path",
            {
                d: "M10 14 21 3",
            },
        ],
        [
            "path",
            {
                d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
            },
        ],
    ],
    "panel-right": [
        [
            "rect",
            {
                width: "18",
                height: "18",
                x: "3",
                y: "3",
                rx: "2",
            },
        ],
        [
            "path",
            {
                d: "M15 3v18",
            },
        ],
    ],
    pin: [
        [
            "path",
            {
                d: "M12 17v5",
            },
        ],
        [
            "path",
            {
                d: "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z",
            },
        ],
    ],
    star: [
        [
            "path",
            {
                d: "M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z",
            },
        ],
    ],
    "volume-2": [
        [
            "path",
            {
                d: "M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z",
            },
        ],
        [
            "path",
            {
                d: "M16 9a5 5 0 0 1 0 6",
            },
        ],
        [
            "path",
            {
                d: "M19.364 18.364a9 9 0 0 0 0-12.728",
            },
        ],
    ],
    x: [
        [
            "path",
            {
                d: "M18 6 6 18",
            },
        ],
        [
            "path",
            {
                d: "m6 6 12 12",
            },
        ],
    ],
};

export function selectionIcon(
    button: HTMLButtonElement,
    icon: string,
    label: string,
) {
    button.textContent = "";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.classList.add("st-icon-button");
    const svg = button.ownerDocument.createElementNS(SVG, "svg");
    for (const [name, value] of Object.entries({
        class: "st-icon",
        viewBox: "0 0 24 24",
        width: "16",
        height: "16",
        fill: "none",
        stroke: "currentColor",
        "stroke-width": "2",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        "aria-hidden": "true",
        focusable: "false",
    }))
        svg.setAttribute(name, value);
    for (const [tag, attributes] of icons[icon]) {
        const shape = button.ownerDocument.createElementNS(SVG, tag);
        for (const [name, value] of Object.entries(attributes))
            shape.setAttribute(name, value);
        svg.append(shape);
    }
    button.append(svg);
}

export const selectionMenuStyle = `
.st-icon-button { flex:0 0 auto; width:28px; height:28px; padding:5px !important; border-color:transparent !important; background:transparent !important; }
.st-icon { display:block; flex:none; width:16px; height:16px; pointer-events:none; }
.st-menu { position:fixed; inset:auto; margin:0; padding:4px; z-index:2147483647; min-width:160px; max-width:calc(100vw - 16px); overflow:auto; border:1px solid var(--st-border, GrayText); border-radius:6px; background:var(--st-bg, Canvas); color:var(--st-text, CanvasText); box-shadow:0 4px 16px #0003; font:13px/1.5 system-ui; }
.st-menu[hidden] { display:none !important; }
.st-menu button { display:flex; width:100%; min-height:30px; margin:0; padding:5px 10px; text-align:start; justify-content:flex-start; border:0; border-radius:3px; background:transparent; color:inherit; font:inherit; cursor:pointer; }
.st-menu button:hover, .st-menu button:focus-visible { background:var(--st-soft, ButtonFace); outline:2px solid var(--st-accent, Highlight); outline-offset:-2px; }
`;

/** A top-layer menu stays inside its owning card for Reader event isolation. */
export function createSelectionMenu(owner: HTMLElement, label: string) {
    const doc = owner.ownerDocument;
    const win = doc.defaultView!;
    const trigger = doc.createElementNS(HTML, "button") as HTMLButtonElement;
    trigger.type = "button";
    selectionIcon(trigger, "ellipsis", label);
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    const menu = doc.createElementNS(HTML, "div");
    menu.className = "st-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", label);
    menu.hidden = true;
    const topLayer = typeof menu.showPopover === "function";
    if (topLayer) menu.setAttribute("popover", "manual");
    owner.append(menu);
    const items = () =>
        Array.from(
            menu.querySelectorAll("button:not([hidden]):not(:disabled)"),
        ) as unknown as HTMLButtonElement[];
    const position = () => {
        if (menu.hidden) return;
        const anchor = trigger.getBoundingClientRect();
        menu.style.maxHeight = `${Math.max(40, win.innerHeight - 16)}px`;
        const rect = menu.getBoundingClientRect();
        const top =
            anchor.bottom + rect.height + 4 > win.innerHeight - 8
                ? anchor.top - rect.height - 4
                : anchor.bottom + 4;
        menu.style.left = `${Math.max(8, Math.min(anchor.right - rect.width, win.innerWidth - rect.width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(top, win.innerHeight - rect.height - 8))}px`;
    };
    const close = (focus = false) => {
        if (menu.hidden) return;
        if (topLayer) menu.hidePopover();
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        if (focus) trigger.focus();
    };
    const open = (last = false) => {
        for (const item of items()) {
            item.setAttribute("role", "menuitem");
            item.tabIndex = -1;
        }
        menu.hidden = false;
        if (topLayer) menu.showPopover();
        trigger.setAttribute("aria-expanded", "true");
        position();
        const choices = items();
        choices[last ? choices.length - 1 : 0]?.focus();
    };
    trigger.addEventListener("click", () =>
        menu.hidden ? open() : close(true),
    );
    trigger.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            open(event.key === "ArrowUp");
        }
    });
    menu.addEventListener("keydown", (event) => {
        if (event.key === "Escape" || event.key === "Tab") {
            if (event.key === "Escape") event.preventDefault();
            event.stopPropagation();
            close(true);
        } else if (
            ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)
        ) {
            event.preventDefault();
            const choices = items(),
                index = choices.indexOf(doc.activeElement as HTMLButtonElement);
            const next =
                event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? choices.length - 1
                      : (index +
                            (event.key === "ArrowDown" ? 1 : -1) +
                            choices.length) %
                        choices.length;
            choices[next]?.focus();
        }
    });
    menu.addEventListener("click", (event) => {
        if ((event.target as Element).closest("button")) close(true);
    });
    const outside = (event: Event) => {
        if (
            !menu.contains(event.target as Node) &&
            !trigger.contains(event.target as Node)
        )
            close();
    };
    doc.addEventListener("mousedown", outside, true);
    win.addEventListener("resize", position);
    win.addEventListener("scroll", position, true);
    return {
        trigger,
        menu,
        close,
        destroy() {
            close();
            doc.removeEventListener("mousedown", outside, true);
            win.removeEventListener("resize", position);
            win.removeEventListener("scroll", position, true);
            menu.remove();
        },
    };
}
