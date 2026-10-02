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
for(const name of ['attachmentNaming','attachmentTitleLayout','attachmentNamingSettings','attachmentNamingPreferences','selectionPreferences','llmApiManager']) modules['./'+name]=ts.transpileModule(fs.readFileSync(path.join(root,'plugin/src/modules/'+name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
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
    window.bindMenu=menu=>{
     const query=menu.querySelector.bind(menu);
     menu.querySelector=selector=>selector==='menupopup'?{
      replaceChildren:()=>menu.replaceChildren(),
      append:item=>{const option=new Option(item.getAttribute('label'),item.getAttribute('value'));for(const attr of item.attributes)if(attr.name!=='label')option.setAttribute(attr.name,attr.value);menu.append(option);}
     }:query(selector);
     menu.addEventListener('change',()=>menu.dispatchEvent(new Event('command')));
    };
    const convert=n=>{
     if(n.nodeType===3)return document.createTextNode(n.textContent);
     const tag=n.localName;const out=document.createElement(tag==='checkbox'?'input':tag==='menulist'?'select':tag==='groupbox'?'div':tag);
     for(const a of n.attributes) if(a.name!=='preference'&&a.name!=='onload')out.setAttribute(a.name,a.value);
     const l=n.getAttribute('data-l10n-id');if(l){if(labels[l])out.textContent=msg(l);for(const attr of ['placeholder','aria-label','label','title'])if(labels[l+'.'+attr])out.setAttribute(attr,msg(l+'.'+attr));}
     if(tag==='checkbox'){out.type='checkbox';out.checked=false;out.addEventListener('change',()=>out.dispatchEvent(new Event('command')));}
     if(tag==='menulist'){
      for(const option of n.querySelectorAll('menuitem')){const o=document.createElement('option');o.value=option.getAttribute('value');o.textContent=option.hasAttribute('data-l10n-id')?msg(option.getAttribute('data-l10n-id')+'.label'):option.getAttribute('label');out.append(o);}
      bindMenu(out);
     }
     else if(!l) for(const child of n.childNodes)if(child.nodeType===1||child.nodeType===3)out.append(convert(child));
     return out;
    };
    document.head.append(convert(xml.getElementsByTagNameNS('http://www.w3.org/1999/xhtml','style')[0]));
    // Zotero preferences.css: the odd-child rule caused the real pane to drift
    // right even though this HTML fixture used to pass. Include Windows margins too.
    const nativeStyle=document.createElement('style');nativeStyle.textContent=`
     .form-grid { display:grid; grid-template-columns:max-content 1fr; row-gap:.3em; }
     .form-grid > :nth-child(odd) { justify-self:end; }
     button, select, input { margin-block:4px; }
    `;document.head.append(nativeStyle);
    const pane=convert(xml.querySelector('.pdf2zh-pref-pane'));document.body.append(pane);
    document.body.style.cssText='margin:22px;font:16px/1.5 system-ui;background:Field;color:FieldText';
    window.prefs=new Map([['rename',true],['selectionDictionary','ecdict'],['selectedApiKey','document'],['attachmentTitleTemplate','{title} � {type}']]);
    window.profiles=[{key:'document',name:'Document',service:'openai',model:'document-model'},{key:'selection',name:'Selection',service:'openai',model:'selection-model'}];
    window.downloadState={phase:'idle',received:0,available:{version:'2026.10.01',sizeBytes:11462293,entryCount:37375}};
    window.watchers=new Set();window.changed=0;window.downloadCalls=0;window.filePath=undefined;
    const imports={
     '../../package.json':{config:{addonRef:'test'}},
     '../utils/prefs':{getPref:k=>prefs.get(k),setPref:(k,v)=>prefs.set(k,v)},
     '../utils/locale':{getString:(key,options)=>msg(key,options?.args)},
     './diagnostics':{recordDiagnostic(){}},
     './profileStore':{loadProfiles:()=>profiles},
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
    window.reopenTitle=()=>{
     const old=document.querySelector('.attachment-naming');const replacement=old.cloneNode(true);old.replaceWith(replacement);
     bindMenu(replacement.querySelector('[id$="attachmentTitleFields"]'));
     const checkbox=replacement.querySelector('[id$="rename"]');checkbox.addEventListener('change',()=>checkbox.dispatchEvent(new Event('command')));
     registerTitle();
    };
    window.registerSelection=()=>require('./selectionPreferences').registerSelectionPreferences(window,()=>changed++);
    registerTitle();registerSelection();
    window.id=name=>document.getElementById('zotero-prefpane-test-'+name);
    window.titleLayout=()=>JSON.parse(prefs.get('attachmentTitleLayout'));
    id('pluginVersion').textContent='1.7.5';id('serverVersion').textContent='1.7.5';id('serverStatus').textContent=msg('pref-server-connected');
    id('selectedApiKey').append(new Option('Document · document-model','document'));
    id('profileSummary').textContent='api.example.test';
    id('sourceLang').value='en';id('targetLang').value='zh-CN';id('new_serverip').value='http://127.0.0.1:8890';
   },{markup,modules,fluent:locales[language],language});
   const id=n=>page.locator('#zotero-prefpane-test-'+n);
   const addField=async field=>{
    await id('attachmentTitleFields').selectOption(field);
    await page.waitForFunction(()=>id('attachmentTitleFields').value==='');
    assert.equal(await id('attachmentTitleFields').locator(`[data-field="${field}"]`).isDisabled(),true);
   };
   const checkLayout=async stacked=>{
    const bounds=await page.evaluate(()=>{
     const rect=el=>{const {x,y,width,height,right,bottom}=el.getBoundingClientRect();return {x,y,width,height,right,bottom};};
     return {
      section:rect(document.querySelector('.selection-settings')),
      menus:['selection-provider','selection-dictionary','selection-model'].map(n=>rect(id(n))),
      labels:[...document.querySelectorAll('.selection-field > label')].map(rect),
      card:rect(document.querySelector('.dictionary-download-row')),
      auto:rect(document.querySelector('.selection-auto')),
      output:[...document.querySelectorAll('.output-option-row > div')].map(rect),
      tool:[...document.querySelector('.title-add-controls').children].map(rect),
      editor:rect(id('attachmentTitleEditor')),
      overflow:document.documentElement.scrollWidth>innerWidth,
     };
    });
    const near=(a,b,message)=>assert.ok(Math.abs(a-b)<1,message+`: ${a} vs ${b}`);
    const [service,dictionary,model]=bounds.menus;
    assert.equal(bounds.overflow,false,'no horizontal overflow');
    near(service.x,bounds.section.x,'service starts at section edge');
    near(service.width,dictionary.width,'service and dictionary equal width');
    near(model.x,bounds.section.x,'model starts at section edge');
    near(model.width,bounds.section.width,'model spans section');
    near(bounds.card.x,model.x,'dictionary card aligned');
    near(bounds.card.width,model.width,'dictionary card full width');
    near(bounds.auto.x,model.x,'automatic lookup aligned');
    bounds.menus.forEach((menu,i)=>{near(menu.x,bounds.labels[i].x,'label aligns with menu');near(menu.height,32,'consistent control height');});
    if(stacked){near(service.x,dictionary.x,'narrow service fields stack');assert.ok(dictionary.y>=service.bottom);}
    else {near(service.y,dictionary.y,'wide service fields share row');assert.ok(dictionary.x>service.right);}
    assert.ok(model.y>=dictionary.bottom,'model follows first row');
    for(let row=0;row<2;row++){
     const [first,second]=bounds.output.slice(row*2,row*2+2);
     if(stacked){near(first.x,second.x,'narrow output fields stack');assert.ok(second.y>=first.bottom);}
     else {near(first.y,second.y,'two columns for output');near(first.width,second.width,'equal output columns');}
    }
    bounds.tool.forEach(control=>assert.ok(control.x>=bounds.editor.x&&control.right<=bounds.editor.right+1,'toolbar fits editor'));
    near(bounds.tool[2].right,bounds.editor.right,'reset is aligned right');
   };
   await checkLayout(false);
   assert.equal(await page.evaluate(()=>titleLayout().separator),' · ');
   assert.doesNotMatch(await id('attachmentTitlePreview-dual').textContent(),/�/);
   assert.equal(await id('selection-model').inputValue(),'');
   await id('selection-model').selectOption('selection');
   assert.equal(await page.evaluate(()=>prefs.get('selectedApiKey')),'document');
   assert.equal(await page.evaluate(()=>prefs.get('selectionApiKey')),'selection');
   await page.evaluate(()=>{
    window.dispatchEvent(new Event('unload'));
    const old=id('selection-model').closest('section');const replacement=old.cloneNode(true);old.replaceWith(replacement);
    bindMenu(replacement.querySelector('[id$="selection-model"]'));
    for(const suffix of ['selection-provider','selection-dictionary'])replacement.querySelector('[id$="'+suffix+'"]').addEventListener('change',event=>event.target.dispatchEvent(new Event('command')));
    // Re-registering a new DOM simulates closing and reopening the settings pane.
    window.registerSelection();
   });
   assert.equal(await id('selection-model').inputValue(),'selection');
   await page.evaluate(()=>{profiles[1].model='updated-model';window.dispatchEvent(new Event('profiles-changed'));});
   assert.match(await id('selection-model').textContent(),/updated-model/);
   await page.evaluate(()=>{profiles.pop();window.dispatchEvent(new Event('profiles-changed'));});
   assert.equal(await id('selection-model-status').isVisible(),true);
   await id('selection-model').selectOption('');
   assert.equal(await id('selection-model-status').isVisible(),false);
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
   assert.equal(await id('attachmentTitleFields').locator('[data-field]').count(),9);
   assert.equal(await id('attachmentTitleFields').getAttribute('aria-label'),language==='zh-CN'?'添加字段':'Add field');
   // Command handlers must leave the menu untouched until it has closed.
   const immediate=await page.evaluate(()=>{const menu=id('attachmentTitleFields');menu.value='model';menu.dispatchEvent(new Event('command'));return {value:menu.value,count:titleLayout().blocks.length,disabled:menu.querySelector('[data-field="model"]').disabled};});
   assert.deepEqual(immediate,{value:'model',count:2,disabled:false});
   await page.waitForFunction(()=>id('attachmentTitleFields').value==='');
   assert.equal(await id('attachmentTitleFields').locator('[data-field="model"]').isDisabled(),true);
   await page.evaluate(()=>{const menu=id('attachmentTitleFields');menu.value='model';menu.dispatchEvent(new Event('command'));});
   await page.waitForFunction(()=>id('attachmentTitleFields').value==='');
   assert.equal(await id('attachmentTitleBlocks').locator('.title-block').count(),3,'duplicate field is rejected');
   await id('attachmentTitleBlocks').locator('.title-block').last().locator('button').last().click();
   assert.equal(await id('attachmentTitleFields').locator('[data-field="model"]').isDisabled(),false);
   await addField('model');
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
   await id('attachmentTitleSeparatorCustom').fill(' / ');
   assert.equal(await page.evaluate(()=>titleLayout().separator),' / ');
   const saved=await page.evaluate(()=>prefs.get('attachmentTitleLayout'));await page.evaluate(()=>reopenTitle());assert.equal(await page.evaluate(()=>prefs.get('attachmentTitleLayout')),saved);
   await id('attachmentTitleReset').click();
   await page.emulateMedia({colorScheme:'light'});await checkLayout(false);await page.screenshot({path:path.join(output,language+'-light.png'),fullPage:true});
   await page.locator('.output-settings').screenshot({path:path.join(output,language+'-title-light.png')});
   await page.emulateMedia({colorScheme:'dark'});await page.evaluate(()=>document.documentElement.style.colorScheme='dark');
   await checkLayout(false);await page.screenshot({path:path.join(output,language+'-dark.png'),fullPage:true});
   await page.locator('.output-settings').screenshot({path:path.join(output,language+'-title-dark.png')});
   await id('selection-download').click();assert.equal(await id('selection-download-progress').isVisible(),true);assert.equal(await id('selection-import').isDisabled(),true);
   await id('selection-download').click();assert.equal(await id('selection-download-progress').isVisible(),false);
   await id('selection-provider').selectOption('profile');assert.equal(await id('selection-service-hint').evaluate(el=>el.hidden),true);
   assert.equal(await id('selection-model').isVisible(),true);
   await id('selection-provider').selectOption('bing');
   assert.equal(await id('selection-model').isVisible(),true);
   await page.locator('.dictionary-details > summary').click();
   assert.equal(await id('selection-service-hint').isVisible(),true);
   await page.locator('.dictionary-details > summary').click();
   await page.evaluate(()=>{downloadState.installed={version:'2026.10.01',entries:37375,aiEntries:945,skipped:4,importedAt:'2026-10-01T00:00:00Z',sha256:'a'.repeat(64)};watchers.forEach(fn=>fn());});
   assert.match(await id('selection-dictionary-status').textContent(),/37,375/);
   assert.equal(await page.locator('.dictionary-details').getAttribute('open'),null);
   await id('selection-auto-dictionary').check();
   await page.evaluate(()=>{id('selection-cache-status').textContent='';});
   await id('selection-model').locator('xpath=ancestor::section').screenshot({path:path.join(output,language+'-selection-dark.png')});
   await page.evaluate(()=>{profiles.push({key:'long',name:'Long profile '.repeat(20),service:'openai',model:'provider/model-with-long-name'});window.dispatchEvent(new Event('profiles-changed'));});
   await id('selection-model').selectOption('long');
   await page.waitForFunction(()=>prefs.get('selectionApiKey')==='long');
   await checkLayout(false);
   for(const width of [280,480,520,720]){
    await page.evaluate(width=>document.querySelector('.pdf2zh-pref-pane').style.width=width+'px',width);
    await checkLayout(width<498);
   }
   // A wide application window can still contain a narrow preferences pane.
   await page.evaluate(()=>document.querySelector('.pdf2zh-pref-pane').style.width='360px');
   await checkLayout(true);
   for(const theme of ['light','dark']){
    await page.emulateMedia({colorScheme:theme});await page.evaluate(theme=>document.documentElement.style.colorScheme=theme,theme);
    await checkLayout(true);
    await page.locator('.output-settings').screenshot({path:path.join(output,language+'-title-narrow-'+theme+'.png')});
    await page.locator('.selection-settings').screenshot({path:path.join(output,language+'-selection-narrow-'+theme+'.png')});
   }
   await page.evaluate(()=>document.querySelector('.pdf2zh-pref-pane').style.width='');
   await page.setViewportSize({width:400,height:900});
   await checkLayout(true);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'settings must not overflow horizontally');
   for(const field of ['author','year','model','sourceLang','targetLang'])await addField(field);
   const boxes=await id('attachmentTitleBlocks').locator('.title-block').evaluateAll(nodes=>nodes.map(n=>({x:n.getBoundingClientRect().x,y:n.getBoundingClientRect().y,right:n.getBoundingClientRect().right})));
   assert.ok(new Set(boxes.map(b=>b.y)).size>1);assert.ok(boxes.every(b=>b.x>=0&&b.right<=400));
   const handle=id('attachmentTitleBlocks').locator('.title-block-handle').last();await handle.dragTo(id('attachmentTitleBlocks').locator('.title-block').first(),{targetPosition:{x:4,y:10}});
   assert.equal(await page.evaluate(()=>titleLayout().blocks[0].field),'targetLang');
   await id('attachmentTitleAddText').click();await id('attachmentTitleBlocks').locator('input').fill('精读版 Reading edition');
   assert.ok(await id('attachmentTitleBlocks').locator('.title-block').evaluateAll(nodes=>nodes.every(n=>n.getBoundingClientRect().right<=400)));
   assert.ok((await id('attachmentTitleSeparatorCustom').boundingBox()).width<150);
   assert.ok(await page.locator('.title-preview-row > span:first-child').evaluateAll(nodes=>nodes.every(el=>{const range=document.createRange();range.selectNodeContents(el);return range.getBoundingClientRect().height<30;})),'preview labels remain on one line');
   await page.screenshot({path:path.join(output,language+'-narrow.png'),fullPage:true});
   // Removing all content must preserve the last valid saved layout.
   let last;
   while(await id('attachmentTitleBlocks').locator('.title-block').count()) {last=await page.evaluate(()=>prefs.get('attachmentTitleLayout'));await id('attachmentTitleBlocks').locator('.title-block').first().locator('button').last().click();}
   assert.equal(await page.evaluate(()=>prefs.get('attachmentTitleLayout')),last);assert.equal(await id('attachmentTitleError').isVisible(),true);
   // A saved disabled preference must also win over an unbound/stale checkbox.
   await page.evaluate(()=>{prefs.set('rename',false);id('rename').checked=true;reopenTitle();});
   assert.equal(await id('rename').isChecked(),false);
   assert.equal(await id('attachmentTitleEditor').isVisible(),false);
   assert.equal(await id('attachmentTitleDisabled').isVisible(),true);
   assert.equal(await page.evaluate(()=>prefs.get('rename')),false);
   assert.deepEqual(errors,[]);await page.close();
   console.log('PASS settings:',language,'native CSS/layout/container widths/field menu/drag/cross-row/keyboard/text/separator/migration/save/reopen/download states/themes');
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
