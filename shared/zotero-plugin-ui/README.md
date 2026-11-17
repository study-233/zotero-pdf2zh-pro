# Zotero 插件 UI 组件

复制同目录的 `components.js` 和 `components.css` 到目标插件的资源目录。组件使用原生 HTML 元素，可以挂进 Zotero 的 XHTML 窗口、设置页或 React 容器。双击 [离线示例](preview.html) 可操作三个组件、切换中英文和深浅色。

| 组件 | 适用场景 | 接入方式 |
| --- | --- | --- |
| `settingsSection(title, summary)` | ZotMeta 来源设置、Z-Search 搜索源设置 | 将字段加入 `content`；调用 `setSummary(text)` 更新收起时的摘要。容器是原生 `details`。 |
| `taskCard({ title, status, tone, progressLabel, metricsLabel })` | 元数据批次、索引进度 | `setStatus(text, tone)` 更新状态；`setMetrics([[标签, 值], …])` 更新可滚动的指标栏；按钮加入 `actions`。 |
| `readerPanel(title)` | 条目详情、文献预览 | 正文加入 `content`，来源写入 `source.textContent`；按钮加入 `headerActions` 或 `actions`。侧栏布局设置 `element.dataset.docked = "true"`。 |

三个组件均返回 `element` 供宿主挂载。状态色支持 `neutral`、`active`、`success`、`warning` 和 `error`；同时传入面向用户的状态文字。任务进度默认隐藏，需要时将 `progress.hidden` 设为 `false`，给 `progress.value` 传入 0～100 的百分数；移除 `value` 属性即可显示不确定进度。指标里的 `null` 或 `undefined` 显示为破折号，数值 0 保留。

## 原生 Zotero 窗口

以 ZotMeta 为例，将两个文件放进它的 `src/chrome/content/`。在目标 XHTML 文档引入样式；XUL 容器中使用已声明 HTML 命名空间的 `html:link`。

```xml
<html:link rel="stylesheet" href="chrome://zotmeta/content/components.css" />
```

在插件自己的脚本作用域加载工厂。传入挂载位置所属的文档，所有标签、表单及按钮回调由目标插件提供。

```js
const scope = {};
Services.scriptloader.loadSubScript("chrome://zotmeta/content/components.js", scope);
const ui = scope.createZoteroPluginUI(container.ownerDocument);
const card = ui.taskCard({
    title: labels.title, status: labels.ready, tone: "neutral",
    progressLabel: labels.progress, metricsLabel: labels.metrics,
});
card.actions.append(ui.button(labels.update, startMetadataUpdate, "primary"));
container.append(card.element);

// 在批次回调中更新现有卡片，保留焦点和指标滚动位置。
card.setMetrics([[labels.updated, updatedCount], [labels.skipped, skippedCount]]);
```

## React 插件

Z-Search 的包声明了 `"type": "module"`。复制时把 `components.js` 命名为 `components.cjs`，让打包器识别它的 CommonJS 导出。将样式表加载到组件所在的文档。下面的容器交给组件管理；React 卸载时一并移除。

```jsx
import { useEffect, useRef } from "react";
import createZoteroPluginUI from "./components.cjs";

function ReferencePreview({ title, text }) {
    const host = useRef(null);
    useEffect(() => {
        const container = host.current;
        const panel = createZoteroPluginUI(container.ownerDocument).readerPanel(title);
        panel.content.textContent = text;
        panel.element.dataset.docked = "true";
        container.append(panel.element);
        return () => panel.element.remove();
    }, [title, text]);
    return <div ref={host} />;
}
```

设置分组和阅读面板的内容区可以插入目标插件已有的控件。`zpu-fields` 布局支持两列标签和窄栏换行；`zpu-control` 用于原生输入框与选择框。样式优先使用 Zotero 主题变量，独立窗口可在根元素设置 `color-scheme: light dark` 或指定主题。阅读面板的定位、关闭后的焦点还原及业务状态由宿主管理，可参考 [示例交互](preview.js)。
