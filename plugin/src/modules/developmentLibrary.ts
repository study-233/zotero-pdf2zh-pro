/** Local developer bootstrap. The production bundle has no configured runtime. */
let stopWatcher: (() => void) | undefined;

export function stopDevelopmentLibrary() {
    stopWatcher?.();
    stopWatcher = undefined;
}

export async function prepareDevelopmentLibrary(runtime: string) {
    const profile = Services.dirsvc.get("ProfD", Ci.nsIFile).path;
    const data = Zotero.DataDirectory.dir;
    const comparable = (path: string) =>
        Zotero.isWin ? path.replace(/\//g, "\\").toLowerCase() : path;
    if (
        comparable(profile) !==
            comparable(PathUtils.join(runtime, "profile")) ||
        comparable(data) !== comparable(PathUtils.join(runtime, "library"))
    ) {
        throw new Error("开发环境目录不匹配，已拒绝导入测试文献。");
    }
    stopDevelopmentLibrary();
    const win = Zotero.getMainWindow();
    const quitPath = PathUtils.join(runtime, "quit-request.json");
    let checking = false;
    const timer = win.setInterval(async () => {
        if (checking) return;
        checking = true;
        try {
            if (await IOUtils.exists(quitPath)) {
                await IOUtils.remove(quitPath);
                Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit!);
            }
        } catch {
            // A failed request is reported by the CLI timeout; never log profile contents.
        } finally {
            checking = false;
        }
    }, 500);
    stopWatcher = () => win.clearInterval(timer);
    const manifestPath = PathUtils.join(runtime, "samples", "manifest.json");
    let imported = 0;
    if (await IOUtils.exists(manifestPath)) {
        const manifest = JSON.parse(await IOUtils.readUTF8(manifestPath));
        const collections = Zotero.Collections.getByLibrary(
            Zotero.Libraries.userLibraryID,
        );
        let collection = collections.find((entry) => entry.name === "开发测试");
        if (!collection) {
            collection = new Zotero.Collection();
            collection.name = "开发测试";
            await collection.saveTx();
        }
        for (const paper of manifest.papers) {
            if (!["attention", "bert", "resnet"].includes(paper.id)) {
                throw new Error("未知测试样本，拒绝导入。");
            }
            const pdf = PathUtils.join(runtime, "samples", `${paper.id}.pdf`);
            if (
                (await IOUtils.computeHexDigest(pdf, "sha256")) !== paper.sha256
            ) {
                throw new Error("测试 PDF 校验失败，请重新准备样本。");
            }
            const marker = `pdf2zh-dev:${paper.arxiv}`;
            const items = collection.getChildItems();
            if (
                items.some((item) =>
                    item.getTags().some((tag) => tag.tag === marker),
                )
            )
                continue;
            const item = await Zotero.Attachments.importFromFile({
                file: pdf,
                libraryID: Zotero.Libraries.userLibraryID,
                collections: [collection.id],
            });
            item.setField("title", paper.title);
            item.addTag(marker);
            await item.saveTx();
            imported++;
        }
    }
    await IOUtils.writeUTF8(
        PathUtils.join(runtime, "plugin-ready.json"),
        JSON.stringify({
            profile,
            data,
            imported,
            mode: "isolated-development",
            loadedAt: new Date().toISOString(),
        }),
    );
}
