import { getString } from "../utils/locale";
import { getPref } from "../utils/prefs";
import { diagnosticFetch } from "./diagnostics";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";

let active: { cancel: () => void; close: () => void } | undefined;

export type DocumentExportFormat = "md" | "json" | "txt";

export function cancelDocumentExport() {
    active?.cancel();
    active?.close();
}

export async function exportSelectedDocument(format: DocumentExportFormat) {
    if (active) return;
    const parent = Zotero.getMainWindow();
    const formatName = { md: "Markdown", json: "JSON", txt: "TXT" }[format];
    const args = { format: formatName };
    const title = getString("export-format-title", { args });
    const items = ztoolkit.getGlobal("ZoteroPane").getSelectedItems();
    if (items.length !== 1) {
        Zotero.alert(parent, title, getString("export-select-one"));
        return;
    }
    const server = String(getPref("new_serverip") || "").replace(/\/+$/, "");
    const requestId = Zotero.Utilities.randomString(32);
    const controller = new AbortController();
    let submitted = false;
    let finished = false;
    let writing = false;
    let dialog: InstanceType<typeof ztoolkit.Dialog> | undefined;
    let errorKey = "export-failed";
    const headers = { "X-PDF2ZH-Client": "zotero" };
    const cancel = () => {
        if (finished || writing || controller.signal.aborted) return;
        controller.abort();
        if (submitted)
            void diagnosticFetch(
                `${server}/document-exports/${requestId}/cancel`,
                { method: "POST", headers },
            ).catch(() => {});
    };
    active = { cancel, close: () => dialog?.window?.close() };
    try {
        errorKey = "export-invalid-pdf";
        const fileData = await PDF2zhHelperFactory.prepareFileData(
            items[0],
            true,
        );
        if (controller.signal.aborted) return;
        const path = await new ztoolkit.FilePicker(
            title,
            "save",
            [[formatName, `*.${format}`]],
            fileData.fileName.replace(/\.pdf$/i, "") + `.${format}`,
        ).open();
        if (!path || controller.signal.aborted) return;
        dialog = new ztoolkit.Dialog(1, 1)
            .addCell(0, 0, {
                tag: "description",
                namespace: "xul",
                attributes: { role: "status", "aria-live": "polite" },
                properties: {
                    textContent: getString("export-working", { args }),
                },
                styles: { maxWidth: "32em", margin: "16px" },
            })
            .addButton(getString("export-cancel"), "cancel", {
                callback: cancel,
            })
            .setDialogData({ unloadCallback: cancel })
            .open(title, {
                centerscreen: true,
                resizable: true,
                fitContent: true,
            });
        errorKey = "export-server-unavailable";
        const health = await diagnosticFetch(`${server}/health`, {
            signal: controller.signal,
        });
        if (!health.ok) throw new Error("health");
        const healthData = (await health.json()) as {
            capabilities?: { structuredExport?: boolean };
        };
        if (!healthData.capabilities?.structuredExport) {
            errorKey = "export-upgrade-server";
            throw new Error("capability");
        }
        const body = new FormData();
        body.append("file", fileData.file!, fileData.fileName);
        body.append("format", format);
        submitted = true;
        const response = await diagnosticFetch(
            `${server}/document-exports`,
            {
                method: "POST",
                headers: { ...headers, "X-PDF2ZH-Request-ID": requestId },
                body,
                signal: controller.signal,
            },
            { totalMs: 360000, idleMs: 330000, headersMs: 330000 },
        );
        errorKey = "export-failed";
        if (!response.ok) {
            const { code } = (await response.json()) as unknown as {
                code: string;
            };
            const notices: Record<string, string> = {
                export_needs_ocr: "export-needs-ocr",
                export_invalid_pdf: "export-invalid-pdf",
                export_busy: "export-busy",
                export_timeout: "export-timeout",
                export_too_large: "export-too-large",
            };
            errorKey = notices[code] || errorKey;
            throw new Error("export");
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (controller.signal.aborted) return;
        writing = true;
        errorKey = "export-save-failed";
        // A unique sibling avoids touching another operation's temporary file.
        const tempPath = `${path}.${requestId}.tmp`;
        try {
            await IOUtils.write(path, bytes, { tmpPath: tempPath });
        } finally {
            await IOUtils.remove(tempPath, { ignoreAbsent: true }).catch(
                () => {},
            );
        }
        finished = true;
        dialog.window?.close();
        Zotero.alert(parent, title, getString("export-saved", { args }));
    } catch {
        if (!controller.signal.aborted) {
            cancel();
            finished = true;
            dialog?.window?.close();
            Zotero.alert(parent, title, getString(errorKey));
        }
    } finally {
        finished = true;
        dialog?.window?.close();
        active = undefined;
    }
}
