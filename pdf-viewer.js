/* PDF files and rendering assets stay on the same host. Nothing is uploaded. */
window.PdfShowcase=(()=>{
  'use strict';
  const $=id=>document.getElementById(id);
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  let data,catalog,enginePromise,manifestPromise,documentTask,pdf,observer,pageObserver;
  let paper='attention',model='gpt-6-luna',page=1,zoom=1,loadId=0,renderId=0,activeKey='',resizeTimer;
  const localFiles=new Map(),pageRenders=new Map(),visiblePages=new Set();
  let pageNodes=[],scrollFrame=0;
  const short=id=>({attention:'Attention',bert:'BERT',resnet:'ResNet'}[id]||id);
  const modelName=id=>data.models.find(m=>m.id===id)?.name||id;
  const current=()=>catalog?.documents.find(d=>d.paper===paper&&d.model===model);
  const result=()=>data.results.find(r=>r.paper===paper&&r.model===model);
  const fileUrl=d=>localFiles.get(d?.taskId)||d?.url;
  function setControls(enabled){
    for(const id of ['pdf-prev','pdf-next','pdf-page','pdf-zoom-in','pdf-zoom-out','pdf-fit'])$(id).disabled=!enabled;
    if(enabled){$('pdf-prev').disabled=page<=1;$('pdf-next').disabled=page>=pdf.numPages;$('pdf-zoom-out').disabled=zoom<=.5;$('pdf-zoom-in').disabled=zoom>=3;}
  }
  function message(title,text,allowLocal=false){
    $('pdf-sheet').hidden=true;$('pdf-empty').hidden=false;$('pdf-empty-title').textContent=title;
    $('pdf-empty-message').textContent=text;$('pdf-file-label').hidden=!allowLocal;
  }
  function clearDocument(){
    loadId++;renderId++;for(const job of pageRenders.values())job.cancel();pageRenders.clear();
    pageObserver?.disconnect();pageObserver=null;visiblePages.clear();pageNodes=[];
    observer?.disconnect();observer=null;$('pdf-sheet').replaceChildren();
    documentTask?.destroy().catch(()=>{});documentTask=null;pdf=null;
    $('pdf-reader').removeAttribute('aria-busy');$('pdf-stage').removeAttribute('aria-busy');
    $('pdf-thumbnails').replaceChildren();$('pdf-page-total').textContent='/ —';$('pdf-page').value=page;
    $('pdf-download').hidden=true;$('pdf-open').hidden=true;setControls(false);
  }
  function renderChoices(){
    $('pdf-papers').replaceChildren();
    for(const p of data.papers){
      const b=node('button',short(p.id));b.type='button';b.setAttribute('aria-pressed',String(p.id===paper));
      b.onclick=()=>{if(paper===p.id)return;paper=p.id;page=1;zoom=1;renderChoices();loadDocument();};$('pdf-papers').append(b);
    }
    $('pdf-models').replaceChildren();
    for(const m of data.models){
      const entry=catalog?.documents.find(d=>d.paper===paper&&d.model===m.id);
      const b=node('button',m.name,'pdf-model');b.type='button';b.setAttribute('aria-pressed',String(m.id===model));
      if(entry?.availability==='not_generated'||entry?.availability==='missing'){b.classList.add('pdf-model-unavailable');b.title='此任务没有可展示的 PDF，点击查看原因';}
      b.onclick=()=>{if(model===m.id)return;model=m.id;renderChoices();loadDocument();};$('pdf-models').append(b);
    }
  }
  function describeDocument(entry){
    const row=result();$('pdf-title').textContent=short(paper)+' / '+modelName(model);
    const status=entry?.status||row?.status;
    $('pdf-task-state').textContent=(entry?.supplemental?'补跑 · ':'')+(status==='completed'?'完整翻译':status==='incomplete'?'存在未完成段落':status==='cancelled'?'任务已中止':'任务失败');
    $('pdf-task-state').dataset.status=status||'unknown';
    $('pdf-document-meta').textContent=entry?.bytes?(entry.bytes/1048576).toFixed(1)+' MB · 双语 PDF':'双语 PDF';
    $('pdf-reader-note').textContent=entry?.supplemental?(entry.supplementaryRun?.reviewStatus==='verified'?'补跑 PDF 已完成评审与复核；表格费用与耗时包含首轮及补跑。左侧原文，右侧译文。':'这是补跑生成的 PDF，尚未重新评审；表格评分仍对应首轮任务。左侧原文，右侧译文。'):status==='incomplete'?'这是首轮任务生成的原始 PDF，仍有未完成段落；左侧原文，右侧译文。':'直接展示任务生成的双语 PDF；左侧原文，右侧译文。';
  }
  async function engine(){
    if(!enginePromise)enginePromise=import('./vendor/pdfjs/pdf.min.js').then(lib=>{lib.GlobalWorkerOptions.workerSrc=new URL('vendor/pdfjs/pdf.worker.min.js',document.baseURI).href;return lib;}).catch(error=>{enginePromise=null;throw error;});
    return enginePromise;
  }
  async function loadDocument(){
    const entry=current();clearDocument();const ticket=loadId;describeDocument(entry);
    if(!entry||entry.availability==='not_generated'){
      const row=result(),retry=entry?.supplementaryRun;
      const ongoing=retry&&['submitting','queued','running','cancelling'].includes(retry.status);
      message(ongoing?'正在补跑，等待 PDF':'此任务未生成 PDF',ongoing?'本次按账号账单增量控制预算。完成后会接入这里。':retry?'补跑结束后仍没有可下载的 PDF。':row?.stopReason==='unknown_usage'?'首轮因缺少请求用量而中止；原始结果保留。':row?.status==='failed'?'初始化接口返回 HTTP 403，翻译未开始。':'任务结束时没有可下载的文件。');return;
    }
    const url=fileUrl(entry);
    if(!url){
      message(entry.availability==='missing'?'未找到这份 PDF':'PDF 保存在本地',entry.availability==='missing'?'本地原始文件缺失，可选择相同校验值的 PDF 恢复预览。':'完整论文尚未公开分发。选择对应的本地 PDF 即可阅读，文件不会上传。',Boolean(entry.sha256));return;
    }
    activeKey=entry.taskId;$('pdf-download').href=url;$('pdf-download').download=short(paper)+'-'+modelName(model)+'.pdf';$('pdf-download').hidden=false;
    $('pdf-open').href=url;$('pdf-open').hidden=false;
    message('正在展开 PDF','正在加载原始页面…');$('pdf-reader').setAttribute('aria-busy','true');
    try{
      const lib=await engine();if(ticket!==loadId)return;
      const resource=new URL('vendor/pdfjs/',document.baseURI).href;
      documentTask=lib.getDocument({url,cMapUrl:resource+'cmaps/',cMapPacked:true,standardFontDataUrl:resource+'standard_fonts/',wasmUrl:resource+'wasm/',isEvalSupported:false});
      const doc=await documentTask.promise;if(ticket!==loadId)return;pdf=doc;
      page=Math.min(Math.max(page,1),doc.numPages);$('pdf-page').max=doc.numPages;
      await renderPage();if(ticket!==loadId)return;makeThumbnails(ticket);
    }catch(error){console.warn('PDF preview:',error.name,error.message);if(ticket===loadId){pdf=null;setControls(false);message('暂时无法渲染 PDF','可使用右上角的打开或下载按钮查看原始文件。');}}
    finally{if(ticket===loadId)$('pdf-reader').removeAttribute('aria-busy');}
  }
  function updatePageControls(){
    if(!pdf)return;setControls(true);$('pdf-page').value=page;$('pdf-page-total').textContent='/ '+pdf.numPages;
    $('pdf-fit').textContent=zoom===1?'适合宽度':Math.round(zoom*100)+'%';
    document.querySelectorAll('.pdf-thumbnail').forEach(b=>b.setAttribute('aria-current',b.dataset.page===String(page)?'page':'false'));
    const rail=$('pdf-thumbnails'),active=rail.querySelector('[aria-current="page"]');
    if(active&&rail.clientHeight){
      const box=active.getBoundingClientRect(),bounds=rail.getBoundingClientRect();
      if(box.top<bounds.top)rail.scrollTop+=box.top-bounds.top-10;
      else if(box.bottom>bounds.bottom)rail.scrollTop+=box.bottom-bounds.bottom+10;
    }
  }
  function syncScrollPage(){
    if(!pdf||!pageNodes.length||$('view-review').hidden)return;
    const stage=$('pdf-stage'),top=stage.scrollTop+Math.min(stage.clientHeight*.35,180);
    const match=pageNodes.find(e=>e.offsetTop+e.offsetHeight>top)||pageNodes[pageNodes.length-1];
    page=Number(match.dataset.page);updatePageControls();
  }
  async function renderPage(){
    if(!pdf)return;
    const ticket=++renderId,loadTicket=loadId,doc=pdf,targetPage=page,stage=$('pdf-stage');
    for(const job of pageRenders.values())job.cancel();pageRenders.clear();
    pageObserver?.disconnect();visiblePages.clear();
    const old=pageNodes[page-1],fraction=old?Math.max(0,Math.min(1,(stage.scrollTop-old.offsetTop)/old.offsetHeight)):0;
    const pages=await Promise.all(Array.from({length:doc.numPages},(_,i)=>doc.getPage(i+1)));
    if(ticket!==renderId||loadTicket!==loadId)return;
    pageNodes=[];const fragment=document.createDocumentFragment();
    const width=Math.max(120,stage.clientWidth-64)*zoom;
    for(const p of pages){
      const number=p.pageNumber,base=p.getViewport({scale:1}),viewport=p.getViewport({scale:width/base.width});
      const figure=node('figure',null,'pdf-page');figure.dataset.page=number;figure.style.width=viewport.width+'px';figure.style.height=viewport.height+'px';
      figure.setAttribute('aria-label',short(paper)+' '+modelName(model)+' 第 '+number+' 页，左侧原文，右侧译文');
      const placeholder=node('canvas');placeholder.width=1;placeholder.height=1;placeholder.style.width=viewport.width+'px';placeholder.style.height=viewport.height+'px';placeholder.setAttribute('aria-hidden','true');
      figure.append(placeholder,node('figcaption','第 '+number+' 页','sr-only'));fragment.append(figure);pageNodes.push(figure);
    }
    $('pdf-sheet').replaceChildren(fragment);$('pdf-sheet').hidden=false;$('pdf-empty').hidden=true;
    let queue=Promise.resolve();
    pageObserver=new IntersectionObserver(entries=>{
      for(const entry of entries){
        const figure=entry.target,number=Number(figure.dataset.page);
        if(entry.isIntersecting){
          visiblePages.add(number);
          queue=queue.then(async()=>{
            if(ticket!==renderId||!visiblePages.has(number)||figure.dataset.paint==='ready')return;
            const p=pages[number-1],base=p.getViewport({scale:1}),viewport=p.getViewport({scale:width/base.width});
            const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(14000000/(viewport.width*viewport.height)));
            const canvas=node('canvas');canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);
            canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';canvas.setAttribute('role','img');canvas.setAttribute('aria-label',figure.getAttribute('aria-label'));
            let job;
            try{
              job=p.render({canvasContext:canvas.getContext('2d'),viewport,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null});pageRenders.set(number,job);await job.promise;
              if(ticket!==renderId||!visiblePages.has(number))return;
              figure.querySelector('canvas').replaceWith(canvas);figure.dataset.paint='ready';
            }catch(error){if(ticket===renderId&&error.name!=='RenderingCancelledException')figure.setAttribute('aria-label','第 '+number+' 页暂时无法显示，可打开原始 PDF。');}
            finally{if(pageRenders.get(number)===job)pageRenders.delete(number);}
          });
        }else{
          visiblePages.delete(number);pageRenders.get(number)?.cancel();
          const canvas=figure.querySelector('canvas');canvas.width=1;canvas.height=1;delete figure.dataset.paint;
        }
      }
    },{root:stage,rootMargin:'800px'});
    for(const figure of pageNodes)pageObserver.observe(figure);
    const selected=pageNodes[targetPage-1];stage.scrollTop=Math.max(0,selected.offsetTop-24+fraction*selected.offsetHeight);
    page=targetPage;updatePageControls();
  }
  function makeThumbnails(ticket){
    const rail=$('pdf-thumbnails');rail.replaceChildren();let queue=Promise.resolve();const doc=pdf;
    observer=new IntersectionObserver(entries=>{
      for(const entry of entries){if(!entry.isIntersecting)continue;observer?.unobserve(entry.target);
        queue=queue.then(async()=>{if(ticket!==loadId)return;try{
          const p=await doc.getPage(Number(entry.target.dataset.page));if(ticket!==loadId)return;
          const base=p.getViewport({scale:1}),viewport=p.getViewport({scale:112/base.width});
          const canvas=entry.target.querySelector('canvas');canvas.width=Math.ceil(viewport.width*1.5);canvas.height=Math.ceil(viewport.height*1.5);
          await p.render({canvasContext:canvas.getContext('2d'),viewport,transform:[1.5,0,0,1.5,0,0]}).promise;
        }catch{/* A thumbnail failure never blocks the main PDF. */}});
      }
    },{root:rail,rootMargin:'120px'});
    for(let number=1;number<=doc.numPages;number++){
      const b=node('button',null,'pdf-thumbnail');b.type='button';b.dataset.page=number;b.setAttribute('aria-label','跳到第 '+number+' 页');b.setAttribute('aria-current',number===page?'page':'false');
      const canvas=node('canvas');canvas.setAttribute('aria-hidden','true');b.append(canvas,node('span',String(number)));b.onclick=()=>goTo(number);rail.append(b);observer.observe(b);
    }
  }
  function goTo(value){if(!pdf||!Number.isFinite(value))return;page=Math.min(pdf.numPages,Math.max(1,Math.trunc(value)));const target=pageNodes[page-1];if(target)$('pdf-stage').scrollTop=Math.max(0,target.offsetTop-24);updatePageControls();}
  function changeZoom(amount){if(!pdf)return;zoom=Math.min(3,Math.max(.5,zoom+amount));renderPage();}
  async function attachFile(file){
    if(!file)return;const entry=current(),task=entry?.taskId;
    if(!entry?.sha256)return;
    message('正在核对 PDF','校验文件与原始任务是否一致…');
    try{
      const bytes=await file.arrayBuffer();const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
      if(current()?.taskId!==task)return;
      if(hash!==entry.sha256){message('文件与当前任务不匹配','请选择这一模型对应的原始 PDF，避免展示错误的测评结果。',true);return;}
      if(localFiles.has(task))URL.revokeObjectURL(localFiles.get(task));localFiles.set(task,URL.createObjectURL(file));loadDocument();
    }catch{if(current()?.taskId===task)message('无法读取文件','请重新选择本地 PDF。',true);}
  }
  function configure(input){
    data=input;renderChoices();
    $('pdf-stage').addEventListener('scroll',()=>{cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(syncScrollPage);},{passive:true});
    $('pdf-prev').onclick=()=>goTo(page-1);$('pdf-next').onclick=()=>goTo(page+1);
    $('pdf-page').onchange=e=>goTo(Number(e.target.value));$('pdf-page').onkeydown=e=>{if(e.key==='Enter')goTo(Number(e.target.value));};
    $('pdf-zoom-in').onclick=()=>changeZoom(.25);$('pdf-zoom-out').onclick=()=>changeZoom(-.25);$('pdf-fit').onclick=()=>{zoom=1;renderPage();};
    $('pdf-stage').onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();goTo(page+(e.key==='ArrowRight'?1:-1));}};
    $('pdf-file').onchange=e=>{attachFile(e.target.files[0]);e.target.value='';};
    $('pdf-fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('pdf-reader').requestFullscreen();}catch{$('pdf-reader-note').textContent='当前浏览器不支持全屏，可使用右上角的按钮打开原始 PDF。';}};
    document.addEventListener('fullscreenchange',()=>{$('pdf-fullscreen').setAttribute('aria-label',document.fullscreenElement?'退出全屏':'全屏阅读');});
    new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(!$('view-review').hidden&&pdf)renderPage();},150);}).observe($('pdf-stage'));
  }
  async function show(options={}){
    if(!data)return;
    if(options.paper&&data.papers.some(p=>p.id===options.paper)&&options.paper!==paper){paper=options.paper;page=1;zoom=1;}
    if(options.model&&data.models.some(m=>m.id===options.model))model=options.model;
    try{
      if(!manifestPromise)manifestPromise=fetch('pdfs.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(value=>{if(value.schemaVersion!==1||!Array.isArray(value.documents))throw Error();return value;}).catch(error=>{manifestPromise=null;throw error;});
      catalog=await manifestPromise;renderChoices();
      const count=catalog.documents.filter(d=>d.sha256).length;$('pdf-library-count').textContent=count+' 份双语 PDF';
      if(current()?.taskId===activeKey&&pdf){renderPage();return;}loadDocument();
    }catch{message('PDF 目录暂时不可用','请刷新页面重试。');}
  }
  return {configure,show};
})();
