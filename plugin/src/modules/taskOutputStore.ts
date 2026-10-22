// Local import receipts live in the same database transaction as their attachment.
// They contain only a digest and Zotero identifiers, and do not modify synced metadata.
async function ensureStore(): Promise<void> {
    await Zotero.DB.queryAsync(`CREATE TABLE IF NOT EXISTS pdf2zhOutputImports (
        outputKey TEXT PRIMARY KEY, libraryID INTEGER NOT NULL, itemKey TEXT NOT NULL
    )`);
}

async function receipt(outputKey: string) {
    return Zotero.DB.rowQueryAsync(
        "SELECT libraryID, itemKey FROM pdf2zhOutputImports WHERE outputKey = ?",
        [outputKey],
    ) as Promise<{ libraryID: number; itemKey: string } | false>;
}

export async function findTaskOutput(
    outputKey: string,
): Promise<Zotero.Item | undefined> {
    await ensureStore();
    return findSavedOutput(outputKey);
}

async function findSavedOutput(
    outputKey: string,
): Promise<Zotero.Item | undefined> {
    const row = await receipt(outputKey);
    if (!row) return undefined;
    const attachment = await Zotero.Items.getByLibraryAndKeyAsync(
        row.libraryID,
        row.itemKey,
    );
    if (!attachment || attachment.deleted)
        throw new Error(
            "此结果已导入，但附件已删除；请先在 Zotero 中恢复该附件。",
        );
    const path = await attachment.getFilePathAsync();
    if (!path || !(await IOUtils.exists(path)))
        throw new Error(
            "此结果已导入，但附件文件不可用；请先恢复或同步附件文件。",
        );
    return attachment;
}

export async function importTaskOutput(options: {
    outputKey: string;
    file: string;
    libraryID: number;
    parentItemID?: number;
    collections?: number[];
    title: string;
    tags?: string[];
    isCurrent: () => boolean;
}): Promise<Zotero.Item | undefined> {
    await ensureStore();
    let directory: string | undefined;
    let created = false;
    let attachment: Zotero.Item | undefined;
    try {
        attachment = await Zotero.DB.executeTransaction(async () => {
            if (!options.isCurrent()) return undefined;
            const existing = await findSavedOutput(options.outputKey);
            if (existing) return existing;
            // importFromFile starts its own transaction; nesting it would deadlock.
            // Use the same attachment/file APIs, adding the receipt before commit.
            const item = new Zotero.Item("attachment");
            item.libraryID = options.libraryID;
            if (options.parentItemID) item.parentID = options.parentItemID;
            else if (options.collections)
                item.setCollections(options.collections);
            item.setField("title", options.title);
            for (const tag of options.tags || []) item.addTag(tag);
            item.attachmentLinkMode =
                Zotero.Attachments.LINK_MODE_IMPORTED_FILE;
            item.attachmentContentType = "application/pdf";
            await item.save();
            directory = await Zotero.Attachments.createDirectoryForItem(item);
            const file = await Zotero.File.copyToUnique(
                options.file,
                PathUtils.join(
                    directory,
                    Zotero.File.getValidFileName(
                        PathUtils.filename(options.file),
                    ),
                ),
            );
            await Zotero.File.setNormalFilePermissions(file.path);
            item.attachmentPath = file.path;
            if (!options.isCurrent())
                throw new DOMException("任务已变更，停止导入。", "AbortError");
            await item.save();
            await Zotero.DB.queryAsync(
                "INSERT INTO pdf2zhOutputImports (outputKey, libraryID, itemKey) VALUES (?, ?, ?)",
                [options.outputKey, item.libraryID, item.key],
            );
            created = true;
            return item;
        });
    } catch (error) {
        // Never remove a committed attachment, even if a post-commit callback failed.
        if (directory) {
            try {
                if (!(await receipt(options.outputKey)))
                    await IOUtils.remove(directory, {
                        recursive: true,
                        ignoreAbsent: true,
                    });
            } catch (cleanupError) {
                ztoolkit.log("无法清理未提交的附件目录:", cleanupError);
            }
        }
        throw error;
    }
    if (created && attachment) {
        try {
            await Zotero.Fulltext.indexItems([attachment.id]);
        } catch (error) {
            ztoolkit.log("译文附件已导入，全文索引失败:", error);
        }
    }
    return attachment;
}
