'use strict';
let data, sortKey='name', descending=false, revealed=false;
const $=id=>document.getElementById(id);
const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
const money=v=>v==null?'—':'$'+v.toFixed(4);
const duration=v=>v==null?'—':v>=60?(v/60).toFixed(1)+' 分钟':v.toFixed(1)+' 秒';
const names={completed:'完成',incomplete:'部分完成',failed:'失败',cancelled:'已取消',unavailable:'不可用',running:'运行中',pending:'待测试'};
const palette=['#087962','#c58139','#6183b0','#876bb0','#ba6470','#709248'];
function option(select,value,label){const o=el('option',label);o.value=value;select.append(o);}
function commonPapers(){return data.papers.filter(p=>data.models.every(m=>data.results.some(r=>r.paper===p.id&&r.model===m.id&&['completed','incomplete','failed','cancelled','unavailable'].includes(r.status)))).map(p=>p.id);}
function aggregate(model){const chosen=$('paper').value;const papers=chosen==='all'?commonPapers():[chosen];const rows=data.results.filter(r=>r.model===model.id&&papers.includes(r.paper));const complete=rows.length===papers.length&&papers.length>0;const sum=key=>complete&&rows.every(r=>r[key]!=null)?rows.reduce((a,r)=>a+r[key],0):null;const quality=complete&&rows.every(r=>!['failed','unavailable'].includes(r.status)&&r.reviewStatus==='verified'&&r.quality!=null)?rows.reduce((a,r)=>a+r.quality,0)/rows.length:null;let total=0,ok=0;for(const r of rows){if(r.summary){total+=(r.summary.total||0)-(r.summary.skipped||0);ok+=r.summary.succeeded||0;}}return {...model,quality,costUsd:sum('costUsd'),seconds:sum('seconds'),rate:rows.every(r=>r.summary)&&total?ok/total:null,completedTasks:rows.filter(r=>r.status==='completed').length,status:complete?rows.every(r=>r.status==='completed')?'completed':rows.find(r=>r.status!=='completed').status:rows[0]?.status||'pending',rows};}
function renderResults(){const items=data.models.map(aggregate);items.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];if(x==null)return y==null?0:1;if(y==null)return -1;return (typeof x==='string'?x.localeCompare(y):x-y)*(descending?-1:1);});$('rows').replaceChildren();for(const m of items){const tr=el('tr');tr.append(el('td',m.name),el('td',m.quality==null?(m.rows.some(r=>['failed','unavailable'].includes(r.status))?'未交付全套':'待复核'):m.quality.toFixed(1),'numeric'),el('td',money(m.costUsd),'numeric'),el('td',duration(m.seconds),'numeric'),el('td',m.completedTasks+' / '+m.rows.length,'numeric'),el('td',m.rate==null?'—':(m.rate*100).toFixed(1)+'%','numeric'));const status=el('td');status.append(el('span',names[m.status]||m.status,'badge '+(m.status==='completed'?'good':m.status==='failed'?'bad':'')));tr.append(status);$('rows').append(tr);}const common=commonPapers();$('comparison-note').textContent=$('paper').value==='all'?`共同测试 ${common.length} 篇论文。总体费用与耗时为合计，质量为各篇等权平均；完整任务按服务终态统计。片段漏译计入评分；整项失败单列完成率，不纳入总体质量排行。未完成任务不参与性价比散点图。`:'此表显示选定论文的单次实测。费用为估算，缺失值不按零处理。';renderChart(items);}
function renderChart(items){const points=items.filter(m=>m.quality!=null&&m.costUsd!=null&&m.status==='completed');$('chart').replaceChildren();$('chart-key').replaceChildren();if(!points.length){$('chart').append(el('p','当前选择没有同时满足完整翻译、已复核且费用已知的可比结果。可切换逐篇查看。'));return;}const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 520 280');function shape(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text)n.textContent=text;svg.append(n);return n;}const max=Math.max(...points.map(p=>p.costUsd),.001)*1.15;for(let i=0;i<=4;i++){const y=230-i*50;shape('line',{x1:45,x2:490,y1:y,y2:y,stroke:'#e3e9e1'});shape('text',{x:35,y:y+4,'text-anchor':'end','font-size':10,fill:'#657970'},String(i*25));shape('text',{x:45+i*111,y:250,'text-anchor':'middle','font-size':10,fill:'#657970'},'$'+(max*i/4).toFixed(3));}points.forEach(p=>{const color=palette[data.models.findIndex(m=>m.id===p.id)];const circle=shape('circle',{cx:45+p.costUsd/max*445,cy:230-p.quality*2,r:7,fill:color,stroke:'white','stroke-width':2,tabindex:0});const title=document.createElementNS(ns,'title');title.textContent=`${p.name}：${p.quality.toFixed(1)} 分，${money(p.costUsd)}`;circle.append(title);const item=el('span');const dot=el('i');dot.style.background=color;item.append(dot,document.createTextNode(p.name));$('chart-key').append(item);});$('chart').append(svg);}
function updateCandidates(){const paper=$('review-paper').value;const rows=data.results.filter(r=>r.paper===paper&&r.alias);for(const id of ['left','right']){$(id).replaceChildren();rows.forEach(r=>option($(id),r.model,revealed?data.models.find(m=>m.id===r.model).name:'候选 '+r.alias));}if(rows.length>1)$('right').selectedIndex=1;renderComparison();}
function renderComparison(){$('comparison').replaceChildren();for(const id of ['left','right']){const r=data.results.find(r=>r.paper===$('review-paper').value&&r.model===$(id).value);const card=el('article',null,'candidate');if(!r){card.append(el('p','该论文的匿名评审材料尚未生成。','empty'));$('comparison').append(card);continue;}card.append(el('h3',revealed?data.models.find(m=>m.id===r.model).name:'候选 '+r.alias));card.append(el('p',r.reviewStatus==='verified'?'主代理复核完成':r.reviewStatus==='unverified'?'子代理已评，等待主代理复核':'等待 GPT-6 Sol 子代理评审','muted'));for(const s of r.samples){const block=el('div',null,'sample');block.append(el('b',s.id+' · 第 '+s.page+' 页 · '+(s.scores?Object.values(s.scores).reduce((a,b)=>a+b,0)+' / 100':'无法确认')));if(s.unassessable)block.append(el('p',s.reason));else if(!s.issues.length)block.append(el('p','所选片段未发现需扣分的问题。'));else s.issues.forEach(i=>block.append(el('p',`[${i.severity} / ${i.category}] ${i.explanation}`)));if(s.source)block.append(el('p','原文：'+s.source));if(s.translation)block.append(el('p','译文：'+s.translation));appendLocalEvidence(block,s);card.append(block);}if(r.layout.length){card.append(el('h4','PDF 页面检查'));r.layout.forEach(l=>card.append(el('p',`第 ${l.page} 页 · ${l.status}：${l.evidence}`,'muted')));}appendReviewEvidence(card,r);$('comparison').append(card);}}
async function init(){try{const response=await fetch('results.json');if(!response.ok)throw Error('结果文件不可用');data=await response.json();for(const finding of data.findings||[]){const item=el('article');item.append(el('h3',finding.title),el('p',finding.text,'muted'));$('findings').append(item);}$('budget-status').textContent='本轮预算 $'+data.budgetUsd+'；账号账单增量 '+money(data.budgetAccountDeltaUsd)+'（保守预算占用）；'+data.unknownCostTasks+' 项费用未核清。';if(data.schemaVersion!==1)throw Error('不支持的数据版本');const done=data.results.filter(r=>r.status==='completed').length;const reviewed=data.results.filter(r=>r.reviewStatus==='verified').length;$('study-status').textContent=`${done} / ${data.models.length*data.papers.length} 项完整翻译 · ${reviewed} 项已复核`;const stats=[[''+data.models.length,'参测模型'],[done+' / '+data.models.length*data.papers.length,'完整翻译任务'],[money(data.measuredSpendUsd),'本轮已知 token 费用'],['GPT-6 Sol','匿名子代理评审']];stats.forEach(([v,l])=>{const n=el('div',null,'stat');n.append(el('b',v),el('span',l));$('stats').append(n);});data.papers.forEach(p=>{option($('paper'),p.id,p.title);option($('review-paper'),p.id,p.title);const a=el('a',p.title+' · '+p.arxiv);a.href='https://arxiv.org/abs/'+encodeURIComponent(p.arxiv);$('paper-links').append(a);});data.limitations.forEach(t=>$('limitations').append(el('li',t)));$('limitations').append(el('li','Zotero 界面验证：'+data.zoteroValidation));$('provenance').textContent=JSON.stringify({commit:data.commit,benchmarkCodeCommit:data.benchmarkCodeCommit,preparedAt:data.preparedAt,pricingSource:data.pricingSource,pricingCheckedAt:data.pricingCheckedAt,pricingUnit:'USD / 1M tokens',prices:data.models,preparationSeconds:data.preparationSeconds,config:data.config,papers:data.papers,runtime:data.runtime,python:data.python,platform:data.platform,sourceFingerprints:data.sourceFingerprints,selectionAmendment:data.selectionAmendment,budgetResetAt:data.budgetResetAt,budgetAccountDeltaUsd:data.budgetAccountDeltaUsd},null,2);$('updated').textContent='数据更新 '+data.generatedAt.slice(0,10);$('paper').onchange=renderResults;$('review-paper').onchange=updateCandidates;for(const id of ['left','right'])$(id).onchange=renderComparison;$('reveal').onclick=()=>{revealed=!revealed;$('reveal').textContent=revealed?'隐藏模型名称':'揭示模型名称';updateCandidates();};document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{const key=b.dataset.sort;descending=sortKey===key?!descending:key==='quality';sortKey=key;renderResults();});if(!commonPapers().length)$('paper').value=data.papers[0].id;renderResults();updateCandidates();}catch(error){$('study-status').textContent='暂时无法读取结果';$('rows').append(el('tr','结果数据尚未发布，请稍后再试。'));console.error(error);}}
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
  if(result.status==='failed' && result.requests?.statusCodes?.['403'])card.append(el('p','初始化接口返回 HTTP 403，未生成译文。这里记录的是接口拒绝访问，不能据此判断模型的语言能力；生成 token 估算为零不等于供应商确认免计费。','muted'));
  if(result.failedParagraphs?.length){
    const details=el('details');details.append(el('summary','未完成段落与校验原因'));
    for(const f of result.failedParagraphs)details.append(el('p',`第 ${f.page} 页 · ${f.paragraphId}：${f.sourceKind==='url-footnote'?'网址脚注':'正文/参考文献'}，${f.unchangedTranslation?'插件判定译文未改变':f.errorType||'原因未知'}。`));
    card.append(details);
  }
  if(result.stopReason==='unknown_usage')card.append(el('p','此项因请求用量缺失被预算保护中止，未生成完整 PDF。','muted'));
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
init();
