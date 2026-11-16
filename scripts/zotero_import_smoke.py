"""Verify import transactions and their receipts across two Zotero processes."""
import json
import zipfile

from workspace import Workspace
from zotero_acceptance import bundle_module, create_test_pdf, run_phase, write_profile

bootstrap = r'''
function install() {}
function uninstall() {}
function shutdown() {}
async function startup({rootURI,resourceURI}) {
 const work=WORK, report=REPORT;
 try {
  await Zotero.initializationPromise;
  while (!Zotero.getMainWindow()) await Zotero.Promise.delay(50);
  if (Zotero.DataDirectory.dir !== work+'/library') throw new Error('Wrong isolated data directory');
  const ctx={Zotero,IOUtils,PathUtils,DOMException:Zotero.getMainWindow().DOMException,ztoolkit:{log:(...args)=>Zotero.debug(args.join(' '))}};
  Services.scriptloader.loadSubScript((rootURI || resourceURI.spec)+'store.js',ctx);
  const api=ctx.smokeModule;
  const phase=JSON.parse(await IOUtils.readUTF8(work+'/phase.json'));
  const results=[];
  function check(value,name) {if(!value) throw new Error(name); results.push(name);}
  if(phase===1) {
   const win=Zotero.getMainWindow();
   const diag={...ctx,AbortController:win.AbortController,Response:win.Response,DOMException:win.DOMException,Blob:win.Blob,TextEncoder:win.TextEncoder,setTimeout:win.setTimeout.bind(win),clearTimeout:win.clearTimeout.bind(win)};
   let bodyCancelled=false;
   diag.fetch=async()=>new win.Response(new win.ReadableStream({cancel(){bodyCancelled=true;}}));
   Services.scriptloader.loadSubScript((rootURI || resourceURI.spec)+'diagnostics.js',diag);
   let timeout=false;
   try {await diag.smokeModule.diagnosticFetch('http://isolated.invalid/tasks',{}, {totalMs:1000,idleMs:50});} catch(e) {timeout=e.name==='TimeoutError';}
   check(timeout && bodyCancelled,'native stalled response body times out and cancels');
   diag.fetch=async()=>new win.Response('{"tasks":[]}',{headers:{'Content-Type':'application/json'}});
   const response=await diag.smokeModule.diagnosticFetch('http://isolated.invalid/tasks');
   check(Array.isArray((await response.json()).tasks),'native JSON fetch recovers after timeout');
  }
  let info;
  if(phase===1) {
   const parent=new Zotero.Item('journalArticle'); parent.setField('title','Isolated import verification');
   await parent.saveTx();
   const options={outputKey:'smoke-output-1',file:work+'/input.pdf',libraryID:parent.libraryID,parentItemID:parent.id,title:'Stable translated title',isCurrent:()=>true};
   const [a,b]=await Promise.all([api.importTaskOutput(options),api.importTaskOutput(options)]);
   check(a.id===b.id,'concurrent imports share one attachment');
   check(parent.getAttachments().length===1,'only one child attachment committed');
   check(await a.fileExists(),'stored PDF exists');
   a.setNote('<p>Preserve this user note.</p>'); await a.saveTx();
   const before=await Zotero.DB.valueQueryAsync('SELECT COUNT(*) FROM items');
   const query=Zotero.DB.queryAsync;
   Zotero.DB.queryAsync=async function(sql,params,...rest) {
    if(sql.startsWith('INSERT INTO pdf2zhOutputImports') && params[0]==='smoke-rollback') throw new Error('injected receipt failure');
    return query.call(this,sql,params,...rest);
   };
   let failed=false;
   try {await api.importTaskOutput({...options,outputKey:'smoke-rollback'});} catch(e) {failed=/injected receipt failure/.test(e.message);} finally {Zotero.DB.queryAsync=query;}
   check(failed,'receipt write failure propagated');
   check((await Zotero.DB.valueQueryAsync('SELECT COUNT(*) FROM items'))===before,'real SQLite transaction rolls back attachment');
   check(!(await api.findTaskOutput('smoke-rollback')),'rolled back receipt absent');
   check(await a.fileExists(),'rollback preserves prior attachment file');
   info={options,itemID:a.id,key:a.key};
   await IOUtils.writeUTF8(work+'/receipt.json',JSON.stringify(info));
  } else {
   info=JSON.parse(await IOUtils.readUTF8(work+'/receipt.json')); info.options.isCurrent=()=>true;
   const a=await api.findTaskOutput(info.options.outputKey);
   check(a.id===info.itemID,'fresh process finds durable receipt');
   const b=await api.importTaskOutput({...info.options,title:'Changed naming preference'});
   check(b.id===info.itemID,'restart import reuses committed attachment');
   check(b.getField('title')==='Stable translated title','retry preserves existing title');
   check(b.getNote().includes('Preserve this user note.'),'retry preserves user note');
   check((await Zotero.DB.valueQueryAsync('SELECT COUNT(*) FROM pdf2zhOutputImports'))===1,'one durable receipt after restart');
  }
  await IOUtils.writeUTF8(report+'/phase-'+phase+'.json',JSON.stringify({passed:true,version:Zotero.version,results}));
 } catch(error) {
  await IOUtils.writeUTF8(report+'/failure.json',JSON.stringify({message:String(error),stack:error.stack}));
 } finally { Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit); }
}
'''


def run(work, report):
    profile, library = work/'profile', work/'library'
    (profile/'extensions').mkdir(parents=True)
    library.mkdir()
    write_profile(profile, library, {"extensions.zotero.debug.log": True})
    manifest = {'manifest_version': 2, 'name': 'Isolated task import test', 'version': '1.0',
                'applications': {'zotero': {'id': 'task-import-smoke@pdf2zh.invalid', 'update_url': 'https://example.invalid/update.json', 'strict_min_version': '8.0', 'strict_max_version': '10.0.*'}}}
    with zipfile.ZipFile(profile/'extensions/task-import-smoke@pdf2zh.invalid.xpi', 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('bootstrap.js', bootstrap.replace('WORK', json.dumps(str(work))).replace('REPORT', json.dumps(str(report))))
        for filename, module in [('store.js', 'taskOutputStore'), ('diagnostics.js', 'diagnostics')]:
            code = bundle_module(f"export * from './src/modules/{module}';")
            archive.writestr(filename, code)
    create_test_pdf(work / 'input.pdf', 'Isolated import test')
    for phase in (1, 2):
        (work/'phase.json').write_text(str(phase))
        run_phase(work, report, f'zotero-{phase}.log', f'phase-{phase}.json')


if __name__ == "__main__":
    with Workspace().run("zotero-import-smoke") as (work, report):
        print(f"验收报告 {report}", flush=True)
        run(work, report)
