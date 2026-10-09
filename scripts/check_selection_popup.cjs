// Optional: node scripts/check_selection_popup.cjs [path-to-playwright]
// Runs real selection modules in nested browser documents with mocked Zotero APIs.
// It does not operate Zotero or use a real model.
const fs = require("fs");
const path = require("path");
const assert = require("node:assert/strict");
const ts = require("../plugin/node_modules/typescript");
const { chromium } = require(process.argv[2] || "playwright");
const root = path.resolve(__dirname, "..");
const output = path.join(root, ".local-dev/selection-ui");
fs.mkdirSync(output, { recursive: true });
const modules = {};
for (const name of ["selectionFormatting", "selectionTranslate", "selectionDictionary", "selectionDictionaryStore", "selectionPopup", "selectionUI", "selectionAudio", "selectionOnlineDictionary", "selectionDictionaryService", "selectionStream", "selectionEvents", "selectionRequest", "selectionView", "selectionPane"]) {
    modules[`./${name}`] = ts.transpileModule(fs.readFileSync(path.join(root, `plugin/src/modules/${name}.ts`), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
}
const localeStrings = Object.fromEntries([...fs.readFileSync(path.join(root,"plugin/addon/locale/zh-CN/addon.ftl"),"utf8").matchAll(/^([\w-]+) = (.+)$/gm)].map(m=>[m[1],m[2].trim()]));
const dictionary = fs.readFileSync(path.join(root, "plugin/addon/content/dictionaries/ecdict.json"), "utf8");
(async () => {
    const browser = await chromium.launch({ headless: true, channel: "chrome" });
    try {
        const page = await browser.newPage({ viewport: { width: 920, height: 740 } });
        page.setDefaultTimeout(10000);
        // Reader icon controls must render with all image/resource loads blocked.
        await page.route("**/*", route => route.abort());
        const errors = [];
        page.on("pageerror", e => errors.push(e.message));
        await page.setContent('<html><body style="margin:0;background:#dce0e5"><button id="outside">Zotero window toolbar</button><iframe id="reader" style="border:0;width:100%;height:680px"></iframe></body></html>');
        await page.evaluate(({ modules, dictionary, localeStrings }) => {
            const frame = document.querySelector("#reader");
            const doc = frame.contentDocument;
            doc.open();
            doc.write('<html><head><style>body{font:16px/1.7 system-ui;margin:40px;background:#f3f4f6;color:#222} #native{position:absolute;left:400px;top:110px} iframe{width:250px;height:100px;border:1px solid #888} </style></head><body><button class="toolbar-button pageDown" id="next">Next page</button><h2>Automatic selection translation</h2><p>U-Cond: Unconditional generation without external constraints.</p><div id="native" class="selection-popup"><button>原生高亮</button><div class="custom-sections"><div class="section other-plugin">Other plugin</div></div></div><iframe id="pdf" srcdoc="<p>PDF text layer</p>"></iframe></body></html>');
            doc.close();
            window.rdoc = doc;
            window.calls = [];
            window.logs = [];
            window.pending = false;
            window.memory = true;
            window.matchType = "exact";
            window.imported = false;
            window.copied = "";
            window.preferences = {selectionDictionaryFallback:"youdao"};
            const cache = {
                "../../package.json": { config: { addonID: "test", addonRef: "pdf2zhpro" } },
                "./pdf2zhHelper": { PDF2zhHelperFactory: { getServerConfig: () => ({ serverUrl: "http://localhost:8890", sourceLang: "en", targetLang: "zh-CN", apiConfig: {model:"test"} }) } },
                "../utils/prefs": {getPref: name => preferences[name] ?? (name === "selectionAutoDictionary" ? false : name === "selectionDictionary" ? imported ? "collins" : "ecdict" : "bing"), setPref: (name,value) => { preferences[name] = value; }},
                "./profileStore": {loadProfiles:()=>[]},
                "./llmApiManager": {profileLabel:p=>p.key},
                "./selectionFavorites": {listFavorites:async()=>[],favoriteLibrary:()=>({type:"user"}),favoriteIdentity:()=>""},
                "./selectionFavoritesPane": {registerFavoritesPane(){},unregisterFavoritesPane(){}},
                "../utils/locale": {getLocaleID: id => id, getString: key => localeStrings[key] || key},
                "./glossaryStore": { loadGlossaryEntries: () => [] },
                "./selectionContext": { getSelectionContext: async () => "page context" },
                "./diagnostics": { recordDiagnostic: (...args) => logs.push(args) },
            };
            window.fetch = async (url, options) => {
                const body = JSON.parse(options.body);
                if (url.endsWith("selection-capabilities")) return {ok:true,status:200,json:async()=>({selectionLearning:true})};
                if (body.allowGenerate === false) return {ok:true,status:200,json:async()=>({status:"miss"})};
                calls.push({url,body});
                if (pending) return new Promise((resolve,reject) => {
                    window.finish = () => resolve({ok:true,status:200,json:async()=>({matched:true,matchType:"exact",translation:"late",provider:"translation-memory"})});
                    options.signal.addEventListener("abort",()=>reject(new Error("cancelled")));
                });
                if (body.mode === "dictionary") return {ok:true,status:200,json:async()=>({translation:"一般含义",entry:{headword:body.text,senses:[{chinese:"一般含义",pos:"n.",examples:[{english:"An example.",chinese:"一个例子。"}]}],usage:"补充说明"},model:"test-model",provider:"personal-dictionary",saved:true})};
                if (body.mode === "context") return {ok:true,status:200,json:async()=>({translation:"这里表示当前段落的具体含义。",contextMeaning:{pos:"n.",meaning:"当前段落的具体含义",explanation:"这里表示当前段落中所指的概念。"},model:"test-model",saved:true})};
                return {ok:true,status:200,json:async()=>url.endsWith("translation-lookup") ? memory ? {matched:true,matchType:window.matchType,translation:"布局生成分为类别、尺寸和位置。",source:"source"} : {matched:false} : {translation:"所选句子的中文译文",provider:"bing",cached:false}};
            };
            window.Zotero = {
                DataDirectory: {dir:"/mock-data"},
                HTTP: {request:async()=>({responseText:JSON.stringify({input:"unknown",suggest:{}})})},
                getMainWindow: () => window,
                File: { getResourceAsync: async () => dictionary, getContentsAsync: async () => ({responseText:dictionary}) },
                Items: {get:()=>({getFilePathAsync:async()=>"paper.pdf"})},
                Reader: {_readers:[],registerEventListener:(_type,fn)=>{window.onSelection=fn},unregisterEventListener(){}},
                Notifier: {registerObserver:observer=>{window.notify=observer.notify;return "selection"},unregisterObserver(){}},
                Utilities: {Internal:{copyTextToClipboard(text){window.copied=text}}},
            };
            const details = document.createElement("item-details");
            details.tabID = "tab1";
            details.style.cssText = "position:fixed;right:0;top:24px;width:290px;background:white;z-index:10";
            const paneBody = document.createElement("div"); details.append(paneBody); document.body.append(details);
            window.paneBody = paneBody;
            window.ZoteroContextPane = {collapsed:true,context:{mode:"notes"}};
            let paneOptions;
            details.render = async () => paneOptions?.onRender({body:paneBody,tabType:"reader",paneID:"selection-translation",setEnabled(){}});
            details.scrollToPane = async () => {};
            Zotero.ItemPaneManager = {registerSection(options){paneOptions=options;return "selection-translation"},unregisterSection(){paneBody.replaceChildren()}};
            Zotero.Reader.getByTabID = id => id === window.reader?.tabID ? window.reader : undefined;
            window.PathUtils={join:(...parts)=>parts.join("/")};
            window.IOUtils = {exists:async()=>true,readUTF8:async()=>JSON.stringify({format:1,info:{entries:1,importedAt:"2026-09-30",sha256:"test",skipped:0,aiEntries:0},entries:{Unconditional:{headword:"Unconditional",phonetic:"ʌnkənˈdɪʃənəl",senses:Array.from({length:4},(_,i)=>({pos:"adj.",chinese:"无条件的 " + i,english:"without conditions "+i,examples:[{english:"Example "+i,chinese:"例句 "+i}]}))}}}),stat:async()=>({size:1,lastModified:1}),computeHexDigest:async()=>"sha256"};
            const require = name => {
                if (!cache[name]) {const exports={};new Function("require","exports",modules[name])(require,exports);cache[name]=exports;}
                return cache[name];
            };
            window.previewFactory = require("./selectionPopup").createSelectionPopup;
            window.dictionaryAPI = require("./selectionOnlineDictionary");
            window.selection = require("./selectionTranslate");
            selection.registerSelectionTranslation();
            window.reader = {type:"pdf",itemID:1,tabID:"tab1",_window:window,_iframeWindow:frame.contentWindow};
            window.trigger = text => {

                onSelection({reader,doc,params:{annotation:{text,position:{pageIndex:4,rects:[1,2,3,4]}}},append:node=>{const section=doc.createElement("div");section.className="section";section.append(node);doc.querySelector(".custom-sections").append(section)}});
            };
        }, {modules,dictionary,localeStrings});
        const reader = page.frameLocator("#reader");
        const card = reader.locator(".pdf2zh-selection-card");
        const select = async text => {console.log("Selection:",text);await page.evaluate(text=>trigger(text),text);await card.waitFor();};
        const expectText = async text => {await page.waitForFunction(text=>rdoc.querySelector(".st-body")?.textContent.includes(text),text);};
        await select("distributions"); await expectText("分布");
        assert.equal(await page.evaluate(()=>calls.length),0);
        await expectText("distributions → distribution");
        const iconState = await card.locator(".st-icon-button:visible").evaluateAll(buttons => buttons.map(button => {
            const svg = button.querySelector("svg");
            return {label:button.getAttribute("aria-label"), visible:!!svg && svg.getBBox().width>0 && svg.getBBox().height>0 && getComputedStyle(svg).stroke!=="none", external:!!button.querySelector("[href], [src], image, use")};
        }));
        assert.ok(iconState.length >= 5);
        assert.ok(iconState.every(icon=>icon.label && icon.visible && !icon.external),JSON.stringify(iconState));
        await reader.locator("#next").click(); assert.equal(await card.count(),1);
        await select("Unconditional"); await expectText("无条件");
        assert.equal(await page.evaluate(()=>calls.length),0);
        assert.equal(await reader.locator("#native button").count(),1);
        assert.equal(await reader.locator(".custom-sections > .section").count(),1);
        assert.equal(await reader.locator(".other-plugin").textContent(),"Other plugin");
        await page.screenshot({path:path.join(output,"auto-word-light.png")});
        assert.equal(await reader.getByRole("button",{name:"补充释义",exact:true}).count(),0);
        await reader.getByRole("button",{name:"结合上下文解释",exact:true}).click();
        await expectText("这里表示当前段落");
        await reader.getByRole("button",{name:"复制当前语境含义",exact:true}).click();
        assert.equal(await page.evaluate(()=>copied),"n. 当前段落的具体含义\n这里表示当前段落中所指的概念。");
        assert.equal(await card.locator(".st-context-meaning").count(),1);
        await reader.getByRole("button",{name:"重翻语境",exact:true}).click();
        await page.waitForFunction(()=>calls.filter(c=>c.body.mode==="context"&&c.body.cachePolicy==="refresh").length===1);
        assert.equal(await card.locator(".st-footer button").filter({hasText:"重翻"}).count(),0);
        await page.screenshot({path:path.join(output,"personal-context-final.png")});
        await page.evaluate(()=>{calls.length=0});
        await reader.getByRole("button",{name:"复制",exact:true}).click();
        assert.match(await page.evaluate(()=>copied),/无条件/);
        await reader.locator(".st-header button[aria-pressed]").click();
        await page.locator("#outside").click(); assert.equal(await card.count(),1);
        await page.evaluate(()=>trigger("Architecture")); await expectText("建筑");assert.equal(await card.count(),1);
        assert.equal(await reader.locator(".st-header button[aria-pressed]").getAttribute("aria-pressed"),"true");
        // Embedded Reader pointer capture can fail: document listeners must still drag.
        await card.locator(".st-header").evaluate(el=>{el.setPointerCapture=()=>{throw new Error("inactive pointer")}});
        const before=await card.boundingBox();await page.mouse.move(before.x+5,before.y+5);await page.mouse.down();await page.mouse.move(90,100);await page.mouse.up();
        assert.ok((await card.boundingBox()).x<before.x);
        const savedPosition=await card.evaluate(el=>({left:Math.round(el.getBoundingClientRect().left),top:Math.round(el.getBoundingClientRect().top)}));
        assert.deepEqual(await page.evaluate(()=>({left:preferences.selectionPopupLeft,top:preferences.selectionPopupTop})),savedPosition);
        assert.equal(await page.evaluate(()=>preferences.selectionPopupPinned),true);
        await page.keyboard.press("Escape");await card.waitFor({state:"detached"});
        await page.evaluate(()=>trigger("Architecture"));await page.waitForTimeout(500);assert.equal(await card.count(),0);
        await reader.frameLocator("#pdf").locator("p").click();await select("Architecture");await expectText("建筑");
        assert.equal(await reader.locator(".st-header button[aria-pressed]").getAttribute("aria-pressed"),"true");
        assert.deepEqual(await card.evaluate(el=>({left:Math.round(el.getBoundingClientRect().left),top:Math.round(el.getBoundingClientRect().top)})),savedPosition);
        await reader.locator(".st-header button[aria-pressed]").click();
        assert.equal(await page.evaluate(()=>preferences.selectionPopupPinned),false);
        await reader.getByRole("button",{name:"关闭",exact:true}).click();await card.waitFor({state:"detached"});
        // A throwing optional constructor cannot leave the core blank/uncloseable.
        await page.evaluate(()=>{
            rdoc.documentElement.setAttribute("data-color-scheme","dark");rdoc.body.style.background="#202227";rdoc.body.style.color="#e8eaed";
            window.originalObserver=rdoc.defaultView.MutationObserver;
            rdoc.defaultView.MutationObserver=class {constructor(){throw new Error("unavailable")}};
        });
        await select("Unconditional");await expectText("无条件");await page.screenshot({path:path.join(output,"auto-word-dark.png")});
        await reader.getByRole("button",{name:"关闭",exact:true}).click();await card.waitFor({state:"detached"});
        assert.ok(await page.evaluate(()=>logs.some(([event])=>event==="selection_popup_theme_degraded")));
        // Even a disconnect failure cannot prevent removal.
        await page.evaluate(()=>{rdoc.defaultView.MutationObserver=class{observe(){}disconnect(){throw new Error("cleanup failed")}}});
        await select("Architecture");await expectText("建筑");await reader.getByRole("button",{name:"关闭",exact:true}).click();await card.waitFor({state:"detached"});
        await page.evaluate(()=>{rdoc.defaultView.MutationObserver=originalObserver;});
        // A partial mounting failure rolls back DOM; standalone fallback is closeable.
        await page.evaluate(()=>{
            const el=rdoc.documentElement;window.originalAppend=el.append;el.append=function(...nodes){originalAppend.apply(this,nodes);throw new Error("partial mount")};trigger("Unconditional");
        });
        await page.waitForFunction(()=>rdoc.querySelector(".pdf2zh-selection-card")?.textContent.includes("无法打开"));assert.equal(await card.count(),1);
        await card.getByRole("button",{name:"关闭"}).click();
        await page.evaluate(()=>{rdoc.documentElement.append=originalAppend;});
        await select("A sentence which has already been translated.");await expectText("布局生成");
        assert.ok(await page.evaluate(()=>calls.every(c=>!c.url.endsWith("translate-text"))));
        // Child PDF reloads must not leave outside click dismissal unbound.
        await reader.locator("#pdf").evaluate(el=>{el.srcdoc="<p>Reloaded PDF</p>"});
        await reader.frameLocator("#pdf").locator("p").click();await card.waitFor({state:"detached"});
        await page.evaluate(()=>{window.matchType="contained";});await select("Only translate this selected sentence.");await expectText("所选句子的中文译文");
        assert.equal(await card.locator("details").filter({hasText:"已有段落译文"}).getAttribute("open"),null);
        await card.getByRole("button",{name:"复制",exact:true}).click();assert.equal(await page.evaluate(()=>copied),"所选句子的中文译文");
        assert.ok(await page.evaluate(()=>calls.filter(c=>c.url.endsWith("translate-text")&&c.body.mode==="translate").every(c=>c.body.selectionProvider==="bing"&&c.body.memoryPolicy==="exact"&&!c.body.context&&!c.body.llm_api)));
        await page.locator("#outside").click();await card.waitFor({state:"detached"});
        await page.evaluate(()=>{window.memory=false;});await select("An unseen sentence needing free translation.");await expectText("所选句子的中文译文");
        assert.equal(await page.evaluate(()=>calls.filter(c=>c.url.endsWith("translate-text")&&c.body.mode==="translate").length),2);
        await page.locator("#outside").click();await card.waitFor({state:"detached"});
        await page.evaluate(()=>{window.pending=true;});await select("Request in flight");await page.waitForFunction(()=>typeof finish==="function");
        await reader.getByRole("button",{name:"关闭",exact:true}).click();await page.evaluate(()=>finish());await page.waitForTimeout(100);assert.equal(await card.count(),0);
        // A stable view moves an in-flight request into another document without cancelling/refetching.
        await select("Another request in flight"); await page.waitForFunction(()=>typeof finish==="function");
        const beforeDockCalls = await page.evaluate(()=>calls.length);
        await card.getByRole("button",{name:"移到右侧栏"}).click();
        const docked = page.locator("item-details .pdf2zh-selection-card"); await docked.waitFor();
        assert.equal(await card.count(),0);
        await page.evaluate(()=>finish());
        await page.waitForFunction(()=>paneBody.textContent.includes("late"));
        assert.equal(await page.evaluate(()=>calls.length),beforeDockCalls);
        await reader.frameLocator("#pdf").locator("p").click();await page.keyboard.press("Escape");
        assert.equal(await docked.count(),1);
        assert.equal(await page.evaluate(()=>preferences.selectionDisplayMode),"sidebar");
        assert.equal(await page.evaluate(()=>ZoteroContextPane.context.mode),"item");
        await docked.getByRole("button",{name:"切回悬浮窗"}).click();await card.waitFor();
        await expectText("late");assert.equal(await page.evaluate(()=>calls.length),beforeDockCalls);
        await page.evaluate(()=>{window.pending=false});
        // Pointer resizing persists dimensions across close/reopen and display switches.
        const originalSize = await card.boundingBox();
        const handle = await card.locator(".st-resize").boundingBox();
        await page.mouse.move(handle.x+6,handle.y+6);await page.mouse.down();await page.mouse.move(handle.x+76,handle.y+66);await page.mouse.up();
        const resized = await card.boundingBox();
        assert.ok(resized.width > originalSize.width + 50);
        assert.ok(resized.height > originalSize.height + 40);
        assert.equal(await page.evaluate(()=>preferences.selectionPopupWidth),Math.round(resized.width));
        await card.getByRole("button",{name:"移到右侧栏"}).click();await docked.waitFor();
        await docked.getByRole("button",{name:"更多操作"}).click();await docked.getByRole("menuitem",{name:"清空结果"}).click();assert.equal(await docked.count(),0);
        await page.evaluate(()=>{window.memory=true;window.matchType="exact";trigger("Saved sidebar mode")});await docked.waitFor();
        await page.waitForFunction(()=>paneBody.textContent.includes("布局生成"));
        await page.screenshot({path:path.join(output,"sidebar.png")});
        await docked.getByRole("button",{name:"切回悬浮窗"}).click();await card.waitFor();
        const restored = await card.boundingBox();assert.equal(Math.round(restored.width),Math.round(resized.width));
        assert.equal(Math.round(restored.x),Math.round(resized.x));
        assert.equal(Math.round(restored.y),Math.round(resized.y));
        await page.screenshot({path:path.join(output,"resized-floating.png")});
        await page.evaluate(()=>{window.pending=false;window.memory=true;window.matchType="exact";trigger("Window edge sentence");});await expectText("布局生成");
        await page.setViewportSize({width:340,height:330});await page.locator("#reader").evaluate(el=>{el.style.height="290px"});
        // Force a long paragraph response near the bottom right.
        await page.evaluate(()=>{rdoc.querySelector("#native").style.left="320px";rdoc.querySelector("#native").style.top="260px";window.fetch=async()=>({ok:true,status:200,json:async()=>({matched:true,matchType:"exact",translation:"已有长段落译文。".repeat(100)})});trigger("Long paragraph near edge")});
        await expectText("已有长段落");const box=await card.boundingBox();assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=340&&box.y+box.height<=330,JSON.stringify(box));
        assert.ok(await card.locator(".st-body").evaluate(el=>el.scrollHeight>el.clientHeight));await page.screenshot({path:path.join(output,"auto-narrow.png")});
        // Neither scrolling the card nor PDF nor an empty native popup closes results.
        await card.locator(".st-body").evaluate(el=>{el.scrollTop=40;el.dispatchEvent(new Event("scroll"))});assert.equal(await card.count(),1);
        await reader.frameLocator("#pdf").locator("p").evaluate(el=>el.dispatchEvent(new Event("scroll")));
        await page.evaluate(()=>trigger(""));assert.equal(await card.count(),1);
        await page.setViewportSize({width:920,height:740});await page.locator("#reader").evaluate(el=>{el.style.height="680px"});
        await page.evaluate(()=>{window.imported=true;window.fetch=async()=>{throw new Error("offline")};trigger("Unconditional")});await expectText("无条件的 0");
        assert.equal(await card.locator("details[open]").count(),0);
        assert.match(await card.locator(".st-body").textContent(),/更多释义/);
        assert.equal(await card.locator(".st-body").getByText("无条件的 3",{exact:true}).isVisible(),false);
        await page.screenshot({path:path.join(output,"collins-dark.png")});
        // Online lookup is independent of Python; click mode cannot query before the explicit action.
        const youdaoFixture = JSON.parse(fs.readFileSync(path.join(root,"plugin/tests/fixtures/selection-dictionaries/youdao-learning.json"),"utf8"));
        const bingFixture = fs.readFileSync(path.join(root,"plugin/tests/fixtures/selection-dictionaries/bing-learning.html"),"utf8");
        const parsed = await page.evaluate(html=>dictionaryAPI.parseBing(html,"learning",new DOMParser()),bingFixture);
        assert.ok(parsed.senses.length>=2);assert.ok(parsed.pronunciations.some(p=>p.audioUrl?.startsWith("https://www.bing.com/dict/mediamp3")));
        assert.ok(parsed.examples.length);assert.ok(!JSON.stringify(parsed).includes("<script"));
        await page.evaluate(fixture=>{
            selection.resetSelectionTranslation(); preferences.selectionDictionary="youdao";preferences.selectionTrigger="click";preferences.selectionDisplayMode="floating";preferences.selectionPopupWidth=0;preferences.selectionPopupHeight=0;preferences.selectionPopupPinned=false;
            window.dictionaryCalls=0;window.audioCalls=0;Zotero.HTTP.request=async(_method,_url,options)=>{
                if(options.responseType==="arraybuffer"){audioCalls++;return{response:new Uint8Array([1,2,3]).buffer,getResponseHeader:()=>"audio/mpeg"};}
                dictionaryCalls++;return{responseText:JSON.stringify(fixture)};
            };
            window.fetch=async()=>{throw Error("Python service intentionally unavailable")};
            window.calls.length=0;trigger("learning");
        },youdaoFixture);
        await page.waitForTimeout(500);assert.equal(await card.count(),0);assert.equal(await page.evaluate(()=>dictionaryCalls),0);assert.equal(await page.evaluate(()=>audioCalls),0);
        await reader.locator(".selection-popup").getByRole("button",{name:"翻译选中文字",exact:true}).last().click();await expectText("学习");
        assert.equal(await page.evaluate(()=>dictionaryCalls),1);assert.equal(await page.evaluate(()=>calls.length),0);
        assert.equal(await page.evaluate(()=>audioCalls),2,"both accents preload after showing the result");
        assert.equal(await page.locator("audio").count(),0,"preloading creates no playing media");
        assert.ok(await card.locator(".st-example mark").count()>0);assert.equal(await card.locator(".st-pronunciation button").count(),2);
        await card.screenshot({path:path.join(output,"youdao-compact-dark.png")});
        await card.locator(".st-editor summary").click();await card.locator("textarea").fill("studying");
        await page.waitForTimeout(450);assert.equal(await page.evaluate(()=>dictionaryCalls),1);
        await card.getByRole("button",{name:"移到右侧栏"}).click();await docked.waitFor();
        assert.equal(await docked.locator("textarea").inputValue(),"studying","draft survives moving into the sidebar");
        assert.equal(await docked.locator("textarea").isVisible(),true);
        await docked.getByRole("button",{name:"切回悬浮窗"}).click();await card.waitFor();
        assert.equal(await card.locator("textarea").inputValue(),"studying");
        assert.equal(await page.evaluate(()=>audioCalls),2,"moving the result reuses its audio");
        await card.locator("textarea").press("Control+Enter");await page.waitForFunction(()=>dictionaryCalls===2);
        await card.locator(".st-editor summary").click();await card.getByRole("button",{name:"恢复划选原文",exact:true}).click();
        assert.equal(await card.locator("textarea").inputValue(),"learning");
        await card.getByRole("button",{name:"取消",exact:true}).click();
        await page.evaluate(()=>{rdoc.documentElement.setAttribute("data-color-scheme","light");});
        await card.screenshot({path:path.join(output,"youdao-compact-light.png")});
        // Menu keyboard handling must keep the card open, fit the viewport and
        // remain usable at narrow widths and enlarged text sizes.
        for (const theme of ["light", "dark"]) {
            for (const width of [280, 320, 400]) {
                await page.evaluate(({theme,width})=>{
                    rdoc.documentElement.setAttribute("data-color-scheme",theme);
                    const card=rdoc.querySelector(".pdf2zh-selection-card");
                    card.style.width=width+"px";card.style.left="8px";card.style.top="8px";
                    card.style.fontSize="18px";
                },{theme,width});
                assert.ok(await card.evaluate(el=>el.scrollWidth<=el.clientWidth),`card overflow ${theme}/${width}`);
                assert.ok(await card.locator(".st-body").evaluate(el=>el.scrollWidth<=el.clientWidth));
                const more=card.getByRole("button",{name:"更多操作",exact:true});
                await more.focus();await page.keyboard.press("ArrowDown");
                const menu=card.getByRole("menu");await menu.waitFor();
                await page.keyboard.press("End");
                assert.equal(await menu.getByRole("menuitem").last().evaluate(el=>el===el.ownerDocument.activeElement),true);
                await page.keyboard.press("Home");await page.keyboard.press("ArrowDown");
                assert.equal(await menu.getByRole("menuitem").nth(1).evaluate(el=>el===el.ownerDocument.activeElement),true);
                assert.ok(await menu.evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=el.ownerDocument.defaultView.innerWidth&&r.bottom<=el.ownerDocument.defaultView.innerHeight&&el.contains(el.ownerDocument.elementFromPoint(r.left+10,r.bottom-10));}));
                await page.screenshot({path:path.join(output,`menu-${width}-${theme}.png`)});
                await page.keyboard.press("Escape");assert.equal(await card.count(),1);assert.equal(await more.getAttribute("aria-expanded"),"false");
                await more.click();await card.locator(".st-status").click();assert.equal(await menu.isVisible(),false);
                await card.locator(".st-body").evaluate(el=>{el.scrollTop=0;});
                await card.screenshot({path:path.join(output,`dictionary-${width}-${theme}.png`)});
            }
        }
        // Real ReadableStream chunks exercise the POST client and Reader stale-result boundary.
        await page.evaluate(()=>{
            selection.resetSelectionTranslation();preferences.selectionTrigger="auto";preferences.selectionTranslationProvider="profile";preferences.selectionStream=true;
            window.streams=[];window.cancellations=[];
            window.fetch=async(url,options)=>{
                const body=JSON.parse(options.body);
                if(url.endsWith("selection-capabilities"))return new Response(JSON.stringify({selectionLearning:true,selectionStream:true}));
                if(url.endsWith("cancel-text")){cancellations.push(body.requestId);return new Response('{}');}
                if(body.allowGenerate===false)return new Response(JSON.stringify({status:"miss"}));
                if(url.endsWith("translation-lookup"))return new Response(JSON.stringify({matched:false}));
                if(url.endsWith("translate-text/stream")){
                    let controller; const readable=new ReadableStream({start(c){controller=c;}});
                    let seq=0; const send=(event,data)=>controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify({...data,requestId:body.requestId,seq:++seq})}\n\n`));
                    streams.push({send,close:()=>controller.close(),signal:options.signal});send("start",{model:"test"});
                    return new Response(readable,{headers:{"Content-Type":"text/event-stream"}});
                }
                throw Error("unexpected request");
            };
            trigger("This sentence tests incremental generation.");
        });
        await page.waitForFunction(()=>streams.length===1);await page.evaluate(()=>streams[0].send("delta",{text:"第一段译文"}));await expectText("第一段译文");
        await card.getByRole("button",{name:"移到右侧栏"}).click();await docked.waitFor();
        await page.evaluate(()=>{streams[0].send("delta",{text:"仍在生成"});});await page.waitForFunction(()=>paneBody.textContent.includes("仍在生成"));
        await docked.getByRole("button",{name:"停止生成"}).click();assert.ok(await page.evaluate(()=>streams[0].signal.aborted));
        await page.waitForFunction(()=>cancellations.length===1);assert.ok((await docked.textContent()).includes("未完成"));
        assert.equal(await page.evaluate(()=>streams.length),1,"stopping never restarts generation");
        await docked.getByRole("button",{name:"切回悬浮窗"}).click();await card.waitFor();
        await page.evaluate(()=>trigger("The next selection must reject old output."));await page.waitForFunction(()=>streams.length===2);
        await page.evaluate(()=>{streams[0].send("done",{translation:"OLD RESULT"});streams[0].close();streams[1].send("delta",{text:"新选区"});streams[1].send("done",{translation:"新选区完成",saved:true});streams[1].close();});
        await expectText("新选区完成");assert.ok(!(await card.textContent()).includes("OLD RESULT"));
        // A stopped refresh must not reset controls belonging to the next
        // generation when its old request finally settles.
        await card.getByRole("button",{name:"重新翻译",exact:true}).click();await page.waitForFunction(()=>streams.length===3);
        await page.evaluate(()=>streams[2].send("delta",{text:"刷新中"}));await expectText("刷新中");
        await card.getByRole("button",{name:"停止生成"}).click();
        await card.getByRole("button",{name:"重试",exact:true}).click();await page.waitForFunction(()=>streams.length===4);
        await page.evaluate(()=>{streams[3].send("delta",{text:"再次生成"});streams[2].send("done",{translation:"过期刷新"});streams[2].close();});
        await expectText("再次生成");assert.equal(await card.getByRole("button",{name:"停止生成"}).isVisible(),true);
        await page.evaluate(()=>{streams[3].send("done",{translation:"重新生成完成"});streams[3].close();});await expectText("重新生成完成");
        await page.evaluate(()=>selection.unregisterSelectionTranslation());
        // Use the production renderer for both screenshot examples and both themes.
        const icon = fs.readFileSync(path.join(root, "plugin/addon/content/icons/selection-translate.svg"), "utf8");
        for (const dark of [false, true]) {
            for (const mode of ["sidebar", "floating"]) {
                for (const word of ["function", "point-wise"]) {
                    await page.evaluate(({dark,mode,word,icon}) => {
                        window.preview?.destroy();
                        document.documentElement.setAttribute("data-color-scheme",dark?"dark":"light");
                        document.body.replaceChildren();
                        document.body.style.cssText=`margin:0;padding:24px;background:${dark?"#303030":"#f4f4f4"};font:14px system-ui;color:${dark?"#e8eaed":"#202124"}`;
                        const shell=document.createElement("div");
                        shell.id="preview-shell";
                        shell.style.cssText="width:300px";
                        const heading=document.createElement("div");
                        heading.style.cssText="display:flex;gap:8px;align-items:center;padding:8px 4px;font-weight:600";
                        const img=document.createElement("img");img.src="data:image/svg+xml,"+encodeURIComponent(icon);img.width=20;img.height=20;
                        heading.append(img,document.createTextNode("翻译"));
                        const host=document.createElement("div");shell.append(heading,host);document.body.append(shell);
                        window.preview=previewFactory(document,heading,word,"lookup",()=>{},"dictionary-preview",()=>{},{host:mode==="sidebar"?host:undefined,onSwitch(){}});
                        preview.render({kind:"dictionary",headword:word,text:"fallback",origin:word==="function"?"柯林斯离线词典":"AI 补充 · 示例模型",phonetic:word==="function"?"fʌŋkʃn":undefined,aiGenerated:word!=="function",usage:word==="point-wise"?"也写作 pointwise；数学中的逐点不等于一致。":undefined,
                            senses:word==="function"?[
                                {pos:"N-COUNT",chinese:"功能;作用;职责",english:"the purpose of something",examples:[{english:"Each part has a specific function.",chinese:"每个部分都有特定的功能。"}]},
                                {pos:"VERB",chinese:"工作;运转;运行",english:"to work or operate",examples:[]},
                                {pos:"VERB",chinese:"发挥功能;起作用;行使职责",examples:[]},
                                {pos:"N-COUNT",chinese:"函数",examples:[]}
                            ]:[
                                {pos:"形容词",chinese:"逐点的",english:"at each point separately",examples:[{english:"a point-wise operation",chinese:"逐点运算"}]},
                                {pos:"副词",chinese:"逐点地",examples:[]}
                            ]});
                        preview.setLearning({refreshLabel:"重新生成",refreshHidden:word==="function",onRefresh(){},context:{title:"当前语境含义",contextMeaning:{pos:word==="function"?"n.":"adj.",meaning:word==="function"?"函数":"逐位置的",explanation:word==="function"?"此处指将输入映射为输出的数学函数。":"此处表示对每个位置分别应用同一变换。"},origin:"示例模型",actionLabel:"重翻语境",onAction(){}}});
                    },{dark,mode,word,icon});
                    const preview=page.locator("#dictionary-preview");
                    assert.equal(await preview.getAttribute("data-dark"),String(dark));
                    assert.equal(await preview.locator("details[open]").count(),0);
                    assert.equal(await preview.locator(".st-context-meaning").textContent(),word==="function"?"n.函数":"adj.逐位置的");
                    await (mode==="sidebar"?page.locator("#preview-shell"):preview).screenshot({path:path.join(output,`dictionary-${word}-${mode}-${dark?"dark":"light"}.png`)});
                    await preview.getByRole("button",{name:"复制",exact:true}).click();
                    const copied=await page.evaluate(()=>window.copied);
                    assert.ok(copied.startsWith(word+"\n"));
                    if(word==="function") {
                        assert.ok(copied.includes("4. n. [C] 函数"));
                        await preview.getByText("更多释义",{exact:true}).click();
                        assert.equal(await preview.locator("details .st-definition").textContent(),"函数");
                        await preview.getByText("更多释义",{exact:true}).click();
                    } else assert.ok(copied.includes("用法说明："));
                    await page.evaluate(()=>{window.previewControls={dictionaryAvailable:true,original:"original",dictionary:"youdao",translation:"bing",models:[],onMode(){},onDictionary(){},onTranslation(){},onSubmit(){}};preview.setControls(previewControls);});
                    await preview.locator(".st-editor summary").click();await preview.locator("textarea").fill("unfinished draft");
                    await preview.locator(".st-status").click();
                    await page.evaluate(()=>preview.setControls(previewControls));
                    assert.equal(await preview.locator("textarea").inputValue(),"unfinished draft","async controls cannot overwrite a blurred draft");
                    await preview.locator(".st-editor summary").click();
                    assert.ok(await preview.locator(".st-body").evaluate(el=>el.scrollWidth<=el.clientWidth));
                    // Narrow layout and very long headwords must wrap, never overflow.
                    await page.evaluate(()=>{preview.card.querySelector(".st-word").textContent="unusuallylongtechnicalcompound".repeat(4);preview.card.style.width="240px";});
                    assert.ok(await preview.locator(".st-body").evaluate(el=>el.scrollWidth<=el.clientWidth));
                }
            }
        }
        await page.evaluate(icon=>{
            window.preview?.destroy();document.body.replaceChildren();
            document.body.style.cssText="margin:0;display:flex;align-items:flex-start;height:72px;background:#888";
            for(const dark of [false,true]) {
                const panel=document.createElement("div");panel.style.cssText=`display:flex;gap:24px;align-items:center;padding:24px;background:${dark?"#303030":"#fff"}`;
                for(const size of [16,20,24]) {const img=document.createElement("img");img.src="data:image/svg+xml,"+encodeURIComponent(icon);img.width=img.height=size;panel.append(img);}
                document.body.append(panel);
            }
        },icon);
        await page.locator("img").evaluateAll(images=>Promise.all(images.map(img=>img.decode())));
        await page.screenshot({path:path.join(output,"translation-icons.png"),clip:{x:0,y:0,width:330,height:72}});
        assert.deepEqual(errors,[]);
        console.log("PASS: scroll/page navigation persistence, resizing and saved dimensions, sidebar round-trip, pending request transfer, saved display mode, native wrapper isolation, cancellation, dictionary/context/copy, theme, viewport bounds, and failure recovery");
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
