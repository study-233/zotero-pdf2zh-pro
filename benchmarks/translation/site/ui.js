/* Theme initialization runs before the stylesheet to avoid a wrong-theme flash. */
(function (root) {
  'use strict';
  const THEME_KEY='translation-bench-theme';
  const STAR_KEY='translation-bench-stars';
  const STAR_TTL=30*60*1000;
  const STAR_URL='https://api.github.com/repos/study-233/zotero-pdf2zh-pro';
  function storage(){try{return root.localStorage;}catch{return null;}}
  function read(store,key){try{return store?.getItem(key);}catch{return null;}}
  function save(store,key,value){try{store?.setItem(key,value);}catch{/* Storage is optional. */}}
  function savedTheme(store=storage()){return read(store,THEME_KEY)==='light'?'light':'dark';}
  function applyTheme(theme,store=storage()){
    const value=theme==='light'?'light':'dark';
    if(root.document)root.document.documentElement.dataset.theme=value;
    save(store,THEME_KEY,value);
    return value;
  }
  async function starCount(store=storage(),fetcher=root.fetch.bind(root),now=Date.now()){
    let cached=null;
    try{
      const value=JSON.parse(read(store,STAR_KEY));
      if(value&&Number.isSafeInteger(value.count)&&value.count>=0&&Number.isFinite(value.at)&&value.at<=now)cached=value;
    }catch{/* A corrupt cache should not prevent the link working. */}
    if(cached&&now-cached.at<STAR_TTL)return cached.count;
    try{
      const response=await fetcher(STAR_URL,{credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(5000)});
      if(!response.ok)throw Error('GitHub count unavailable');
      const value=(await response.json()).stargazers_count;
      if(!Number.isSafeInteger(value)||value<0)throw Error('Invalid star count');
      save(store,STAR_KEY,JSON.stringify({count:value,at:now}));
      return value;
    }catch{return cached?.count??null;}
  }
  function displayedCost(row){return row.costUsd??row.recordedCostUsd??null;}
  function compareRows(a,b,key,descending){
    const x=key==='displayedCost'?displayedCost(a):a[key];
    const y=key==='displayedCost'?displayedCost(b):b[key];
    if(x==null)return y==null?0:1;
    if(y==null)return -1;
    return (typeof x==='string'?x.localeCompare(y):x-y)*(descending?-1:1);
  }
  const api={savedTheme,applyTheme,starCount,displayedCost,compareRows};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.BenchUI=api;
  if(root.document)root.document.documentElement.dataset.theme=savedTheme();
})(globalThis);
