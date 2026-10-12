const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');

function fixture(documentUrl,sha256){
  const elements=new Map(),blobs=[];
  const node=()=>({hidden:false,value:'',dataset:{},style:{},classList:{add(){}},
    append(){},replaceChildren(){},setAttribute(){},removeAttribute(){},addEventListener(){},querySelectorAll(){return [];}});
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  class FileURL extends URL{
    static createObjectURL(blob){blobs.push(blob);return 'blob:https://example.test/generated-'+blobs.length;}
    static revokeObjectURL(){}
  }
  const entry={taskId:'task-1',paper:'paper',model:'model',availability:'available',status:'completed',url:documentUrl,sha256};
  const context={window:{},document:{currentScript:{src:'https://example.test/bench/pdf-viewer.js'},
    getElementById:get,createElement:node,addEventListener(){},querySelectorAll(){return [];}},
    URL:FileURL,Blob,crypto:webcrypto,console:{warn(){}},ResizeObserver:class{observe(){}},
    fetch:async()=>({ok:true,json:async()=>({schemaVersion:1,documents:[entry]})})};
  vm.runInNewContext(fs.readFileSync(__dirname+'/site/pdf-viewer.js','utf8'),context);
  context.window.PdfShowcase.configure({papers:[{id:'paper'}],models:[{id:'model',name:'Model'}],results:[]});
  return {api:context.window.PdfShowcase,get,blobs};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));

test('PDF catalog links accept only same-origin HTTP(S)',async()=>{
  for(const [url,expected] of [
    ['pdfs/paper.pdf','https://example.test/bench/pdfs/paper.pdf'],
    ['https://example.test/paper.pdf','https://example.test/paper.pdf'],
    ['javascript:alert(1)',null],['data:text/html,<script>evil()</script>',null],
    ['https://evil.test/paper.pdf',null],['blob:https://example.test/untrusted',null],
    ['https://user:password@example.test/paper.pdf',null],['http://example.test/paper.pdf',null],
  ]){
    const f=fixture(url);
    await f.api.show({paper:'paper',model:'model'});
    await settle();
    if(expected){assert.equal(f.get('pdf-open').href,expected);assert.equal(f.get('pdf-download').href,expected);}
    else{assert.equal(f.get('pdf-open').hidden,true);assert.equal(f.get('pdf-open').href,undefined);}
  }
});

test('local previews create PDF blobs only after the expected file hash matches',async()=>{
  const bytes=new TextEncoder().encode('%PDF-1.4\nfixture');
  const hash=Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex');
  for(const matches of [false,true]){
    const f=fixture('blob:https://example.test/untrusted',matches?hash:'0'.repeat(64));
    await f.api.show({paper:'paper',model:'model'});
    f.get('pdf-file').onchange({target:{files:[{type:'text/html',arrayBuffer:async()=>bytes.buffer}],value:'file'}});
    await settle();
    assert.equal(f.blobs.length,matches?1:0);
    if(matches){
      assert.equal(f.blobs[0].type,'application/pdf');
      assert.equal(f.get('pdf-open').href,'blob:https://example.test/generated-1');
      assert.equal(await f.blobs[0].text(),'%PDF-1.4\nfixture');
    }
  }
});
