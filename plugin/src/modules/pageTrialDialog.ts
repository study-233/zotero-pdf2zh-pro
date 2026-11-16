import { config } from "../../package.json";
import { getString } from "../utils/locale";

type TrialRange = { pages: string; count: number };
const openDialogs = new Set<() => void>();

export function closePageTrialDialogs() {
    for (const cancel of openDialogs) cancel();
}

export async function selectTrialPages(
    win: Window,
    title: string,
    total: number,
    initial: string,
    parse: (input: string, total: number) => TrialRange | undefined,
): Promise<string | undefined> {
    const url = `chrome://${config.addonRef}/content/pageTrial.xhtml`;
    const template = await (Zotero.File.getResourceAsync
        ? Zotero.File.getResourceAsync(url)
        : Zotero.File.getContentsFromURLAsync(url));
    const doc = win.document;
    if (win.closed || doc.getElementById("pdf2zh-page-trial")) return;
    const xml = new win.DOMParser().parseFromString(
        template,
        "application/xhtml+xml",
    );
    const dialog = doc.importNode(
        xml.documentElement,
        true,
    ) as HTMLDialogElement;
    const el = <T extends HTMLElement>(selector: string) =>
        dialog.querySelector<T>(selector)!;
    const input = el<HTMLInputElement>("input");
    const start = el<HTMLButtonElement>('[type="submit"]');
    const summary = el("#pdf2zh-trial-summary");
    const map = el(".map");
    const file = el("#pdf2zh-trial-file");
    file.textContent = file.title = title;
    el("h1").textContent = getString("trial-title");
    el("label").textContent = getString("trial-label");
    el(".total").textContent = getString("trial-total", { args: { total } });
    el(".cancel").textContent = getString("trial-cancel");
    start.textContent = getString("trial-start");
    input.placeholder = getString("trial-example", {
        args: {
            example: total >= 6 ? "2, 4–6" : total > 1 ? "1–" + total : "1",
        },
    });
    input.value = initial;
    if (total <= 48) {
        map.toggleAttribute("data-divisions", true);
        map.style.setProperty("--page-width", 100 / total + "%");
    }

    function update() {
        const result = parse(input.value, total);
        const invalid = Boolean(input.value.trim() && !result);
        start.disabled = !result;
        input.setAttribute("aria-invalid", String(invalid));
        summary.toggleAttribute("data-invalid", invalid);
        summary.textContent = result
            ? getString("trial-summary", {
                  args: {
                      pages: result.pages
                          .replace(/-/g, "–")
                          .replace(/,/g, ", "),
                      count: result.count,
                  },
              })
            : invalid
              ? getString("trial-invalid", { args: { total } })
              : getString("trial-hint");
        map.replaceChildren();
        for (const range of result?.pages.split(",") ?? []) {
            const [first, last = first] = range.split("-").map(Number);
            const segment = doc.createElementNS(
                "http://www.w3.org/1999/xhtml",
                "span",
            );
            segment.style.left = ((first - 1) / total) * 100 + "%";
            segment.style.width = ((last - first + 1) / total) * 100 + "%";
            map.append(segment);
        }
        return result;
    }

    function addPreset(label: string, value: string) {
        const button = doc.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "button",
        );
        button.setAttribute("type", "button");
        button.textContent = label;
        button.addEventListener("click", () => {
            input.value = value;
            update();
            input.focus();
            input.select();
        });
        el(".presets").append(button);
    }
    addPreset(
        initial
            ? getString("trial-this-page", { args: { page: initial } })
            : getString("trial-first"),
        initial || "1",
    );
    if (total > 1) {
        const count = Math.min(3, total);
        addPreset(
            getString("trial-first-pages", { args: { count } }),
            "1-" + count,
        );
    }
    update();
    return new Promise((resolve) => {
        const finish = (pages?: string) => {
            openDialogs.delete(cancel);
            win.removeEventListener("unload", cancel);
            if (dialog.open) dialog.close();
            dialog.remove();
            resolve(pages);
        };
        const cancel = () => finish();
        el("form").addEventListener("submit", (e) => {
            e.preventDefault();
            const range = update();
            if (range) finish(range.pages);
        });
        input.addEventListener("input", update);
        el(".cancel").addEventListener("click", cancel);
        dialog.addEventListener("cancel", (e) => {
            e.preventDefault();
            cancel();
        });
        doc.documentElement.append(dialog);
        try {
            dialog.showModal();
        } catch (error) {
            dialog.remove();
            throw error;
        }
        win.addEventListener("unload", cancel, { once: true });
        openDialogs.add(cancel);
        input.focus();
        input.select();
    });
}
