const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const run=require('../scripts/workspace.cjs').newRun('website-next-visual-motion');
const origin=process.env.WEBSITE_ORIGIN||'http://127.0.0.1:8897';const local=origin+'/zotero-pdf2zh-pro/';
const results=[];const errors=[];const previous={TEMP:process.env.TEMP,TMP:process.env.TMP};process.env.TEMP=process.env.TMP=run.work;
(async()=>{let browser,passed=false;try{
 browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined});
 const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light'});await context.addInitScript(()=>localStorage.setItem('theme','light'));
 const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 for(const [name,url] of [['memos','https://usememos.com/'],['zotero',local]]){
  await page.goto(url,{waitUntil:'networkidle',timeout:45000});await page.evaluate(()=>document.fonts.ready);
  const animations=await page.evaluate(()=>document.getAnimations().filter(a=>/copy-in|mock-in/.test(a.animationName||'')).map(a=>({name:a.animationName,duration:a.effect.getTiming().duration,delay:a.effect.getTiming().delay,easing:a.effect.getTiming().easing})));
  assert.equal(animations.length,2,name+' hero animations');assert.deepEqual(animations.map(a=>[a.duration,a.delay]),[[650,0],[800,120]]);
  for(const time of [50,300,1000]){await page.evaluate(time=>{for(const a of document.getAnimations().filter(a=>/copy-in|mock-in/.test(a.animationName||''))){a.pause();a.currentTime=time;}},time);await page.screenshot({path:path.join(run.report,`${name}-motion-${time}.png`)});}
  const geometry=await page.evaluate(()=>{const nav=document.querySelector('header nav'),title=document.querySelector('h1');return{header:document.querySelector('header').getBoundingClientRect().toJSON(),nav:nav.getBoundingClientRect().toJSON(),title:title.getBoundingClientRect().toJSON(),font:getComputedStyle(title).fontFamily,plotlyOnHome:performance.getEntriesByType('resource').some(r=>/plotly/i.test(r.name))};});
  results.push({name,animations,geometry});
  assert.equal(geometry.header.height,56);if(name==='zotero')assert.equal(geometry.plotlyOnHome,false);
 }
 assert.equal(results[0].geometry.nav.x,results[1].geometry.nav.x);assert.equal(results[0].geometry.nav.width,results[1].geometry.nav.width);
 for(const width of [1440,390])for(const [name,url] of [['memos-home','https://usememos.com/'],['memos-docs','https://usememos.com/docs'],['memos-changelog','https://usememos.com/changelog'],['zotero-home',local],['zotero-docs',local+'guide/windows/'],['zotero-changelog',local+'changelog/']]){
  await page.setViewportSize({width,height:1000});await page.goto(url,{waitUntil:'networkidle',timeout:45000});await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(run.report,`${name}-${width}.png`),fullPage:false});
 }
 await page.emulateMedia({reducedMotion:'reduce'});await page.goto(local,{waitUntil:'networkidle'});
 const reduced=await page.evaluate(()=>({running:document.getAnimations().filter(a=>a.playState==='running').length,opacity:getComputedStyle(document.querySelector('h1').parentElement).opacity,overflow:document.documentElement.scrollWidth>innerWidth}));
 assert.equal(reduced.running,0);assert.equal(reduced.opacity,'1');assert.equal(reduced.overflow,false);results.push({reduced});
 const plain=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});const fallback=await plain.newPage();await fallback.goto(local);assert.equal(await fallback.locator('h1').count(),1);assert.equal(await fallback.locator('a[href$="zotero-pdf2zh-pro.xpi"]').count(),1);await fallback.goto(local+'guide/windows/');assert.match(await fallback.locator('body').innerText(),/安装并启动/);await plain.close();
 assert.deepEqual(errors,[]);passed=true;
}finally{await browser?.close();for(const[key,value]of Object.entries(previous)){if(value===undefined)delete process.env[key];else process.env[key]=value;}fs.writeFileSync(path.join(run.report,'results.json'),JSON.stringify({passed,results,errors},null,2));}})().catch(e=>{console.error(e);process.exitCode=1;});
