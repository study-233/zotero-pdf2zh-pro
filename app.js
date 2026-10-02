'use strict';
const benchmarkBase = new URL('.', document.currentScript.src);
let data, sortKey='name', descending=false, revealed=false, selectedPaper='all';
const $=id=>document.getElementById(id);
const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
const money=v=>v==null?'未知':'$'+v.toFixed(4);
const unit=v=>v==null?'未知':'$'+v.toFixed(v<.01?3:2);
const duration=v=>v==null?'未知':v>=60?(v/60).toFixed(1)+' 分钟':v.toFixed(1)+' 秒';
const names={completed:'完成',incomplete:'部分完成',failed:'失败',cancelled:'已取消',unavailable:'不可用',running:'运行中',pending:'待测试'};
const palette=Array.from({length:5},(_,i)=>'var(--series-'+(i+1)+')');
function option(select,value,label){const o=el('option',label);o.value=value;select.append(o);}
function shortPaper(id){return {attention:'Attention',bert:'BERT',resnet:'ResNet'}[id]||id;}
function assessable(r){return r&&r.reviewStatus==='verified'&&r.quality!=null&&!['failed','unavailable'].includes(r.status);}
function commonPapers(){return data.papers.filter(p=>data.models.every(m=>assessable(data.results.find(r=>r.paper===p.id&&r.model===m.id)))).map(p=>p.id);}
function aggregate(model){
  const chosen=selectedPaper;
  const papers=chosen==='all'?data.papers.map(p=>p.id):[chosen];
  const qualityPapers=chosen==='all'?commonPapers():papers;
  const rows=data.results.filter(r=>r.model===model.id&&papers.includes(r.paper));
  const scores=rows.filter(r=>qualityPapers.includes(r.paper));
  const complete=rows.length===papers.length&&papers.length>0;
  const sum=key=>complete&&rows.every(r=>r[key]!=null)?rows.reduce((a,r)=>a+r[key],0):null;
  const quality=scores.length===qualityPapers.length&&scores.length&&scores.every(assessable)?scores.reduce((a,r)=>a+r.quality,0)/scores.length:null;
  const recorded=rows.filter(r=>r.recordedCostUsd!=null);
  const missing=rows.reduce((a,r)=>a+(r.usageMissingRequests||0),0);
  const generated=rows.some(r=>(r.usageKnownRequests||0)>0);
  return {...model,quality,qualityCount:quality==null?0:scores.length,costUsd:generated?sum('costUsd'):null,
    recordedCostUsd:recorded.length?recorded.reduce((a,r)=>a+r.recordedCostUsd,0):null,missing,
    seconds:sum('seconds'),completedTasks:rows.filter(r=>r.status==='completed').length,
    status:complete&&rows.every(r=>r.status==='completed')?'completed':'incomplete',rows};
}
function cell(value,sub,cls='numeric'){
  const td=el('td');td.append(el('span',sub?value+' · '+sub:value,cls));return td;
}
function costCell(m){
  if(m.costUsd!=null)return cell(money(m.costUsd));
  if(m.recordedCostUsd!=null){const td=cell(money(m.recordedCostUsd));td.title='仅为已记录请求的小计，总费用未核清；详见费用说明。';return td;}
  return cell('未核清',null,'quiet');
}
function renderResults(){
  const items=data.models.map(aggregate);
  items.sort((a,b)=>BenchUI.compareRows(a,b,sortKey,descending));
  $('rows').replaceChildren();
  items.forEach(m=>{
    const tr=el('tr');tr.append(cell(m.name,null,'model-name'));
    tr.append(cell(m.quality==null?'无译文':m.quality.toFixed(2),null,m.quality==null?'quiet':'score'));
    tr.append(cell(unit(m.input)+(m.priceDetails?.timeOfDay?' 起':'')),cell(unit(m.output)+(m.priceDetails?.timeOfDay?' 起':'')),costCell(m),cell(duration(m.seconds)));
    tr.append(cell(m.completedTasks+' / '+m.rows.length));
    const td=el('td');const button=el('button','详情','detail-button');button.setAttribute('aria-label',m.name+' 详情');button.onclick=()=>showDetail(m.id);td.append(button);tr.append(td);$('rows').append(tr);
  });
  if(!items.length){const tr=el('tr');const td=el('td','暂无测评结果。','empty');td.colSpan=8;tr.append(td);$('rows').append(tr);}
  $('comparison-note').textContent=selectedPaper==='all'
    ?'质量基于共同可评分的 '+commonPapers().map(shortPaper).join('、')+'；费用与耗时包含首轮及补跑。'
    :'当前为 '+shortPaper(selectedPaper)+' 单篇实测。质量来自 12 个固定片段；费用与耗时包含所有尝试。';
  document.querySelectorAll('[data-sort]').forEach(b=>b.parentElement.setAttribute('aria-sort',b.dataset.sort===sortKey?(descending?'descending':'ascending'):'none'));
  renderChart(items);
}
function showDetail(id){
  const m=data.models.find(m=>m.id===id);$('detail-title').textContent=m.name;$('detail-content').replaceChildren();
  const intro=el('p','质量只评价已生成的译文；整项接口失败显示“无译文”。部分完成任务仍按原始评分记录漏译。');
  $('detail-content').append(intro);
  const wrap=el('div',null,'table-wrap'),table=el('table'),head=el('thead'),hr=el('tr');
  ['论文','质量 / 100','任务费用','耗时','状态'].forEach(t=>hr.append(el('th',t)));head.append(hr);table.append(head);
  const body=el('tbody');
  for(const r of data.results.filter(r=>r.model===id)){
    const tr=el('tr'),value=assessable(r)?r.quality.toFixed(2):'无译文';
    const rCost=r.usageKnownRequests>0?r.costUsd:null;
    tr.append(el('td',shortPaper(r.paper)),cell(value,assessable(r)?'12 个片段':'HTTP 403'));
    tr.append(costCell({...r,costUsd:rCost,missing:r.usageMissingRequests,rows:[r]}),cell(duration(r.seconds)));
    let extra=r.summary?(r.summary.succeeded+' / '+(r.summary.total-r.summary.skipped)+' 段成功'):'初始化失败';
    if(r.stopReason==='unknown_usage')extra='预算保护中止';
    tr.append(cell(names[r.status],extra));body.append(tr);
  }
  table.append(body);wrap.append(table);$('detail-content').append(wrap);
  const retried=data.results.filter(r=>r.model===id&&r.attempts);
  if(retried.length){const history=el('details');history.append(el('summary','首轮与补跑记录'));
    for(const r of retried)for(const [i,a] of r.attempts.entries())history.append(el('p',shortPaper(r.paper)+' · '+(i?'补跑':'首轮')+' · '+names[a.status]+' · '+(assessable(a)?a.quality.toFixed(2)+' 分':'无可用评分')+' · '+money(a.costUsd)+' · '+duration(a.seconds)+' · 任务 '+a.taskId));
    $('detail-content').append(history);}
  $('detail-content').append(el('h3','费用说明'));
  for(const r of data.results.filter(r=>r.model===id)){
    const text=shortPaper(r.paper)+'：'+(r.recordedCostUsd==null?'无可计价的生成记录。':money(r.recordedCostUsd)+' 来自 '+r.usageKnownRequests+' 次已记录请求。')+
      (r.usageMissingRequests?' '+r.usageMissingRequests+' 次请求未返回用量，不能确认其计费。':'');
    $('detail-content').append(el('p',text));
  }
  $('detail-content').append(el('p','费用与耗时累计首轮和补跑；各次任务记录单独保留。以上均为 token 估算，不是供应商逐任务账单；未核清费用不参加性价比排名。'));
  const prices=el('details');prices.append(el('summary','完整单价、分档与价格来源'));
  const pre=el('pre',JSON.stringify({unit:'USD / 1M tokens',input:m.input,output:m.output,cacheRead:m.cacheRead,tiers:m.priceDetails?.tiers,timeOfDay:m.priceDetails?.timeOfDay},null,2));prices.append(pre);
  const source=el('a','Command Code 价格快照 · '+data.pricingCheckedAt?.slice(0,10));source.href=data.pricingSource;prices.append(source);$('detail-content').append(prices);
  const review=el('button','查看翻译 PDF','secondary');review.style.marginTop='20px';review.onclick=()=>{$('model-dialog').close();switchView('review',{model:m.id,paper:selectedPaper==='all'?undefined:selectedPaper});};$('detail-content').append(review);
  $('model-dialog').showModal();
}
function switchView(view,pdfOptions){
  document.querySelectorAll('[data-view]').forEach(b=>{const active=b.dataset.view===view;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
  for(const name of ['table','chart','review'])$('view-'+name).hidden=name!==view;
  $('result-filters').hidden=view==='review';document.querySelector('.panel-tabs>.download').hidden=view==='review';if(view==='review')PdfShowcase.show(pdfOptions);
}
function navigate(){
  const method=['#method','#compare'].includes(location.hash);$('method').hidden=!method;$('results').hidden=method;
  if(location.hash==='#compare') { $('review-evidence').open=true; requestAnimationFrame(()=> $('review-evidence').scrollIntoView()); }
  document.querySelectorAll('[data-page]').forEach(a=>{if(a.dataset.page===(method?'method':'results'))a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
}

function initChrome(){
  const button=$('theme-toggle');
  function label(){const next=document.documentElement.dataset.theme==='light'?'深色':'浅色';button.setAttribute('aria-label','切换到'+next+'主题');button.title='切换到'+next+'主题';}
  label();button.onclick=()=>{BenchUI.applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light');label();};
  BenchUI.starCount().then(count=>{if(count==null)return;const node=$('star-count');node.textContent=count.toLocaleString('en-US');node.hidden=false;});
}

async function init(){
  try{
    const response=await fetch(new URL('results.json', benchmarkBase));if(!response.ok)throw Error('结果文件不可用');data=await response.json();
    if(data.schemaVersion!==1)throw Error('不支持的数据版本');PdfShowcase.configure(data);
    $('stats').textContent=data.models.length+' 个模型 · '+data.papers.length+' 篇论文 · '+data.preparedAt.slice(0,10)+' 测试';
    data.papers.forEach(p=>{option($('review-paper'),p.id,shortPaper(p.id));const a=el('a',p.title+' · '+p.arxiv);a.href='https://arxiv.org/abs/'+encodeURIComponent(p.arxiv);$('paper-links').append(a);});
    for(const finding of data.findings||[]){const item=el('article');item.append(el('h3',finding.title),el('p',finding.text));$('findings').append(item);}
    data.limitations.forEach(t=>$('limitations').append(el('li',t)));$('limitations').append(el('li','Zotero 界面验证：'+data.zoteroValidation));
    $('budget-status').textContent='预算 $'+data.budgetUsd+'；账号账单增量 '+money(data.budgetAccountDeltaUsd)+'（保守预算占用，不能分摊到模型）；'+data.unknownCostTasks+' 项总费用未核清。';
    $('provenance').textContent=JSON.stringify({commit:data.commit,benchmarkCodeCommit:data.benchmarkCodeCommit,preparedAt:data.preparedAt,pricingSource:data.pricingSource,pricingCheckedAt:data.pricingCheckedAt,pricingUnit:'USD / 1M tokens',prices:data.models,preparationSeconds:data.preparationSeconds,config:data.config,papers:data.papers,runtime:data.runtime,python:data.python,platform:data.platform,sourceFingerprints:data.sourceFingerprints,selectionAmendment:data.selectionAmendment,budgetResetAt:data.budgetResetAt,budgetAccountDeltaUsd:data.budgetAccountDeltaUsd},null,2);
    $('review-note').textContent=data.visibility==='private'?'本地完整对照版。展开片段查看原文、译文与页面；评审时未提供模型身份和价格。':'匿名评审依据。受来源许可限制，完整原文、译文与 PDF 页面仅在本地对照版展示。';
    $('updated').textContent='测试快照 '+data.preparedAt.slice(0,10);
    document.querySelectorAll('[data-paper]').forEach(b=>b.onclick=()=>{selectedPaper=b.dataset.paper;document.querySelectorAll('[data-paper]').forEach(p=>p.setAttribute('aria-pressed',String(p===b)));renderResults();});$('review-paper').onchange=updateCandidates;
    for(const id of ['left','right'])$(id).onchange=renderComparison;
    $('reveal').onclick=()=>{revealed=!revealed;$('reveal').textContent=revealed?'隐藏模型名称':'揭示模型名称';updateCandidates();};
    $('close-detail').onclick=()=>$('model-dialog').close();
    document.querySelectorAll('[data-sort]').forEach(b=>{const icon=el('span',null,'icon sort-icon');icon.setAttribute('aria-hidden','true');b.append(icon);b.onclick=()=>{const key=b.dataset.sort;descending=sortKey===key?!descending:key==='quality';sortKey=key;renderResults();};});
    const tabs=[...document.querySelectorAll('[data-view]')];
    tabs.forEach((b,i)=>{b.onclick=()=>switchView(b.dataset.view);b.onkeydown=e=>{if(!['ArrowRight','ArrowLeft','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;switchView(tabs[next].dataset.view);tabs[next].focus();};});
    window.addEventListener('hashchange',navigate);navigate();renderResults();updateCandidates();
  }catch(error){$('rows').replaceChildren();const tr=el('tr'),td=el('td','数据暂时无法加载，请刷新重试或下载 JSON。','empty');td.colSpan=8;tr.append(td);$('rows').append(tr);console.error(error);}
}

function renderChart(items){const points=items.filter(m=>m.quality!=null&&m.costUsd!=null&&m.status==='completed');$('chart').replaceChildren();$('chart-key').replaceChildren();if(!points.length){$('chart').append(el('p','当前选择没有同时满足完整翻译、已复核且费用已知的可比结果。可切换逐篇查看。'));return;}const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 520 280');function shape(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text)n.textContent=text;svg.append(n);return n;}const max=Math.max(...points.map(p=>p.costUsd),.001)*1.15;for(let i=0;i<=4;i++){const y=230-i*50;shape('line',{x1:45,x2:490,y1:y,y2:y,stroke:'var(--line)'});shape('text',{x:35,y:y+4,'text-anchor':'end','font-size':10,fill:'var(--muted)'},String(i*25));shape('text',{x:45+i*111,y:250,'text-anchor':'middle','font-size':10,fill:'var(--muted)'},'$'+(max*i/4).toFixed(3));}points.forEach(p=>{const color=palette[data.models.findIndex(m=>m.id===p.id)];const circle=shape('circle',{cx:45+p.costUsd/max*445,cy:230-p.quality*2,r:7,fill:color,stroke:'var(--panel)','stroke-width':2,tabindex:0});const title=document.createElementNS(ns,'title');title.textContent=`${p.name}：${p.quality.toFixed(1)} 分，${money(p.costUsd)}`;circle.append(title);const item=el('span');item.style.color=color;item.append(document.createTextNode(p.name));$('chart-key').append(item);});$('chart').append(svg);}
function updateCandidates(){const paper=$('review-paper').value;const rows=data.results.filter(r=>r.paper===paper&&r.alias);for(const id of ['left','right']){$(id).replaceChildren();rows.forEach(r=>option($(id),r.model,revealed?data.models.find(m=>m.id===r.model).name:'候选 '+r.alias));}if(rows.length>1)$('right').selectedIndex=1;renderComparison();}
function renderComparison(){$('comparison').replaceChildren();for(const id of ['left','right']){const r=data.results.find(r=>r.paper===$('review-paper').value&&r.model===$(id).value);const card=el('article',null,'candidate');if(!r){card.append(el('p','该论文的匿名评审材料尚未生成。','empty'));$('comparison').append(card);continue;}card.append(el('h3',revealed?data.models.find(m=>m.id===r.model).name:'候选 '+r.alias));card.append(el('p',r.reviewStatus==='verified'?'主代理复核完成':r.reviewStatus==='unverified'?'子代理已评，等待主代理复核':'等待 GPT-6 Sol 子代理评审','muted'));for(const s of r.samples){const block=el('details',null,'sample');block.append(el('summary',s.id+' · 第 '+s.page+' 页 · '+(s.scores?Object.values(s.scores).reduce((a,b)=>a+b,0)+' / 100':'无法确认')));if(s.unassessable)block.append(el('p',s.reason));else if(!s.issues.length)block.append(el('p','所选片段未发现需扣分的问题。'));else s.issues.forEach(i=>block.append(el('p',`[${i.severity} / ${i.category}] ${i.explanation}`)));if(s.source)block.append(el('p','原文：'+s.source));if(s.translation)block.append(el('p','译文：'+s.translation));appendLocalEvidence(block,s);card.append(block);}if(r.layout.length){card.append(el('h4','PDF 页面检查'));r.layout.forEach(l=>card.append(el('p',`第 ${l.page} 页 · ${l.status}：${l.evidence}`,'muted')));}appendReviewEvidence(card,r);$('comparison').append(card);}}
function appendLocalEvidence(block, sample) {
  if (!sample.pageParagraphs) return;
  const details=el('details');details.append(el('summary','查看该页原文与译文（本地材料）'));
  for(const p of sample.pageParagraphs){
    const pair=el('div',null,'sample');
    pair.append(el('p','原文：'+(p.source||'—')),el('p','译文：'+(p.translation||'【无译文】')));
    details.append(pair);
  }
  block.append(details);
}
function appendReviewEvidence(card, result) {
  if(result.status==='failed' && result.requests?.statusCodes?.['403'])card.append(el('p','首轮初始化接口返回 HTTP 403，未生成译文。这里记录的是首轮接口拒绝访问，不能据此判断模型的语言能力；生成 token 估算为零不等于供应商确认免计费。','muted'));
  if(result.failedParagraphs?.length){
    const details=el('details');details.append(el('summary','未完成段落与校验原因'));
    for(const f of result.failedParagraphs)details.append(el('p',`第 ${f.page} 页 · ${f.paragraphId}：${f.sourceKind==='url-footnote'?'网址脚注':'正文/参考文献'}，${f.unchangedTranslation?'插件判定译文未改变':f.errorType||'原因未知'}。`));
    card.append(details);
  }
  if(result.stopReason==='unknown_usage')card.append(el('p','首轮因缺少请求用量而中止；补跑文件可在“翻译 PDF”中查看，首轮评分保留。','muted'));
  if(result.reviewSummary)card.append(el('p',result.reviewSummary));
  if(result.reviewRevisions?.length){
    const details=el('details');details.append(el('summary','初评与修订记录'));
    for(const change of result.reviewRevisions)details.append(el('p',change.target+'：初评 '+change.initial+' 修订 '+change.revised));
    card.append(details);
  }
  if(result.audit?.length){
    const details=el('details');details.append(el('summary','主代理复核记录'));
    for(const a of result.audit)details.append(el('p',a.sample+'：'+a.note));
    card.append(details);
  }
  if(result.visuals?.length){
    const details=el('details');details.append(el('summary','PDF 排版案例（本地材料）'));
    for(const path of result.visuals){
      const img=el('img');img.src=path;img.alt=path.split('/').pop();img.loading='lazy';
      img.style.width='100%';details.append(img);
    }
    card.append(details);
  }
}

if(document.getElementById('theme-toggle')) initChrome();
init();
