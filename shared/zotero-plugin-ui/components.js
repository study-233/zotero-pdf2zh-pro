// Pass the destination document, including when mounting inside a reader frame.
function createZoteroPluginUI(doc) {
    function node(tag, className, text) {
        const element = doc.createElementNS("http://www.w3.org/1999/xhtml", tag);
        element.className = className;
        if (text != null) element.textContent = text;
        return element;
    }

    function button(label, onClick, variant = "default") {
        const element = node("button", "zpu-button", label);
        element.type = "button";
        element.dataset.variant = variant;
        element.addEventListener("click", onClick);
        return element;
    }

    function settingsSection(title, summary = "") {
        const element = node("details", "zpu zpu-settings");
        const header = node("summary", "zpu-settings-header");
        const note = node("span", "zpu-note");
        const content = node("div", "zpu-settings-body");
        header.append(node("span", "zpu-title", title), note);
        element.append(header, content);
        function setSummary(text) {
            note.textContent = text;
            note.title = text;
        }
        setSummary(summary);
        return { element, content, setSummary };
    }

    function taskCard({ title, status, tone = "neutral", progressLabel, metricsLabel }) {
        const element = node("article", "zpu zpu-task");
        const header = node("header", "zpu-task-header");
        const heading = node("h3", "zpu-title", title);
        heading.title = title;
        const badge = node("span", "zpu-badge");
        const progress = node("progress", "zpu-progress");
        progress.max = 100;
        progress.hidden = true;
        progress.setAttribute("aria-label", progressLabel);
        const footer = node("footer", "zpu-footer");
        const metrics = node("dl", "zpu-metrics");
        metrics.tabIndex = 0;
        metrics.hidden = true;
        metrics.setAttribute("aria-label", metricsLabel);
        const actions = node("div", "zpu-actions");
        header.append(heading, badge);
        footer.append(metrics, actions);
        element.append(header, progress, footer);
        function setStatus(text, state = "neutral") {
            badge.textContent = text;
            badge.dataset.tone = state;
        }
        function setMetrics(entries) {
            const fragment = doc.createDocumentFragment();
            for (const [label, value] of entries) {
                const pair = node("div", "zpu-metric");
                pair.append(node("dt", "", label), node("dd", "", value ?? "—"));
                fragment.append(pair);
            }
            metrics.replaceChildren(fragment);
            metrics.hidden = !metrics.childElementCount;
        }
        setStatus(status, tone);
        return { element, actions, progress, setStatus, setMetrics };
    }

    function readerPanel(title) {
        const element = node("section", "zpu zpu-reader");
        element.setAttribute("aria-label", title);
        const header = node("header", "zpu-reader-header");
        const headerActions = node("div", "zpu-actions");
        header.append(node("h3", "zpu-reader-title", title), headerActions);
        const content = node("div", "zpu-reader-body");
        const footer = node("footer", "zpu-footer");
        const source = node("span", "zpu-note");
        const actions = node("div", "zpu-actions");
        footer.append(source, actions);
        element.append(header, content, footer);
        return { element, headerActions, content, source, actions };
    }

    return { settingsSection, taskCard, readerPanel, button };
}

// Bundlers can import this file; classic scripts and loadSubScript can use the factory directly.
if (typeof module !== "undefined" && module.exports) {
    module.exports = createZoteroPluginUI;
}
