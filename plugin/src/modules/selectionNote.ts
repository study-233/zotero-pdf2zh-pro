import { getString } from "../utils/locale";

export type SelectionExcerpt = {
    original: string;
    query: string;
    translation: string;
    position: { pageIndex: number; rects?: number[][] };
    pageLabel?: string;
};

function excerptHTML(
    doc: Document,
    attachment: Zotero.Item,
    excerpt: SelectionExcerpt,
) {
    const page = excerpt.position.pageIndex + 1;
    const pageLabel = excerpt.pageLabel || String(page);
    const fragment = doc.createElement("div");
    const quote = doc.createElement("p");
    const highlight = doc.createElement("span");
    highlight.className = "highlight";
    highlight.setAttribute(
        "data-annotation",
        encodeURIComponent(
            JSON.stringify({
                attachmentURI: Zotero.URI.getItemURI(attachment),
                color: "#ffd400",
                position: excerpt.position,
                pageLabel,
            }),
        ),
    );
    function appendText(node: HTMLElement, value: string) {
        value.split(/\r\n|[\n\r]/).forEach((line, i) => {
            if (i) node.append(doc.createElement("br"));
            node.append(line);
        });
    }
    // The native editor unwraps inline highlights that lack quotation marks.
    highlight.append(Zotero.getString("punctuation.openingQMark"));
    appendText(highlight, excerpt.original);
    highlight.append(Zotero.getString("punctuation.closingQMark"));
    const link = doc.createElement("a");
    const prefix =
        attachment.libraryID === Zotero.Libraries.userLibraryID
            ? "library"
            : `groups/${Zotero.Groups.getGroupIDFromLibraryID(attachment.libraryID)}`;
    link.setAttribute(
        "href",
        `zotero://open-pdf/${prefix}/items/${attachment.key}?page=${page}`,
    );
    link.textContent = getString("selection-note-page", {
        args: { page: pageLabel },
    });
    quote.append(highlight, " ", link);
    fragment.append(quote);
    if (excerpt.query !== excerpt.original) {
        const edited = doc.createElement("p");
        appendText(
            edited,
            getString("selection-note-edited") + " " + excerpt.query,
        );
        fragment.append(edited);
    }
    const translation = doc.createElement("p");
    appendText(translation, excerpt.translation);
    fragment.append(translation);
    return fragment;
}

type ReaderWindow = {
    Zotero_Tabs?: { selectedID?: string };
    ZoteroContextPane?: {
        collapsed?: boolean;
        activeEditor?: {
            item?: Zotero.Item;
            mode?: string;
            getCurrentInstance(): Zotero.EditorInstance | undefined;
        };
    };
};

async function insertIntoEditor(editor: Zotero.EditorInstance, html: string) {
    await editor._initPromise;
    if (
        editor._disableSaving ||
        editor._readOnly ||
        editor._isReadOnly() ||
        editor._item.deleted
    )
        throw new Error("Note is read-only");
    await editor._ensureNoteCreated();
    // Same editor command as Zotero.EditorInstance.insertAnnotations, at the cursor.
    editor._postMessage({ action: "insertHTML", pos: null, html });
}

export async function saveSelectionNote(
    reader: _ZoteroTypes.ReaderInstance,
    excerpt: SelectionExcerpt,
) {
    const attachment = reader.itemID && Zotero.Items.get(reader.itemID);
    if (
        !attachment ||
        !attachment.isPDFAttachment() ||
        attachment.deleted ||
        attachment.parentItem?.deleted ||
        !Number.isSafeInteger(excerpt.position.pageIndex) ||
        excerpt.position.pageIndex < 0
    )
        throw new Error("PDF location unavailable");
    const doc =
        Zotero.getMainWindow().document.implementation.createHTMLDocument("");
    const fragment = excerptHTML(doc, attachment, excerpt);
    const win = (reader._window ??
        Zotero.getMainWindow()) as unknown as ReaderWindow;
    const pane = win.ZoteroContextPane;
    const active =
        !pane?.collapsed &&
        (!reader.tabID || reader.tabID === win.Zotero_Tabs?.selectedID)
            ? pane?.activeEditor
            : undefined;
    if (
        active?.mode === "edit" &&
        active.item?.libraryID === attachment.libraryID
    ) {
        const editor = active.getCurrentInstance();
        if (!editor) throw new Error("Note editor unavailable");
        return insertIntoEditor(editor, fragment.innerHTML);
    }
    const library = Zotero.Libraries.get(attachment.libraryID);
    if (!library || !library.editable) throw new Error("Library is read-only");

    const parent = attachment.parentItem;
    // A Zotero tag survives renaming, sync and restarts, also for standalone PDFs.
    const tag = "pdf2zhpro:excerpts:" + (parent || attachment).key;
    let openEditor: Zotero.EditorInstance | undefined;
    await Zotero.DB.executeTransaction(async () => {
        let ids: number[];
        if (parent) {
            ids = parent.getNotes();
        } else {
            const search = new Zotero.Search({
                libraryID: attachment.libraryID,
            });
            search.addCondition("tag", "is", tag);
            ids = await search.search();
        }
        const notes = await Zotero.Items.getAsync(ids);
        let note = notes.find(
            (item) =>
                item.isNote() &&
                !item.deleted &&
                item.hasTag(tag) &&
                (item.parentID || false) === (parent?.id || false),
        );
        let html: string;
        if (note) {
            openEditor = Zotero.Notes._editorInstances.find(
                (editor) =>
                    editor._item?.id === note!.id &&
                    !editor._disableSaving &&
                    !editor._readOnly,
            );
            if (openEditor) return;
            // Zotero stores the schema and citation data on the outer div. Keep
            // its original markup verbatim, including custom protocols and images.
            const existing = note.getNote().trimEnd();
            html =
                /^\s*<div\b[^>]*\bdata-schema-version=/i.test(existing) &&
                /<\/div>$/i.test(existing)
                    ? existing.slice(0, -6) +
                      fragment.innerHTML +
                      existing.slice(-6)
                    : existing + fragment.innerHTML;
        } else {
            note = new Zotero.Item("note");
            note.libraryID = attachment.libraryID;
            if (parent) note.parentID = parent.id;
            note.addTag(tag);
            fragment.setAttribute("data-schema-version", "9");
            const heading = doc.createElement("h1");
            heading.textContent = getString("selection-note-title");
            fragment.prepend(heading);
            html = fragment.outerHTML;
        }
        note.setNote(html);
        await note.save();
    });
    if (openEditor) await insertIntoEditor(openEditor, fragment.innerHTML);
}
