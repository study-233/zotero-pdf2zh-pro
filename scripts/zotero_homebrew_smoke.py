"""Read-only native pane acceptance on a Mac with the official Brew backend installed."""
import json
import shutil
import zipfile

from workspace import ROOT, Workspace
from zotero_acceptance import run_phase, write_profile


HARNESS = r"""
function install() {}
function uninstall() {}
function shutdown() {}
function startup() { void run(); }
async function run() {
    const results = [], report = REPORT;
    const wait = async (label, fn) => {
        for (let i=0; i<300; i++) {
            const value = fn();
            if (value) return value;
            await Zotero.Promise.delay(100);
        }
        throw Error(label);
    };
    let win;
    try {
        await Zotero.uiReadyPromise;
        const plugin = await wait('plugin load', () => Zotero.pdf2zhpro?.data.locale && Zotero.pdf2zhpro);
        Zotero.Utilities.Internal.openPreferences('pdf2zhpro-preferences');
        const key = 'zotero-prefpane-pdf2zhpro-';
        win = await wait('preferences window', () => plugin.data.prefs?.window);
        const doc = win.document, el = name => doc.getElementById(key + name);
        await wait('Brew detection', () => el('brew-panel') && !el('brew-panel').hidden);
        const card = el('brew-panel').closest('[data-pref-card="connection"]');
        card.open = true;
        await wait('localized action', () => el('brew-update').textContent.trim());
        const diagnostics = el('brew-troubleshooting');
        if (el('brew-follow').checked || el('connection-options').open || diagnostics.open)
            throw Error('default controls');
        await wait('header service status', () => !el('brew-status').hidden && el('brew-status').textContent.trim());
        if (el('brew-match').hidden === false && !el('brew-message').hidden)
            throw Error('duplicated version status');
        if (el('export-diagnostics').parentElement !== el('brew-tools')) throw Error('diagnostic action missing');
        if (!el('brew-path').textContent.includes('zotero-pdf2zh-pro')) throw Error('data path missing');
        const prefWin = win.docShell.rootTreeItem.domWindow;
        for (const width of [820, 1080]) {
            prefWin.resizeTo(width, 740);
            card.scrollIntoView({block:'center'});
            await Zotero.Promise.delay(300);
            const action = el('brew-update'), box = card.getBoundingClientRect();
            if (action.getBoundingClientRect().right > box.right + 1 || card.scrollWidth > card.clientWidth+1)
                throw Error('overflow at ' + width);
            action.focus();
            if (!action.disabled && doc.activeElement !== action) throw Error('upgrade keyboard focus');
            const frame = win.document.createElementNS('http://www.w3.org/1999/xhtml','canvas');
            frame.width = Math.ceil(box.width); frame.height = Math.ceil(box.height);
            frame.getContext('2d').drawWindow(win, win.scrollX+box.left, win.scrollY+box.top, frame.width, frame.height, 'rgb(245,245,245)');
            const bytes = Uint8Array.from(win.atob(frame.toDataURL().split(',')[1]), c=>c.charCodeAt(0));
            await IOUtils.write(report + '/'+LOCALE+'-'+width+'.png',bytes);
            results.push({locale:Zotero.locale,dark:win.matchMedia('(prefers-color-scheme: dark)').matches,
                window:prefWin.outerWidth, card:box.width, action:action.textContent, installed:el('brew-version').textContent});
        }
        diagnostics.open = true;
        for (const id of ['brew-path', 'brew-copy', 'export-diagnostics'])
            if (!el(id).getBoundingClientRect().width) throw Error('missing troubleshooting control: ' + id);
        diagnostics.open = false;
        el('connection-options').open = true;
        if (!el('new_serverip').getBoundingClientRect().width) throw Error('custom URL inaccessible');
        Zotero.Prefs.set('extensions.zotero.pdf2zhpro.new_serverip','http://127.0.0.1:1',true);
        await wait('custom connection view', () => el('brew-panel').hidden && el('connection-options').open);
        if (!el('brew-status').hidden || !diagnostics.hidden) throw Error('stale Homebrew controls');
        if (el('export-diagnostics').parentElement === el('brew-tools')) throw Error('external diagnostics missing');
        await IOUtils.writeUTF8(report+'/'+LOCALE+'.json', JSON.stringify({passed:true,version:Zotero.version,results},null,2));
    } catch(e) {
        await IOUtils.writeUTF8(report+'/failure.json',JSON.stringify({error:String(e),stack:e.stack}));
    } finally { Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit); }
}
"""


def main(work, report):
    library = work / "library"
    profile = work / "profile"
    extensions = profile / "extensions"
    extensions.mkdir(parents=True)
    library.mkdir()
    shutil.copyfile(ROOT / "plugin/build/zotero-pdf2zh-pro.xpi", extensions / "zotero-pdf2zh-pro@study-233.xpi")
    addon = "brew-pane-smoke@pdf2zh.invalid"
    manifest = {"manifest_version": 2, "name": "Brew preferences acceptance", "version": "1.0",
                "applications": {"zotero": {"id":addon, "update_url":"https://example.invalid/update.json",
                                             "strict_min_version":"8.0","strict_max_version":"10.0.*"}}}
    for locale, dark in [("zh-CN", 1), ("en-US", 0)]:
        write_profile(profile, library, {"intl.locale.requested":locale, "ui.systemUsesDarkTheme":dark,
            "extensions.zotero.pdf2zhpro.engineManagement":"external",
            "extensions.zotero.pdf2zhpro.new_serverip":"http://127.0.0.1:8890",
            "extensions.zotero.pdf2zhpro.brewFollowPlugin":False})
        with zipfile.ZipFile(extensions / (addon + ".xpi"), "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("bootstrap.js", HARNESS.replace("REPORT", json.dumps(str(report))).replace("LOCALE", json.dumps(locale)))
        run_phase(work, report, locale + ".log", locale + ".json")


if __name__ == "__main__":
    with Workspace().run("brew-preferences") as (work, report):
        print(report, flush=True)
        main(work, report)
