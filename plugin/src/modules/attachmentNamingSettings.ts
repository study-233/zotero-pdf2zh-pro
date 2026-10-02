import { getPref, setPref } from "../utils/prefs";
import { DEFAULT_TITLE_TEMPLATE } from "./attachmentNaming";
import { defaultTitleLayout, parseTitleLayout } from "./attachmentTitleLayout";

// Only repair known encodings of the shipped separator, never arbitrary text.
const brokenSeparators = [" \uFFFD ", " \u00C2\u00B7 "];

export function repairAttachmentNamingPreferences() {
    const template = getPref("attachmentTitleTemplate");
    if (
        brokenSeparators.some(
            (separator) => template === `{title}${separator}{type}`,
        )
    )
        setPref("attachmentTitleTemplate", DEFAULT_TITLE_TEMPLATE);

    const layout = parseTitleLayout(getPref("attachmentTitleLayout"));
    if (!layout) return;
    if (brokenSeparators.includes(layout.separator)) {
        layout.separator = defaultTitleLayout().separator;
        setPref("attachmentTitleLayout", JSON.stringify(layout));
    } else if (
        layout.separator === "" &&
        layout.blocks.length === 3 &&
        layout.blocks[0].kind === "field" &&
        layout.blocks[0].field === "title" &&
        layout.blocks[1].kind === "text" &&
        brokenSeparators.includes(layout.blocks[1].text) &&
        layout.blocks[2].kind === "field" &&
        layout.blocks[2].field === "type"
    ) {
        // The previous template migration could store the broken separator as text.
        setPref("attachmentTitleLayout", JSON.stringify(defaultTitleLayout()));
    }
}
