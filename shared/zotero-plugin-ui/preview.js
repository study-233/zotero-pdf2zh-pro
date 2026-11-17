// Demo data and interactions. Target plugins supply their own localized strings and callbacks.
(() => {
    const en = new URLSearchParams(location.search).get("lang") === "en";
    const t = (zh, english) => en ? english : zh;
    const byId = (id) => document.getElementById(id);
    document.documentElement.lang = en ? "en" : "zh-CN";
    byId("language").textContent = en ? "简体中文" : "English";
    byId("language").href = en ? "?lang=zh" : "?lang=en";
    byId("theme").textContent = t("切换外观", "Switch theme");
    let dark = matchMedia("(prefers-color-scheme: dark)").matches;
    byId("theme").onclick = () => {
        dark = !dark;
        document.documentElement.style.colorScheme = dark ? "dark" : "light";
    };

    const ui = createZoteroPluginUI(document);
    byId("settings-label").textContent = t("设置分组", "Settings section");
    const settings = ui.settingsSection(t("元数据更新", "Metadata updates"));
    settings.element.open = true;
    const fields = document.createElement("div");
    fields.className = "zpu-fields";
    function field(label, control) {
        const wrapper = document.createElement("label");
        control.className = "zpu-control";
        wrapper.append(label, control);
        fields.append(wrapper);
    }
    const source = document.createElement("select");
    for (const name of ["Crossref", "PubMed"]) source.add(new Option(name, name));
    const concurrency = document.createElement("input");
    Object.assign(concurrency, { type: "number", min: "1", max: "8", value: "3" });
    field(t("优先来源", "Preferred source"), source);
    field(t("并发数", "Concurrency"), concurrency);
    function updateSummary() {
        settings.setSummary([source.value, t("并发 ", "Concurrency ") + (concurrency.value || "—")].join(" · "));
    }
    fields.addEventListener("input", updateSummary);
    settings.content.append(fields);
    updateSummary();
    byId("settings-host").append(settings.element);

    byId("task-label").textContent = t("任务卡", "Task card");
    const task = ui.taskCard({
        title: t("更新所选文献的元数据", "Update metadata for selected items"),
        status: t("进行中", "In progress"), tone: "active",
        progressLabel: t("批次进度", "Batch progress"), metricsLabel: t("批次指标", "Batch metrics"),
    });
    task.progress.hidden = false;
    task.progress.value = 75;
    const done = t("已完成", "Completed");
    function metrics(completed, elapsed) {
        task.setMetrics([[done, completed], [t("跳过", "Skipped"), 0], [t("耗时", "Elapsed"), elapsed]]);
    }
    metrics("3 / 4", null);
    const finish = ui.button(t("完成示例批次", "Finish sample batch"), () => {
        task.progress.value = 100;
        task.setStatus(done, "success");
        metrics("4 / 4", "2.4 s");
        finish.disabled = true;
    }, "primary");
    task.actions.append(finish);
    byId("task-host").append(task.element);

    byId("reader-label").textContent = t("阅读面板", "Reader panel");
    const reader = ui.readerPanel(t("文献预览", "Reference preview"));
    const title = document.createElement("strong");
    title.textContent = t("元数据与文献检索", "Metadata and literature search");
    const description = document.createElement("p");
    description.textContent = t("根据标题、作者和年份核对匹配条目。", "Compare the title, authors and year with the matched record.");
    reader.content.append(title, description);
    reader.source.textContent = t("来源 · 示例文库", "Source · Sample library");
    const dock = ui.button(t("停靠", "Dock"), () => {
        const docked = reader.element.dataset.docked !== "true";
        reader.element.dataset.docked = String(docked);
        dock.setAttribute("aria-pressed", String(docked));
    });
    dock.setAttribute("aria-pressed", "false");
    const open = byId("open-reader");
    open.textContent = t("打开预览", "Open preview");
    function close() {
        reader.element.hidden = true;
        open.hidden = false;
        open.focus();
    }
    reader.headerActions.append(ui.button(t("关闭", "Close"), close));
    reader.element.addEventListener("keydown", (event) => {
        if (event.key === "Escape") close();
    });
    reader.actions.append(dock);
    byId("reader-host").append(reader.element);
    open.onclick = () => {
        open.hidden = true;
        reader.element.hidden = false;
        reader.headerActions.firstElementChild.focus();
    };
})();
