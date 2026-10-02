const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {starCount,savedTheme,applyTheme,compareRows,displayedCost}=require('./site/ui.js');
const STAR_KEY='translation-bench-stars';
const now=1800000000000;
function store(initial={}){const values=new Map(Object.entries(initial));return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};}
const offline=async()=>{throw Error('offline');};

test('fresh star cache avoids requests; expired cache refreshes without credentials',async()=>{
  const cache=store({[STAR_KEY]:JSON.stringify({count:42,at:now-60_000})});
  let calls=0;
  assert.equal(await starCount(cache,async()=>{calls++;},now),42);
  assert.equal(calls,0);
  cache.setItem(STAR_KEY,JSON.stringify({count:42,at:now-30*60_000}));
  assert.equal(await starCount(cache,async(url,options)=>{
    calls++;
    assert.equal(url,'https://api.github.com/repos/study-233/zotero-pdf2zh-pro');
    assert.equal(options.credentials,'omit');
    assert.equal(options.headers,undefined);
    return {ok:true,json:async()=>({stargazers_count:43})};
  },now),43);
  assert.equal(calls,1);
  assert.deepEqual(JSON.parse(cache.getItem(STAR_KEY)),{count:43,at:now});
});
test('failed, rate-limited, or invalid star responses preserve cached truth',async()=>{
  const cache=store({[STAR_KEY]:JSON.stringify({count:42,at:now-1800001})});
  for(const fetcher of [offline,async()=>({ok:false}),async()=>({ok:true,json:async()=>({})})]){
    assert.equal(await starCount(cache,fetcher,now),42);
    assert.equal(await starCount(store(),fetcher,now),null);
  }
  assert.equal(await starCount(store({[STAR_KEY]:'broken'}),offline,now),null);
  assert.equal(await starCount(store(),async()=>({ok:true,json:async()=>({stargazers_count:0})}),now),0);
});
test('theme defaults dark and persists light; blocked storage stays usable',async()=>{
  const cache=store();assert.equal(savedTheme(cache),'dark');
  applyTheme('light',cache);assert.equal(savedTheme(cache),'light');
  applyTheme('invalid',cache);assert.equal(savedTheme(cache),'dark');
  const blocked={getItem(){throw Error();},setItem(){throw Error();}};
  assert.equal(savedTheme(blocked),'dark');assert.equal(applyTheme('light',blocked),'light');
  assert.equal(await starCount(blocked,offline,now),null);
});
test('displayed cost sorts partial subtotals numerically and leaves missing values last',()=>{
  const rows=[{name:'known',costUsd:.1},{name:'subtotal',costUsd:null,recordedCostUsd:.05},{name:'missing',costUsd:null}];
  assert.deepEqual([...rows].sort((a,b)=>compareRows(a,b,'displayedCost',false)).map(x=>x.name),['subtotal','known','missing']);
  assert.deepEqual([...rows].sort((a,b)=>compareRows(a,b,'displayedCost',true)).map(x=>x.name),['known','subtotal','missing']);
  assert.equal(displayedCost({costUsd:0,recordedCostUsd:2}),0);
  assert.equal(displayedCost({costUsd:null,recordedCostUsd:null}),null);
});
test('recorded estimates do not convert unknown campaign totals to known costs',()=>{
  const data=JSON.parse(fs.readFileSync(__dirname+'/site/results.json','utf8'));
  const unknown=data.results.filter(r=>r.costUsd===null);
  assert.equal(data.originalResults.filter(r=>r.costUsd===null).length,7);
  assert.equal(unknown.length,8);
  assert.ok(unknown.every(r=>r.recordedCostUsd>0));
  unknown.forEach(r=>{displayedCost(r);assert.equal(r.costUsd,null);});
});
test('reviewed supplements retain first attempts and accumulate their cost and time',()=>{
  const data=JSON.parse(fs.readFileSync(__dirname+'/site/results.json','utf8'));
  const supplemented=data.results.filter(r=>r.attempts);
  assert.equal(supplemented.length,3);
  for(const r of supplemented){
    assert.equal(r.reviewStatus,'verified');assert.equal(r.attempts.length,2);
    assert.equal(r.seconds,r.attempts.reduce((a,t)=>a+t.seconds,0));
    assert.equal(r.costUsd,r.attempts.some(t=>t.costUsd===null)?null:r.attempts.reduce((a,t)=>a+t.costUsd,0));
    assert.equal(r.recordedCostUsd,r.attempts.reduce((a,t)=>a+(t.recordedCostUsd||0),0));
    assert.equal(r.taskId,r.attempts[1].taskId);assert.equal(r.quality,r.attempts[1].quality);
    assert.equal(r.attempts[0].taskId,data.originalResults.find(t=>t.paper===r.paper&&t.model===r.model).taskId);
  }
});
