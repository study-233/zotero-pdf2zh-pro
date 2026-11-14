"""Built XPI in a disposable Zotero profile, with real HTTP and injected storage faults."""
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import zipfile
from workspace import ROOT, Workspace
from zotero_acceptance import create_test_pdf, run_phase, write_profile

BOOTSTRAP = r'''
function install() {}
function uninstall() {}
function shutdown() {}
function startup() { void run(); }
async function run() {
 const work=WORK, report=REPORT, url=SERVER, phase=PHASE;
 let dialog;
 try {
  await Zotero.initializationPromise;
  const wait=async(fn)=>{for(let n=0;n<200;n++){if(fn())return;await Zotero.Promise.delay(100);}throw new Error('wait timed out');};
  await wait(()=>Zotero.getMainWindow() && Zotero.pdf2zhpro?.data.locale?.current);
  if(Zotero.DataDirectory.dir!==work+'/library')throw new Error('Wrong isolated library');
  const win=Zotero.getMainWindow(), checks=[];
  const check=(ok,name)=>{if(!ok)throw new Error(name);checks.push(name);};
  const request=async(path,options={})=>{const response=await win.fetch(url+path,options);return {status:response.status,data:await response.json()};};
  Zotero.pdf2zhpro.hooks.onDialogEvents('openTaskManager');
  await wait(()=>{for(const w of Services.wm.getEnumerator(null)){if(w.location?.href.includes('/taskManager.xhtml')){dialog=w;return true;}}});
  const api=dialog.arguments[0];
  await api._initPromise.promise;
  await api.refreshTasks();
  const key='extensions.zotero.pdf2zhpro.taskBindings';
  if(phase===1) {
   check(api.getServerNotice()?.id==='error-task-bindings','built plugin exposes persistent damaged-binding notice');
   check(Zotero.Prefs.get(key,true)==='{"truncated":','damaged native preference remains byte-identical');
   check(api.getTasks().some(t=>t.taskId==='memory'),'remote task inspection remains available');
   let blocked=false;try{await api.retryTask('retry');}catch(e){blocked=e.code==='task_bindings_unavailable';}
   check(blocked,'built task operation rejects unsafe local binding mutation');
   await Zotero.Promise.delay(5500);
   check(Zotero.Prefs.get(key,true)==='{"truncated":','fallback polling does not erase damaged bindings');
   check(!dialog.document.getElementById('server-notice').hidden,'recovery notice is visible in actual task window');
   const parent=new Zotero.Item('journalArticle');parent.setField('title','Isolated reliability test');await parent.saveTx();
   const input=await Zotero.Attachments.importFromFile({file:work+'/input.pdf',parentItemID:parent.id});
   await IOUtils.writeUTF8(work+'/attachment.json',JSON.stringify({itemID:input.id,parentID:parent.id}));
  } else {
   check(!api.getServerNotice(),'restored native preference removes blocked-store notice after restart');
   await wait(()=>api.getTasks().find(t=>t.taskId==='memory')?.importState==='imported');
   const task=api.getTasks().find(t=>t.taskId==='memory');
   check(task.status==='completed','memory failure never changes successful PDF task to failed');
   const info=JSON.parse(await IOUtils.readUTF8(work+'/attachment.json'));
   const parent=await Zotero.Items.getAsync(info.parentID);
   check(parent.getAttachments().length===2,'actual Zotero database contains one source and one imported PDF');
   if(phase===2) {
    check(task.translationMemorySync==='pending','pending sync state reaches built plugin');
    check(dialog.document.getElementById('task-list').textContent.includes('同步') || dialog.document.getElementById('task-list').textContent.includes('sync'),'actual task card shows pending-sync explanation');
    await request('/audit/storage?fail=1');
    for(const [name,action] of [['retry',()=>api.retryTask('retry')],['delete',()=>api.deleteTask('delete')],['clear',()=>api.clearFailedTasks()],['cancel',()=>api.cancelTask('cancel')]]) {
     let failed=false;try{await action();}catch(e){failed=e.code==='task_persistence_failed';}
     check(failed,name+' returns structured disk-write failure through native plugin HTTP');
    }
    const disk=JSON.parse(await IOUtils.readUTF8(work+'/server/tasks.json'));
    check(disk.tasks.some(t=>t.task_id==='delete'),'failed deletion keeps durable record');
    check(await IOUtils.exists(work+'/server/delete/result.pdf'),'failed deletion keeps output file');
    check(disk.tasks.find(t=>t.task_id==='retry').attempt===1,'failed retry does not advance durable attempt');
    const live=(await request('/tasks')).data.tasks;
    check(live.find(t=>t.taskId==='cancel').status==='cancelled','failed cancellation save still stops work');
    await request('/audit/storage?fail=0');
    await api.cancelTask('cancel');
    await api.deleteTask('delete');
    const saved=JSON.parse(await IOUtils.readUTF8(work+'/server/tasks.json'));
    check(saved.tasks.find(t=>t.task_id==='cancel').cancel_requested,'repeated cancellation persists stopped state');
    check(!saved.tasks.some(t=>t.task_id==='delete'),'successful delete commits record removal');
    for(let n=0;n<20;n++) {await request('/tasks/query',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"limit":30}'});await Zotero.Promise.delay(50);}
    const token=await IOUtils.readUTF8(work+'/server/.control-token');
    const headers={'Content-Type':'application/json','X-PDF2ZH-Control-Token':token.trim()};
    let maintenance=await request('/internal/maintenance',{method:'POST',headers,body:'{"idleSeconds":60}'});
    check(maintenance.status===200,'continuous real dashboard POST polling allows idle maintenance');
    check((await request('/tasks/query',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status===200,'read-only polling remains available during maintenance');
    check((await request('/tasks/retry/retry',{method:'POST'})).status===503,'maintenance still blocks task mutations');
    await request('/internal/maintenance',{method:'DELETE',headers});
   } else {
    check(task.translationMemorySync==='synced','service restart clears pending memory sync');
    check(parent.getAttachments().length===2,'plugin restart does not duplicate imported result');
    check(!api.getTasks().some(t=>t.taskId==='delete'),'deleted task does not return after server restart');
    check(api.getTasks().find(t=>t.taskId==='cancel')?.status==='cancelled','persisted cancellation stays cancelled on restart');
   }
  }
  await IOUtils.writeUTF8(report+'/phase-'+phase+'.json',JSON.stringify({passed:true,version:Zotero.version,checks,taskWindowText:dialog.document.body.textContent.slice(-16000)}));
 }catch(error){await IOUtils.writeUTF8(report+'/failure.json',JSON.stringify({phase,message:String(error),stack:error.stack}));}
 finally{dialog?.close();Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);}
}
'''


def run(work, report):
    http = None
    manager = None
    memory_fault=ExitStack()
    try:
        os.environ['PDF2ZH_DATA_DIR']=str(work/'server')
        sys.path.insert(0,str(ROOT/'server'))
        import server
        from task_manager import TaskManager,TaskRecord
        from pdf2zh_pro import pdf
        from pdf2zh_pro.types import TranslationOutputFile
        from translation_memory import TranslationMemory,document_fingerprint
        from maintenance import control_token
        from werkzeug.serving import make_server
        create_test_pdf(work / 'input.pdf', 'Isolated PDF translation and import test.')
        manager=server.TASK_MANAGER
        manager._start_worker_locked=lambda:None
        manager._translator=pdf.translate_pdf_with_callbacks
        for name,state in [('retry','failed'),('delete','completed'),('cancel','queued')]:
            directory=work/'server'/name;directory.mkdir(parents=True)
            shutil.copyfile(work/'input.pdf',directory/'input.pdf');shutil.copyfile(work/'input.pdf',directory/'result.pdf')
            row=TaskRecord(name,'input.pdf','openai',['dual'],{'input_path':str(directory/'input.pdf'),'output_dir':str(directory/'output')},directory,status=state,
                result_files={'dual':TranslationOutputFile('dual',directory/'result.pdf','result.pdf')})
            manager._tasks[name]=row
        directory=work/'server/memory';directory.mkdir();shutil.copyfile(work/'input.pdf',directory/'input.pdf')
        payload={'input_path':str(directory/'input.pdf'),'output_dir':str(directory/'output'),'output_modes':['dual'],'service':'openai'}
        manager.create_task(task_id='memory',file_name='input.pdf',service='openai',output_modes=['dual'],request_payload=payload,workspace_dir=directory)
        config=SimpleNamespace(qps=2,pool_max_workers=1,translator=SimpleNamespace(),cancel_translation=lambda:None,cleanup_temp_files=lambda:None)
        async def translate(config):
            with config.recovery.connection:
                config.recovery._upsert('p',{'source':'Isolated source sentence.','translation':'隔离测试译文。','status':'succeeded','page':1,'paragraphId':'p'})
            yield {'type':'finish','translate_result':SimpleNamespace()}
        async def fonts(**kwargs):pass
        def output(*args):
            shutil.copyfile(work/'input.pdf',directory/'translated.pdf')
            return {'dual':TranslationOutputFile('dual',directory/'translated.pdf','translated.pdf')}
        # Keep retries in the first backend under the same fault until it shuts down.
        memory_fault.enter_context(patch('translation_memory.sync_recovery',
                                         side_effect=sqlite3.OperationalError('injected locked memory')))
        with (patch.object(pdf,'create_runtime_settings',return_value=object()),
              patch.object(pdf,'create_babeldoc_config',return_value=config),
              patch.object(pdf,'babeldoc_translate',side_effect=translate),
              patch.object(pdf,'download_all_fonts_async',side_effect=fonts),
              patch.object(pdf,'collect_output_files',side_effect=output)):
            manager._run_task('memory',1)
        assert manager.get_task('memory')['status']=='completed'
        control_token(work/'server')
        storage_fault = False
        original_write=manager._write_snapshot
        def write(path,content):
            if storage_fault:
                raise OSError('injected disk full')
            original_write(path,content)
        manager._write_snapshot=write
        @server.app.get('/audit/storage')
        def storage():
            from flask import request
            nonlocal storage_fault
            storage_fault = request.args.get('fail') == '1'
            return {'failed': storage_fault}
        gate=server.app.extensions['update_maintenance']
        gate._clock=lambda:time.monotonic()*100
        gate._last_active=gate._clock()
        http=make_server('127.0.0.1',0,server.app,threaded=True);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{http.server_port}'
        profile=work/'profile';library=work/'library';(profile/'extensions').mkdir(parents=True);library.mkdir()
        shutil.copyfile(ROOT/'plugin/build/zotero-pdf2zh-pro.xpi',profile/'extensions/zotero-pdf2zh-pro@study-233.xpi')
        base = {
            'extensions.zotero.pdf2zhpro.new_serverip': url,
            'extensions.zotero.pdf2zhpro.taskBindingsBackup': '[]',
            'extensions.zotero.pdf2zhpro.notifyOnTranslationComplete': False,
        }
        manifest={'manifest_version':2,'name':'Isolated reliability acceptance','version':'1.0','applications':{'zotero':{'id':'reliability-smoke@pdf2zh.invalid','update_url':'https://example.invalid/update.json','strict_min_version':'8.0','strict_max_version':'10.0.*'}}}
        for phase in (1,2,3):
            prefs=dict(base)
            if phase==1:prefs['extensions.zotero.pdf2zhpro.taskBindings']='{"truncated":'
            if phase==2:
                attachment=json.loads((work/'attachment.json').read_text())
                prefs['extensions.zotero.pdf2zhpro.taskBindings']=json.dumps([{**manager.get_task('memory'),'itemID':attachment['itemID'],'serverUrl':url,'source':'local','importState':'pending','targetLang':'zh-CN'}])
            if phase==3:
                manager.close()
                manager.diagnostics.flush(timeout=2)
                memory_fault.close()
                manager=TaskManager(work/'server/tasks.json');manager._start_worker_locked=lambda:None
                server.TASK_MANAGER=manager
                deadline = time.monotonic() + 10
                while manager.get_task('memory')['translationMemorySync'] != 'synced':
                    if time.monotonic() >= deadline:
                        raise AssertionError('Background translation memory sync did not finish after restart')
                    time.sleep(0.05)
                match = TranslationMemory(work/'server/translation-memory.sqlite3').lookup(
                    document_fingerprint(directory/'input.pdf'), 'Isolated source sentence.')
                assert match['matched'] and match['translation'] == '隔离测试译文。'
            write_profile(profile, library, prefs)
            with zipfile.ZipFile(profile/'extensions/reliability-smoke@pdf2zh.invalid.xpi','w') as archive:
                archive.writestr('manifest.json',json.dumps(manifest))
                archive.writestr('bootstrap.js',BOOTSTRAP.replace('WORK',json.dumps(str(work))).replace('REPORT',json.dumps(str(report))).replace('SERVER',json.dumps(url)).replace('PHASE',str(phase)))
            run_phase(work, report, f'zotero-phase-{phase}.log', f'phase-{phase}.json')
    finally:
        if http is not None:
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)
        if manager is not None:
            manager.close()
            manager.diagnostics.flush(timeout=2)
        memory_fault.close()


if __name__ == "__main__":
    with Workspace().run("zotero-four-reliability-smoke") as (work, report):
        print(f"验收报告 {report}", flush=True)
        run(work, report)
