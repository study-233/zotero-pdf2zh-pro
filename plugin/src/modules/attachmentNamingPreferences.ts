import { config } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import {
    DEFAULT_TITLE_TEMPLATE,
    renderAttachmentTitle,
    validateTitleTemplate,
} from "./attachmentNaming";

export function registerAttachmentNamingPreferences(window: Window) {
    const element = (id: string) =>
        window.document.getElementById(
            `zotero-prefpane-${config.addonRef}-${id}`,
        )!;
    const input = element("attachmentTitleTemplate") as HTMLInputElement;
    const toggle = element("rename") as unknown as XUL.Checkbox;
    const reset = element("attachmentTitleReset") as HTMLButtonElement;
    const error = element("attachmentTitleError");
    input.value =
        getPref("attachmentTitleTemplate")?.toString() ||
        DEFAULT_TITLE_TEMPLATE;
    const render = () => {
        const valid = validateTitleTemplate(input.value);
        input.disabled = !toggle.checked;
        reset.disabled = !toggle.checked;
        input.setAttribute("aria-invalid", String(!valid));
        error.textContent = valid ? "" : getString("attachment-template-error");
        error.hidden = valid || !toggle.checked;
        for (const mode of ["mono", "dual"] as const) {
            element(`attachmentTitlePreview-${mode}`).textContent =
                !toggle.checked
                    ? `paper.zh-CN.${mode}.pdf`
                    : renderAttachmentTitle(
                          valid
                              ? input.value
                              : String(
                                    getPref("attachmentTitleTemplate") ||
                                        DEFAULT_TITLE_TEMPLATE,
                                ),
                          {
                              title: "Attention Is All You Need",
                              fullTitle: "Attention Is All You Need",
                              author: "Vaswani et al.",
                              year: "2017",
                              sourceLang: "en",
                              targetLang: "zh-CN",
                              service: "openai",
                              model: "gpt-4.1-mini",
                              type: getString(`attachment-type-${mode}`),
                          },
                      );
        }
    };
    input.addEventListener("input", () => {
        if (validateTitleTemplate(input.value)) {
            setPref(
                "attachmentTitleTemplate",
                input.value.trim() || DEFAULT_TITLE_TEMPLATE,
            );
        }
        render();
    });
    input.addEventListener("blur", () => {
        if (!input.value.trim()) input.value = DEFAULT_TITLE_TEMPLATE;
        render();
    });
    toggle.addEventListener("command", render);
    reset.addEventListener("click", () => {
        input.value = DEFAULT_TITLE_TEMPLATE;
        setPref("attachmentTitleTemplate", DEFAULT_TITLE_TEMPLATE);
        render();
    });
    render();
}
