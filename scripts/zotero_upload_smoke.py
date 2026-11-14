"""Isolated Zotero native File/FormData + real HTTP + history window smoke test."""
import json
import os
import sys
import threading
import zipfile

from workspace import ROOT, Workspace
from zotero_acceptance import bundle_module, create_test_pdf, run_phase, write_profile

bootstrap = r'''
var chromeHandle;
function install() {}
function uninstall() {}
function shutdown() { if (chromeHandle) chromeHandle.destruct(); }
async function startup({rootURI,resourceURI}) {
 const work=WORK, report=REPORT, url=SERVER;
 let dialog;
 try {
  await Zotero.initializationPromise;
  while (!Zotero.getMainWindow()) await Zotero.Promise.delay(50);
  if (Zotero.DataDirectory.dir !== work+'/library') throw new Error('Wrong isolated library');
  const win=Zotero.getMainWindow(), root=rootURI || resourceURI.spec, results=[];
  const check=(value,name)=>{if(!value) throw new Error(name);results.push(name);};
  const base={Zotero,IOUtils,PathUtils,crypto:win.crypto,File:win.File,FormData:win.FormData,Blob:win.Blob,FileReader:win.FileReader,
   Response:win.Response,DOMException:win.DOMException,AbortController:win.AbortController,TextEncoder:win.TextEncoder,
   fetch:win.fetch.bind(win),setTimeout:win.setTimeout.bind(win),clearTimeout:win.clearTimeout.bind(win),ztoolkit:{log(){}}};
  Services.scriptloader.loadSubScript(root+'upload.js',base);
  const {PDF2zhHelperFactory:helper, ServerTaskClient:client}=base.smokeModule;
  const input=await helper.prepareFileData({isAttachment:()=>true,getFilePath:()=>work+'/input.pdf'},true);
  check(input.file instanceof win.File && input.base64===undefined,'native file-backed upload avoids base64');
  check(input.fingerprint===await IOUtils.computeHexDigest(work+'/input.pdf','sha256'),'source fingerprint matches native hash');
  const options={fileName:input.fileName,service:'openai',outputModes:['dual'],requestId:'native-binary-upload-0001'};
  const a=await client.createTask(url,options,undefined,input.file);
  const b=await client.createTask(url,options,undefined,input.file);
  check(a.taskId===b.taskId,'real HTTP multipart replay deduplicates');
  check(await IOUtils.computeHexDigest(work+'/server/'+a.taskId+'/input.pdf','sha256')===input.fingerprint,'server saved exact native PDF bytes');
  const first=await client.listTasks(url,{tracked:[a.taskId]});
  const second=await client.listTasks(url,{cursor:first.pagination.nextCursor,tracked:[a.taskId]});
  check(first.visibleTaskIds.length===31 && second.visibleTaskIds.length===31,'both pages retain active upload and 30 historical tasks');
  check(!first.visibleTaskIds.filter(id=>id!==a.taskId).some(id=>second.visibleTaskIds.includes(id)),'native pages have no duplicate history');
  // Serve the actual task window from an isolated chrome registration.
  chromeHandle=Cc['@mozilla.org/addons/addon-manager-startup;1'].getService(Ci.amIAddonManagerStartup)
   .registerChrome(Services.io.newURI(root+'manifest.json'),[['content','binarysmoke',root+'content/']]);
  let page=0, data=first, listener;
  const cursors=[undefined,first.pagination.nextCursor];
  const l10n=new win.Localization(['binary-smoke.ftl'],true);
  const api={_initPromise:Zotero.Promise.defer(),getSubmission:()=>undefined,getServerNotice:()=>undefined,
   getTasks:()=>data.tasks.filter(t=>data.visibleTaskIds.includes(t.taskId)).map(t=>({...t,source:'remote',importState:'none',serverUrl:url})),
   getHistoryPage:()=>({page:page+1,supported:true,hasMore:data.pagination.hasMore,total:data.pagination.totalHistory,loading:false}),
   changeHistoryPage:async direction=>{page+=direction;data=await client.listTasks(url,{cursor:cursors[page],tracked:[a.taskId]});listener?.();},
   refreshTasks:async()=>{},onTasksChanged:fn=>{listener=fn;return()=>{};},getEventStreamState:()=>({connected:0,total:0,hasErrors:false}),
   hasActiveTasks:()=>true,message:(key,options)=>l10n.formatValueSync(key,options?.args)};
  dialog=win.openDialog('chrome://binarysmoke/content/taskManager.xhtml','binary-smoke','chrome,centerscreen,resizable,width=980,height=720',api);
  await api._initPromise.promise;
  async function waitForPage(number, snapshot) {
   const doc=dialog.document;
   const expected=new Set(snapshot.visibleTaskIds);
   for (let attempt=0; attempt<100; attempt++) {
    const ids=Array.from(doc.getElementById('task-list').children,card=>card.dataset.taskId);
    if (doc.getElementById('history-label').textContent.trim()===String(number)
        && ids.length===expected.size && new Set(ids).size===expected.size
        && ids.every(id=>expected.has(id))) return;
    await Zotero.Promise.delay(50);
   }
   throw new Error('Task window did not render page '+number+' with its expected task IDs');
  }
  await waitForPage(1,first);
  check(dialog.document.getElementById('task-list').children.length===31,'real task window renders bounded first page');
  check(!dialog.document.getElementById('history-next').disabled,'next history button is enabled');
  dialog.document.getElementById('history-next').click();
  await waitForPage(2,second);
  check(!dialog.document.getElementById('history-prev').disabled,'real task window advances to page 2 and enables previous');
  check(dialog.document.getElementById('task-list').children.length===31,'real task window replaces history cards after next');
  dialog.document.getElementById('history-prev').click();
  await waitForPage(1,first);
  check(dialog.document.getElementById('history-prev').disabled,'real task window returns to page 1 and disables previous');
  await IOUtils.writeUTF8(report+'/native-result.json',JSON.stringify({passed:true,version:Zotero.version,results}));
 } catch(error) {await IOUtils.writeUTF8(report+'/failure.json',JSON.stringify({message:String(error),stack:error.stack}));}
 finally {dialog?.close();Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);}
}
'''


def run(work, report):
    http = None
    manager = None
    try:
        os.environ['PDF2ZH_DATA_DIR'] = str(work/'server')
        sys.path.insert(0, str(ROOT/'server'))
        import server
        from task_manager import TaskRecord
        from werkzeug.serving import make_server
        manager = server.TASK_MANAGER
        manager._start_worker_locked = lambda: None
        for n in range(65):
            task_id=f'history-{n:03}'
            record=TaskRecord(task_id,'history.pdf','openai',['dual'],{},work/'server'/task_id,status='completed')
            record.server_instance_id=manager._server_instance_id
            manager._revision+=1
            record.revision=manager._revision
            manager._tasks[task_id]=record
        http=make_server('127.0.0.1',0,server.app,threaded=True)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        url=f'http://127.0.0.1:{http.server_port}'
        profile, library = work/'profile', work/'library'
        (profile/'extensions').mkdir(parents=True)
        library.mkdir()
        write_profile(profile, library)
        manifest={'manifest_version':2,'name':'Isolated binary upload test','version':'1.0',
         'applications':{'zotero':{'id':'binary-smoke@pdf2zh.invalid','update_url':'https://example.invalid/update.json','strict_min_version':'8.0','strict_max_version':'10.0.*'}}}
        with zipfile.ZipFile(profile/'extensions/binary-smoke@pdf2zh.invalid.xpi','w') as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            archive.writestr('bootstrap.js',bootstrap.replace('WORK',json.dumps(str(work))).replace('REPORT',json.dumps(str(report))).replace('SERVER',json.dumps(url)))
            archive.writestr('content/taskManager.xhtml',(ROOT/'plugin/addon/content/taskManager.xhtml').read_bytes())
            for locale_file in (ROOT/'plugin/addon/locale').glob('*/addon.ftl'):
                archive.write(locale_file, f'locale/{locale_file.parent.name}/binary-smoke.ftl')
            archive.writestr('upload.js', bundle_module(
                "export {PDF2zhHelperFactory} from './src/modules/pdf2zhHelper';"
                "export {ServerTaskClient} from './src/modules/serverTaskClient';"
            ))
        create_test_pdf(work / 'input.pdf', 'Binary PDF upload verification')
        run_phase(work, report, 'zotero.log', 'native-result.json')
    finally:
        if http: http.shutdown();http.server_close();thread.join(timeout=5)
        if manager is not None:
            manager.close()
            manager.diagnostics.flush(timeout=2)


if __name__ == "__main__":
    with Workspace().run("zotero-binary-pagination-smoke") as (work, report):
        print(f"验收报告 {report}", flush=True)
        run(work, report)
