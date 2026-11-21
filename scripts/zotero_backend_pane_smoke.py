"""Exercise the packaged management pane in real Zotero against isolated health fixtures."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import threading
import zipfile

from workspace import ROOT, Workspace
from zotero_acceptance import ZOTERO, bundle_module, write_profile


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'status':'ok','version':self.server.version,'managedOwner':'pane-fixture',
                           'capabilities':{},'maintenance':{'busy':False}}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(b'{"tasks":[]}')

    def log_message(self, *args):
        pass


HARNESS = r"""
function install(){} function uninstall(){} function shutdown(){}
function startup({rootURI}){void run(rootURI);}
async function run(rootURI){
    const result={passed:false,checks:[],screens:[]};
    const wait=async(name,f)=>{
        for(let i=0;i<250;i++){if(f())return;await Zotero.Promise.delay(100);}
        throw Error(name);
    };
    const check=(name,ok)=>{if(!ok)throw Error(name);result.checks.push(name);};
    const observer={observe(subject){
        const http=subject.QueryInterface(Ci.nsIHttpChannel);
        if(http.URI.prePath==="http://127.0.0.1:8890")http.redirectTo(Services.io.newURI(ORIGIN+http.URI.pathQueryRef));
    }};
    Services.obs.addObserver(observer,"http-on-modify-request");
    try {
        await Zotero.uiReadyPromise;
        await wait("plugin",()=>Zotero.pdf2zhpro?.data.locale);
        Zotero.Prefs.set("extensions.zotero.pdf2zhpro.new_serverip","http://127.0.0.1:8890",true);
        Zotero.Utilities.Internal.openPreferences("pdf2zhpro-preferences");
        await wait("pane",()=>Zotero.pdf2zhpro.data.prefs?.window);
        const win=Zotero.pdf2zhpro.data.prefs.window;
        const el=id=>win.document.getElementById("zotero-prefpane-pdf2zhpro-"+id);
        await wait("owned backend",()=>el("backend-panel") && !el("backend-panel").hidden && !el("backend-update").hidden && el("backend-source").textContent.trim());
        const card=el("backend-panel").closest("[data-pref-card=connection]");card.open=true;
        check("managed label",el("backend-source").textContent.trim()===EXPECTED);
        check("bound installation folder",el("backend-location").textContent.endsWith("pdf2zh-engine"));
        check("bound data folder",el("backend-data").textContent.endsWith("data"));
        check("available update",!el("backend-update").hidden && el("backend-install").hidden && el("backend-center").hidden);
        check("follow control",!el("backend-follow-row").hidden && el("backend-follow").checked);
        el("backend-follow").click();
        check("follow preference persisted",Zotero.Prefs.get("extensions.zotero.pdf2zhpro.managedFollowPlugin",true)===false);
        el("checkConnection").click();
        await wait("recheck",()=>!el("checkConnection").disabled && !el("backend-update").hidden);
        check("connection check left update available",!el("backend-update").hidden);
        const top=win.docShell.rootTreeItem.domWindow;
        for(const width of [820,1080]){
            top.resizeTo(width,740);card.scrollIntoView({block:"center"});await Zotero.Promise.delay(250);
            const box=card.getBoundingClientRect();
            check("layout "+width,box.width>200 && card.scrollWidth<=card.clientWidth+1 && el("backend-update").getBoundingClientRect().right<=box.right+1);
            const canvas=win.document.createElementNS("http://www.w3.org/1999/xhtml","canvas");
            canvas.width=Math.ceil(box.width);canvas.height=Math.ceil(box.height);
            canvas.getContext("2d").drawWindow(win,win.scrollX+box.left,win.scrollY+box.top,canvas.width,canvas.height,win.matchMedia("(prefers-color-scheme: dark)").matches?"#202020":"white");
            await IOUtils.write(PathUtils.join(REPORT,LANG+"-"+width+".png"),Uint8Array.from(win.atob(canvas.toDataURL().split(",")[1]),c=>c.charCodeAt(0)));
            result.screens.push({width:top.outerWidth,card:box.width});
        }
        Zotero.Prefs.set("extensions.zotero.pdf2zhpro.new_serverip",ORIGIN,true);
        el("checkConnection").click();
        await wait("external replaces managed",()=>el("backend-source").textContent.trim()===EXTERNAL && el("backend-update").hidden && el("backend-follow-row").hidden);
        result.checks.push("external endpoint has no install or update action");
        const sandbox={Zotero, Services, Ci, Cc, IOUtils, PathUtils};
        Services.scriptloader.loadSubScript(rootURI+"locations.js",sandbox);
        const api=sandbox.smokeModule;
        const selected=PathUtils.join(CUSTOM,"自选 install");
        await api.bindNewEngine(selected);
        const binding=await api.engineBinding();
        check("custom installation bound to profile",binding.root===selected && binding.owner===await IOUtils.readUTF8(PathUtils.join(selected,"owner")));
        const migration=PathUtils.join(CUSTOM,"move");
        await api.reserveMigration(selected,migration);
        await api.reserveMigration(selected,migration);
        check("same migration destination reusable",await IOUtils.exists(PathUtils.join(migration,".pdf2zh-relocation.json")));
        let refused=false;
        try{await api.reserveMigration(selected,PathUtils.join(selected,"child"));}catch{refused=true;}
        check("overlapping migration rejected before writing",refused && !(await IOUtils.exists(PathUtils.join(selected,"child"))));
        result.version=Zotero.version;result.locale=Zotero.locale;result.dark=win.matchMedia("(prefers-color-scheme: dark)").matches;result.passed=true;
    }catch(e){result.error=String(e);result.stack=e.stack;}
    finally{
        Services.obs.removeObserver(observer,"http-on-modify-request");
        await IOUtils.writeUTF8(PathUtils.join(REPORT,LANG+".json"),JSON.stringify(result,null,2));
        Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);
    }
}
"""


def run(work, report, xpi):
    host = ThreadingHTTPServer(('127.0.0.1', 0),Fixture)
    with zipfile.ZipFile(xpi) as archive:
        host.version = json.loads(archive.read('manifest.json'))['version']
    thread = threading.Thread(target=host.serve_forever,daemon=True)
    thread.start()
    try:
        locations = bundle_module('export {bindNewEngine, engineBinding, reserveMigration} from "./src/modules/backendInstallation"')
        for locale, dark, label, external in [('zh-CN',1,'插件托管引擎','外部服务'),('en-US',0,'Plugin-managed engine','External service')]:
            profile = work/locale/'profile'
            library = work/locale/'library'
            extensions = profile/'extensions'
            extensions.mkdir(parents=True)
            library.mkdir()
            root = profile/'pdf2zh-engine'
            root.mkdir()
            (root/'owner').write_text('pane-fixture')
            (root/'active').write_text('0'*64)
            write_profile(profile,library,{'intl.locale.requested':locale,'ui.systemUsesDarkTheme':dark,
                          'extensions.zotero.pdf2zhpro.new_serverip':'http://127.0.0.1:1',
                          'extensions.zotero.pdf2zhpro.engineManagement':'external'})
            shutil.copyfile(xpi,extensions/'zotero-pdf2zh-pro@study-233.xpi')
            addon='backend-pane-smoke@pdf2zh.invalid'
            js=HARNESS
            for k,v in {'ORIGIN':f'http://127.0.0.1:{host.server_port}','REPORT':str(report),'LANG':locale,'EXPECTED':label,'EXTERNAL':external,'CUSTOM':str(work/locale)}.items():
                js=js.replace(k,json.dumps(v))
            with zipfile.ZipFile(extensions/(addon+'.xpi'),'w') as z:
                z.writestr('manifest.json',json.dumps({'manifest_version':2,'name':'Backend pane acceptance','version':'1.0',
                        'applications':{'zotero':{'id':addon,'update_url':'https://example.invalid/updates.json','strict_min_version':'8.0','strict_max_version':'10.0.*'}}}))
                z.writestr('bootstrap.js',js)
                z.writestr('locations.js',locations)
            cmd=[os.environ.get('ZOTERO_BIN') or ZOTERO]
            if os.name=='nt':cmd.append('-wait-for-browser')
            with (report/(locale+'.log')).open('w') as log:
                subprocess.run([*cmd,'-no-remote','-profile',profile,'--dataDir',library],env={**os.environ,'MOZ_NO_REMOTE':'1'},
                               stdout=log,stderr=subprocess.STDOUT,timeout=105,check=True)
            result=json.loads((report/(locale+'.json')).read_bytes())
            if not result['passed']:raise RuntimeError(result)
            print(locale,result['checks'],flush=True)
    finally:
        host.shutdown()
        host.server_close()
        thread.join()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xpi', type=Path, default=ROOT/'plugin/build/zotero-pdf2zh-pro.xpi')
    args = parser.parse_args()
    with Workspace().run('backend-pane') as (work,report):
        print(report,flush=True)
        run(work,report,args.xpi)
