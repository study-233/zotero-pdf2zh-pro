// Browser regression for the real preferences modules. XUL controls are mapped to
// HTML equivalents; native Zotero rendering must still be accepted in Zotero.
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const ts = require('../plugin/node_modules/typescript');
const { chromium } = require(process.argv[2] || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local-dev/settings-ui');
fs.mkdirSync(output,{recursive:true});
const modules={};
for(const name of ['attachmentNaming','attachmentTitleLayout','attachmentNamingPreferences','selectionPreferences']) modules['./'+name]=ts.transpileModule(fs.readFileSync(path.join(root,'plugin/src/modules/'+name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const markup=fs.readFileSync(path.join(root,'plugin/addon/content/preferences.xhtml'),'utf8').replaceAll('__addonRef__','test');
const locales={};
for(const language of ['zh-CN','en-US']) locales[language]=['addon','preferences'].map(name=>fs.readFileSync(path.join(root,`plugin/addon/locale/${language}/${name}.ftl`),'utf8')).join('\n');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  for(const language of ['zh-CN','en-US']) {
   const page=await browser.newPage({viewport:{width:900,height:1050}});page.setDefaultTimeout(10000);
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.setContent('<html><body></body></html>');
   await page.evaluate(({markup,modules,fluent,language})=>{
    const labels={};let current;
    for(const line of fluent.split('\n')) {const m=line.match(/^([\w-]+)\s*=\s*(.*)$/);if(m){current=m[1];labels[current]=m[2];}else if(current&&/^\s+\.\w+/.test(line)){const a=line.match(/\.(\w[\w-]*)\s*=\s*(.*)/);labels[current+'.'+a[1]]=a[2];}else if(current&&line.startsWith('    ')) labels[current]+='\n'+line.trim();}
    window.msg=(key,args={})=>{if(!(key in labels))throw Error('Missing locale '+key);return labels[key].replace(/\{ \$(\w+) \}/g,(_,k)=>args[k]??'');};
    const xml=new DOMParser().parseFromString('<root xmlns:html="http://www.w3.org/1999/xhtml">'+markup+'</root>','application/xml');
    if(xml.querySelector('parsererror'))throw Error(xml.querySelector('parsererror').textContent);
    const convert=n=>{
     if(n.nodeType===3)return document.createTextNode(n.textContent);
     const tag=n.localName;const out=document.createElement(tag==='checkbox'?'input':tag==='menulist'?'select':tag==='groupbox'?'div':tag);
     for(const a of n.attributes) if(a.name!=='preference')out.setAttribute(a.name,a.value);
     const l=n.getAttribute('data-l10n-id');if(l){if(labels[l])out.textContent=msg(l);for(const attr of ['placeholder','aria-label','label'])if(labels[l+'.'+attr])out.setAttribute(attr,msg(l+'.'+attr));}
     if(tag==='checkbox'){out.type='checkbox';out.checked=false;out.addEventListener('change',()=>out.dispatchEvent(new Event('command')));}
     if(tag==='menulist'){for(const option of n.querySelectorAll('menuitem')){const o=document.createElement('option');o.value=option.getAttribute('value');o.textContent=msg(option.getAttribute('data-l10n-id')+'.label');out.append(o);}out.addEventListener('change',()=>out.dispatchEvent(new Event('command')));}
     else if(!l) for(const child of n.childNodes)if(child.nodeType===1||child.nodeType===3)out.append(convert(child));
     return out;
    };
    document.head.append(convert(xml.getElementsByTagNameNS('http://www.w3.org/1999/xhtml','style')[0]));
    const pane=document.createElement('main');pane.className='pdf2zh-pref-pane';document.body.append(pane);
    const naming=xml.querySelector('.attachment-naming');pane.append(convert(naming));
    pane.append(convert(xml.getElementById('zotero-prefpane-test-selection-provider')?.closest('section')||Array.from(xml.getElementsByTagNameNS('http://www.w3.org/1999/xhtml','section')).find(n=>n.querySelector('[id$="selection-provider"]'))));
    document.body.style.cssText='margin:22px;font:14px/1.5 system-ui;background:Field;color:FieldText';
    window.prefs=new Map([['rename',true],['selectionDictionary','ecdict']]);
    window.downloadState={phase:'idle',received:0,available:{version:'2026.10.01',sizeBytes:11462293,entryCount:37375}};
    window.watchers=new Set();window.changed=0;window.downloadCalls=0;window.filePath=undefined;
    const imports={
     '../../package.json':{config:{addonRef:'test'}},
     '../utils/prefs':{getPref:k=>prefs.get(k),setPref:(k,v)=>prefs.set(k,v)},
     '../utils/locale':{getString:(key,options)=>msg(key,options?.args)},
     './diagnostics':{recordDiagnostic(){}},
     './pdf2zhHelper':{PDF2zhHelperFactory:{getServerConfig:()=>({serverUrl:'http://localhost:8890'})}},
     './selectionRequest':{createSelectionRequest:()=>({post:async url=>{window.cacheCalls=(window.cacheCalls||[]).concat(url);return {ok:true,data:url.endsWith('selection-capabilities')?{selectionLearning:true}:{status:'ok'}}}})},
     './selectionDictionaryStore':{importODHDictionary:async()=>{prefs.set('selectionDictionary','collins');}},
     './selectionDictionaryDownload':{
      dictionaryDownloadState:()=>downloadState,subscribeDictionaryDownload:fn=>{watchers.add(fn);return()=>watchers.delete(fn)},refreshDictionaryDownload:async()=>{},dictionaryChoiceChanged(){},dictionaryImported:async()=>{},
      downloadDictionary:async()=>{downloadCalls++;downloadState.phase='downloading';downloadState.received=5242880;watchers.forEach(fn=>fn());},
      cancelDictionaryDownload:()=>{downloadState.phase='idle';watchers.forEach(fn=>fn());}
     }
    };
    window.ztoolkit={FilePicker:class{async open(){return filePath;}}};
    const require=name=>{if(!imports[name]){const exports={};new Function('require','exports',modules[name])(require,exports);imports[name]=exports;}return imports[name];};
    window.registerTitle=()=>require('./attachmentNamingPreferences').registerAttachmentNamingPreferences(window);
    registerTitle();require('./selectionPreferences').registerSelectionPreferences(window,()=>changed++);
    window.id=name=>document.getElementById('zotero-prefpane-test-'+name);
    window.titleLayout=()=>JSON.parse(prefs.get('attachmentTitleLayout'));
   },{markup,modules,fluent:locales[language],language});
   const id=n=>page.locator('#zotero-prefpane-test-'+n);
   assert.equal(await id('selection-auto-dictionary').isChecked(),true);
   await id('selection-auto-dictionary').uncheck();
   assert.equal(await page.evaluate(()=>prefs.get('selectionAutoDictionary')),false);
   await id('selection-clear-cache').click();
   await page.waitForFunction(()=>id('selection-cache-status').textContent.includes('保留')||id('selection-cache-status').textContent.includes('kept'));
   assert.equal(await page.evaluate(()=>cacheCalls.some(url=>url.endsWith('selection-cache/clear'))),true);
   // Preferences binding may run after the addon initializes the pane.
   assert.equal(await id('rename').isChecked(),true);
   assert.equal(await id('attachmentTitleEditor').isVisible(),true);
   assert.equal(await id('attachmentTitleDisabled').isVisible(),false);
   assert.equal(await id('attachmentTitleTemplate').count(),0);
   assert.equal(await id('attachmentTitleBlocks').locator('.title-block').count(),2);
   await id('attachmentTitleFields').locator('[data-field="model"]').click();
   assert.equal(await id('attachmentTitleFields').locator('[data-field="model"]').isDisabled(),true);
   await id('attachmentTitleBlocks').locator('.title-block-handle').last().focus();
   await page.keyboard.press('Alt+ArrowLeft');await page.keyboard.press('Alt+ArrowLeft');
   assert.equal(await page.evaluate(()=>titleLayout().blocks[0].field),'model');
   await id('attachmentTitleAddText').click();await id('attachmentTitleBlocks').locator('input').fill('精读版');
   await id('attachmentTitleSeparators').locator('[data-separator="_"]').click();
   assert.match(await id('attachmentTitlePreview-dual').textContent(),/^gpt-4.1-mini_Attention/);
   const before=await page.evaluate(()=>JSON.stringify(titleLayout().blocks));
   await id('attachmentTitleBlocks').locator('.title-block-handle').first().dragTo(id('attachmentTitleBlocks').locator('.title-block').last());
   assert.notEqual(await page.evaluate(()=>JSON.stringify(titleLayout().blocks)),before);
   await id('rename').uncheck();assert.equal(await id('attachmentTitleEditor').isVisible(),false);await id('rename').check();
   const saved=await page.evaluate(()=>prefs.get('attachmentTitleLayout'));await page.evaluate(()=>registerTitle());assert.equal(await page.evaluate(()=>prefs.get('attachmentTitleLayout')),saved);
   await id('attachmentTitleReset').click();
   await page.emulateMedia({colorScheme:'light'});await page.screenshot({path:path.join(output,language+'-light.png'),fullPage:true});
   await page.emulateMedia({colorScheme:'dark'});await page.evaluate(()=>document.documentElement.style.colorScheme='dark');
   await page.screenshot({path:path.join(output,language+'-dark.png'),fullPage:true});
   await id('selection-download').click();assert.equal(await id('selection-download-progress').isVisible(),true);assert.equal(await id('selection-import').isDisabled(),true);
   await id('selection-download').click();assert.equal(await id('selection-download-progress').isVisible(),false);
   await id('selection-provider').selectOption('profile');assert.equal(await id('selection-service-hint').isVisible(),false);
   await page.evaluate(()=>{downloadState.installed={version:'2026.10.01',entries:37375,aiEntries:945,skipped:4,importedAt:'2026-10-01T00:00:00Z',sha256:'a'.repeat(64)};watchers.forEach(fn=>fn());});
   assert.match(await id('selection-dictionary-status').textContent(),/37,375/);
   assert.equal(await page.locator('.dictionary-details').getAttribute('open'),null);
   await page.setViewportSize({width:400,height:900});
   for(const field of ['author','year','model','sourceLang','targetLang'])await id('attachmentTitleFields').locator(`[data-field="${field}"]`).click();
   const boxes=await id('attachmentTitleBlocks').locator('.title-block').evaluateAll(nodes=>nodes.map(n=>({x:n.getBoundingClientRect().x,y:n.getBoundingClientRect().y,right:n.getBoundingClientRect().right})));
   assert.ok(new Set(boxes.map(b=>b.y)).size>1);assert.ok(boxes.every(b=>b.x>=0&&b.right<=400));
   const handle=id('attachmentTitleBlocks').locator('.title-block-handle').last();await handle.dragTo(id('attachmentTitleBlocks').locator('.title-block').first());
   assert.equal(await page.evaluate(()=>titleLayout().blocks[0].field),'targetLang');
   await id('attachmentTitleAddText').click();await id('attachmentTitleBlocks').locator('input').fill('精读版 Reading edition');
   assert.ok(await id('attachmentTitleBlocks').locator('.title-block').evaluateAll(nodes=>nodes.every(n=>n.getBoundingClientRect().right<=400)));
   assert.ok((await id('attachmentTitleSeparatorCustom').boundingBox()).width<150);
   await page.screenshot({path:path.join(output,language+'-narrow.png'),fullPage:true});
   // Removing all content must preserve the last valid saved layout.
   let last;
   while(await id('attachmentTitleBlocks').locator('.title-block').count()) {last=await page.evaluate(()=>prefs.get('attachmentTitleLayout'));await id('attachmentTitleBlocks').locator('.title-block').first().locator('button').last().click();}
   assert.equal(await page.evaluate(()=>prefs.get('attachmentTitleLayout')),last);assert.equal(await id('attachmentTitleError').isVisible(),true);
   // A saved disabled preference must also win over an unbound/stale checkbox.
   await page.evaluate(()=>{const old=document.querySelector('.attachment-naming');old.replaceWith(old.cloneNode(true));prefs.set('rename',false);id('rename').checked=true;registerTitle();});
   assert.equal(await id('rename').isChecked(),false);
   assert.equal(await id('attachmentTitleEditor').isVisible(),false);
   assert.equal(await id('attachmentTitleDisabled').isVisible(),true);
   assert.equal(await page.evaluate(()=>prefs.get('rename')),false);
   assert.deepEqual(errors,[]);await page.close();
   console.log('PASS settings:',language,'drag/cross-row/keyboard/text/separator/migration/save/reopen/download states/themes');
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
