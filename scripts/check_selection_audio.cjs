// Real media decoding/playback in a browser with a restricted Reader frame.
// node scripts/check_selection_audio.cjs [path-to-playwright] [optional-mp3]
const fs = require("fs"), path = require("path"), assert = require("node:assert/strict");
const ts = require("../plugin/node_modules/typescript");
const { chromium } = require(process.argv[2] || "playwright");
const root = path.resolve(__dirname, "..");
const code = ts.transpileModule(fs.readFileSync(path.join(root,"plugin/src/modules/selectionAudio.ts"),"utf8"), {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText;
const labels=Object.fromEntries([...fs.readFileSync(path.join(root,"plugin/addon/locale/zh-CN/addon.ftl"),"utf8").matchAll(/^([\w-]+) = (.+)$/gm)].map(m=>[m[1],m[2].trim()]));
function wav() {
    const rate=16000, samples=rate*2, buffer=Buffer.alloc(44+samples*2);
    buffer.write("RIFF");buffer.writeUInt32LE(buffer.length-8,4);buffer.write("WAVEfmt ",8);
    buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22);
    buffer.writeUInt32LE(rate,24);buffer.writeUInt32LE(rate*2,28);
    buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34);buffer.write("data",36);buffer.writeUInt32LE(samples*2,40);
    for(let i=0;i<samples;i++)buffer.writeInt16LE(Math.round(1000*Math.sin(i*2*Math.PI*440/rate)),44+i*2);
    return buffer;
}
(async()=>{
    const browser=await chromium.launch({headless:true,channel:"chrome",args:["--mute-audio"]});
    try {
        const page=await browser.newPage(), errors=[];
        page.setDefaultTimeout(10000);
        page.on("pageerror",e=>errors.push(e.message));
        await page.route("**/*",route=>route.abort());
        await page.setContent('<iframe id="reader"></iframe>');
        const data=process.argv[3]?fs.readFileSync(process.argv[3]):wav();
        await page.evaluate(({code,labels,bytes,type})=>{
            const frame=document.querySelector("#reader"), doc=frame.contentDocument;
            doc.open();doc.write('<meta http-equiv="Content-Security-Policy" content="media-src \'none\'"><button id="uk" title="英音发音"><svg width="16" height="16"><circle r="6" cx="8" cy="8"/></svg></button><button id="us" title="美音发音"><svg width="16" height="16"><circle r="6" cx="8" cy="8"/></svg></button><p id="status"></p>');doc.close();
            window.rdoc=doc;window.requests=[];window.pending=[];window.revoked=[];window.logs=[];window.word="learning";
            const revoke=URL.revokeObjectURL.bind(URL);
            URL.revokeObjectURL=url=>{revoked.push(url);revoke(url);};
            const result=()=>({response:new Uint8Array(bytes).buffer,getResponseHeader:()=>window.bad?"text/html":type});
            window.Zotero={
                getMainWindow:()=>window,
                HTTP:{request:async(method,url,options)=>{
                    const request={method,url,options,cancelled:false};requests.push(request);
                    options.cancellerReceiver(()=>{request.cancelled=true;});
                    if(window.hold)return new Promise(resolve=>pending.push(()=>resolve(result())));
                    return result();
                }},
            };
            const imports={
                "./selectionOnlineDictionary":{safeDictionaryAudio:url=>url?.startsWith("https://dict.youdao.com/")?url:undefined},
                "../utils/locale":{getString:key=>labels[key]},
                "./diagnostics":{recordDiagnostic:(...args)=>logs.push(args)},
            };
            window.api={};new Function("require","exports",code)(key=>imports[key],api);
            window.soundArgs=id=>[word,id==="uk"?"英":"美",id==="uk"?`https://dict.youdao.com/dictvoice?audio=${word}&type=1`:undefined];
            window.preload=id=>api.preloadSelectionAudio(...soundArgs(id));
            for(const id of ["uk","us"]) {
                const button=doc.getElementById(id);
                button.onclick=()=>api.playSelectionAudio(doc,...soundArgs(id),
                    button,message=>{doc.getElementById("status").textContent=message||"词典来源";});
            }
        },{code,labels,bytes:[...data],type:process.argv[3]?"audio/mpeg":"audio/wav"});
        const reader=page.frameLocator("#reader"), uk=reader.locator("#uk"), us=reader.locator("#us");
        assert.equal(await page.evaluate(()=>requests.length),0);
        await page.evaluate(()=>{window.releaseUK=preload("uk");window.releaseUS=preload("us");});
        assert.equal(await page.evaluate(()=>requests.length),2);
        assert.equal(await page.locator("audio").count(),0,"preload never autoplays");
        await uk.click();
        await page.waitForFunction(()=>document.querySelector("audio")?.currentTime>0.05);
        assert.equal(await page.evaluate(()=>requests.length),2,"click reuses the preloaded pronunciation");
        assert.equal(await page.evaluate(()=>rdoc.querySelectorAll("audio").length),0);
        assert.ok(await page.locator("audio").getAttribute("src").then(src=>src.startsWith("blob:")));
        assert.equal(await uk.getAttribute("aria-pressed"),"true");
        assert.equal(await uk.locator("svg").count(),1);
        await uk.click();
        assert.equal(await page.locator("audio").count(),0);
        assert.equal(await uk.getAttribute("title"),"英音发音");
        assert.equal(await page.evaluate(()=>revoked.length),1);
        await page.evaluate(()=>{releaseUK();releaseUS();preload("uk")();window.word="cancelled";window.hold=true;window.releaseUK=preload("uk");});
        assert.equal(await page.evaluate(()=>requests.length),3,"completed pronunciation stays cached");
        await uk.click();assert.equal(await uk.getAttribute("aria-busy"),"true");
        assert.equal(await page.evaluate(()=>requests.length),3,"click shares a pending preload");
        await page.evaluate(()=>releaseUK());
        assert.equal(await page.evaluate(()=>requests.at(-1).cancelled),false,"playback retains its pending request");
        await uk.click();assert.equal(await page.evaluate(()=>requests.at(-1).cancelled),true);
        await page.evaluate(()=>pending.shift()());
        assert.equal(await page.locator("audio").count(),0);
        await page.evaluate(()=>{window.word="switching";});
        await uk.click();await us.click();
        assert.equal(await page.evaluate(()=>requests.at(-2).cancelled),true);
        assert.match(await page.evaluate(()=>requests.at(-1).url),/lan=en&text=switching/);
        await page.evaluate(()=>pending.shift()());
        assert.equal(await page.locator("audio").count(),0);
        await page.evaluate(()=>pending.shift()());
        await page.waitForFunction(()=>document.querySelector("audio")?.currentTime>0.05);
        await page.evaluate(()=>api.stopSelectionAudio(document.createElement("div")));
        assert.equal(await page.locator("audio").count(),1,"closing another card does not stop playback");
        await page.evaluate(()=>rdoc.defaultView.dispatchEvent(new Event("pagehide")));
        assert.equal(await page.locator("audio").count(),0);
        await page.evaluate(()=>{window.hold=false;window.bad=true;window.word="retry";preload("uk")();});
        await uk.click();
        await page.waitForFunction(()=>rdoc.getElementById("uk").dataset.audioState==="error");
        assert.match(await reader.locator("#status").textContent(),/加载失败/);
        assert.equal(await uk.locator("svg").count(),1);
        await page.evaluate(()=>{window.bad=false;});
        await uk.click();
        await page.waitForFunction(()=>document.querySelector("audio")?.currentTime>0.05);
        await page.waitForFunction(()=>rdoc.getElementById("uk").dataset.audioState==="idle");
        assert.equal(await uk.getAttribute("title"),"英音发音");
        assert.equal(await page.locator("audio").count(),0);
        assert.equal(await page.evaluate(()=>revoked.length),3);
        await page.evaluate(()=>{
            window.originalPlay=HTMLMediaElement.prototype.play;
            HTMLMediaElement.prototype.play=()=>Promise.reject(new DOMException("Blocked","NotAllowedError"));
        });
        await uk.click();
        await page.waitForFunction(()=>rdoc.getElementById("uk").dataset.audioState==="error");
        assert.match(await reader.locator("#status").textContent(),/播放被阻止/);
        assert.equal(await page.locator("audio").count(),0);
        assert.equal(await page.evaluate(()=>revoked.length),4);
        await page.evaluate(()=>{HTMLMediaElement.prototype.play=originalPlay;});
        // Every popup owns a reference; moving the view shares its download,
        // while closing the final view aborts it and ignores a late response.
        await page.evaluate(()=>{window.hold=true;window.word="moving";window.releaseOld=preload("uk");window.releaseNew=preload("uk");releaseOld();});
        assert.equal(await page.evaluate(()=>requests.at(-1).cancelled),false);
        await page.evaluate(()=>releaseNew());
        assert.equal(await page.evaluate(()=>requests.at(-1).cancelled),true);
        const cancelledCount=await page.evaluate(()=>requests.length);
        await page.evaluate(()=>{window.releaseNew=preload("uk");pending.shift()();});
        assert.equal(await page.evaluate(()=>requests.length),cancelledCount+1);
        await page.evaluate(()=>{pending.shift()();});
        await page.evaluate(()=>{releaseNew();preload("uk")();window.hold=false;});
        assert.equal(await page.evaluate(()=>requests.length),cancelledCount+1,"late cancelled request cannot evict its replacement");
        // Fill beyond the twelve-entry limit; recent entries survive, older
        // completed entries can be fetched again after their views are closed.
        await page.evaluate(async()=>{
            for(let i=0;i<14;i++){window.word=`cached${i}`;const release=preload("uk");await new Promise(resolve=>setTimeout(resolve,0));release();}
        });
        const cachedCount=await page.evaluate(()=>requests.length);
        await page.evaluate(()=>{window.word="cached13";preload("uk")();});
        assert.equal(await page.evaluate(()=>requests.length),cachedCount);
        await page.evaluate(()=>{window.word="cached0";preload("uk")();});
        assert.equal(await page.evaluate(()=>requests.length),cachedCount+1);
        assert.deepEqual(errors,[]);
        console.log("PASS audio: silent preloading, shared downloads, bounded cache, actual "+(process.argv[3]?"MP3":"WAV")+" playback outside restricted Reader, inline icons, stop/cancel, stale responses, accent switching, page unload, retry, cleanup and Blob URL release");
    } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
