import { config } from "../../package.json";
import { getLocaleID } from "../utils/locale";

let activeWindow: Window | undefined;
let pendingTarget: string | undefined;
let dispose: (() => void) | undefined;

function reveal(element: Element) {
    for (
        let parent = element.parentElement;
        parent;
        parent = parent.parentElement
    )
        if (parent.localName === "details") parent.setAttribute("open", "");
}

export function preparePreferenceTarget(target: string) {
    pendingTarget = target;
    const element = activeWindow?.document.getElementById(
        `zotero-prefpane-${config.addonRef}-${target}`,
    );
    if (element) {
        reveal(element);
        pendingTarget = undefined;
    }
}

export function stopPreferenceCards() {
    dispose?.();
    dispose = undefined;
    activeWindow = undefined;
}

export async function registerPreferenceCards(window: Window) {
    stopPreferenceCards();
    const document = window.document;
    const pane = document.querySelector(".pdf2zh-pref-pane");
    if (!pane) return;
    activeWindow = window;
    const node = (id: string) =>
        document.getElementById(`zotero-prefpane-${config.addonRef}-${id}`);
    if (pendingTarget) {
        const target = node(pendingTarget);
        if (target) {
            reveal(target);
            window.requestAnimationFrame(() =>
                target.scrollIntoView({ block: "center" }),
            );
        }
        pendingTarget = undefined;
    }
    const text = (id: string) => node(id)?.textContent?.trim() || "";
    const value = (id: string) =>
        (node(id) as HTMLInputElement | null)?.value || "";
    const checked = (id: string) =>
        Boolean((node(id) as HTMLInputElement | null)?.checked);
    const label = (id: string) =>
        pane
            .querySelector(
                `label[for="zotero-prefpane-${config.addonRef}-${id}"]`,
            )
            ?.textContent?.trim() || "";
    const menuLabel = (id: string) => {
        const menu = node(id) as unknown as XULMenuListElement &
            HTMLSelectElement;
        return (
            menu?.selectedItem?.getAttribute("label") ||
            menu?.selectedOptions?.[0]?.textContent?.trim() ||
            ""
        );
    };
    const localize = async (key: string, args?: Record<string, string>) =>
        (await document.l10n!.formatValue(getLocaleID(key), args)) || key;
    let disposed = false;
    let frame = 0;
    let revision = 0;
    const update = async () => {
        if (disposed || !pane.isConnected) return;
        const current = ++revision;
        const [unconfigured, automatic, noGlossary, advanced] =
            await Promise.all([
                localize("pref-card-unconfigured"),
                localize("pref-card-automatic"),
                localize("pref-card-no-glossary"),
                localize("pref-card-advanced-summary", {
                    qps: value("qps") || "—",
                    pool: value("poolSize") || "—",
                }),
            ]);
        if (disposed || current !== revision) return;
        const packs = Array.from(
            pane.querySelectorAll<HTMLInputElement>(
                ".glossary-pack-row input:checked",
            ),
        ).map((input) =>
            document
                .querySelector(`label[for="${(input as HTMLInputElement).id}"]`)
                ?.textContent?.trim(),
        );
        const summaries: Record<string, string> = {
            profiles: value("selectedApiKey")
                ? menuLabel("selectedApiKey")
                : unconfigured,
            languages: `${value("sourceLang") || automatic} → ${value("targetLang") || "—"}`,
            output: ["outputMono", "outputDual"]
                .filter(checked)
                .map(label)
                .join(" · "),
            selection: [
                menuLabel("selection-dictionary"),
                menuLabel("selection-provider"),
            ]
                .filter(Boolean)
                .join(" · "),
            glossary:
                [
                    ...packs,
                    text("glossarySummary").replace(/[。.]$/, ""),
                    checked("semanticReview") ? label("semanticReview") : "",
                ]
                    .filter(Boolean)
                    .join(" · ") || noGlossary,
            advanced,
            connection: [text("serverStatus"), value("new_serverip")]
                .filter(Boolean)
                .join(" · "),
        };
        for (const [key, summary] of Object.entries(summaries)) {
            const target = node(`card-${key}-note`);
            if (!target || target.textContent === summary) continue;
            target.textContent = summary;
            target.setAttribute("title", summary);
        }
    };
    const schedule = () => {
        if (frame || disposed) return;
        frame = window.requestAnimationFrame(() => {
            frame = 0;
            void update();
        });
    };
    // Observe the form bodies, so writing a header summary never triggers itself.
    const observer = new window.MutationObserver(schedule);
    for (const source of pane.querySelectorAll(".pref-section, .version-strip"))
        observer.observe(source, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeFilter: ["label", "value", "checked", "hidden"],
        });
    const events = ["input", "change", "command"];
    for (const event of events) pane.addEventListener(event, schedule);
    window.addEventListener("profiles-changed", schedule);
    const search = document.getElementById(
        "prefs-search",
    ) as HTMLInputElement | null;
    let searchState: Map<Element, boolean> | undefined;
    const revealSearch = () => {
        if (search?.value.trim()) {
            const details = Array.from(
                pane.querySelectorAll("details"),
            ) as Element[];
            const state =
                searchState ||
                new Map(
                    details.map((element) => [
                        element,
                        element.hasAttribute("open"),
                    ]),
                );
            searchState = state;
            for (const element of state.keys())
                element.setAttribute("open", "");
        } else if (searchState) {
            for (const [element, open] of searchState)
                if (element.isConnected) element.toggleAttribute("open", open);
            searchState = undefined;
        }
    };
    search?.addEventListener("command", revealSearch, true);
    revealSearch();
    const cleanup = () => {
        disposed = true;
        observer.disconnect();
        if (frame) window.cancelAnimationFrame(frame);
        for (const event of events) pane.removeEventListener(event, schedule);
        window.removeEventListener("profiles-changed", schedule);
        search?.removeEventListener("command", revealSearch, true);
        window.removeEventListener("unload", cleanup);
        if (activeWindow === window) activeWindow = undefined;
    };
    dispose = cleanup;
    window.addEventListener("unload", cleanup, { once: true });
    await update();
}
