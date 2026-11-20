import { config, version } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { recordDiagnostic } from "./diagnostics";

type Command = "status" | "refresh" | "manual" | "auto";
type View = {
    state: string;
    installed?: string;
    running?: string;
    directory?: string;
};
const active = ["stopping", "backup", "installing", "verifying", "restoring"];
const failure = ["failed", "restored", "interrupted", "recoveryFailed"];
let view: View = { state: "unavailable" };
let listener: (() => void) | undefined;
let running = false,
    stopped = true,
    revision = 0,
    busy = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let nextRefresh = 0;
let prepared: Promise<string> | undefined;
const observers: symbol[] = [];

function eligible() {
    return (
        !stopped &&
        Zotero.isMac &&
        getPref("engineManagement") !== "managed" &&
        /^http:\/\/(localhost|127\.0\.0\.1):8890\/?$/i.test(
            String(getPref("new_serverip") || ""),
        )
    );
}
function manual() {
    return getPref("brewManualTarget") === version;
}
function automatic() {
    return (
        !!getPref("brewFollowPlugin") && getPref("brewSkipTarget") !== version
    );
}
function update(value: View) {
    if (view.state !== value.state) {
        if (failure.includes(value.state))
            recordDiagnostic("brew_update_failed");
        if (value.state === "waiting") recordDiagnostic("brew_update_deferred");
    }
    view = value;
    listener?.();
}
async function prepare() {
    const script = PathUtils.join(
        Services.dirsvc.get("ProfD", Ci.nsIFile).path,
        "pdf2zh-brew-update.py",
    );
    const source = await Zotero.File.getContentsFromURLAsync(
        "chrome://pdf2zhpro/content/engine/homebrew_update.py",
    );
    await IOUtils.writeUTF8(script, String(source), {
        tmpPath: script + ".tmp",
    });
    return script;
}
async function helper(prefix: string, action: Command): Promise<View> {
    const script = await (prepared ??= prepare().catch((error) => {
        prepared = undefined;
        throw error;
    }));
    const { Subprocess } = ChromeUtils.importESModule(
        "resource://gre/modules/Subprocess.sys.mjs",
    );
    const child = await Subprocess.call({
        command: prefix + "/opt/python@3.13/bin/python3.13",
        arguments: ["-I", script, action, prefix, version],
        stderr: "stdout",
    });
    let text = "";
    for (;;) {
        const chunk = await child.stdout.readString();
        if (!chunk) break;
        text = (text + chunk).slice(-8192);
    }
    if ((await child.wait()).exitCode !== 0)
        throw Error("Homebrew helper failed");
    return JSON.parse(text.trim());
}

function schedule(delay: number) {
    clearTimeout(timer);
    if (eligible()) timer = setTimeout(() => void run(), delay);
}
async function run(refresh = false) {
    if (running || !eligible()) return;
    running = true;
    clearTimeout(timer);
    const epoch = revision;
    const valid = () => epoch === revision && eligible();
    try {
        const prefixes = [];
        for (const p of ["/opt/homebrew", "/usr/local"]) {
            if (
                (await IOUtils.exists(p + "/opt/zotero-pdf2zh-pro")) ||
                (await IOUtils.exists(
                    p + "/var/.zotero-pdf2zh-pro-updater/state.json",
                ))
            )
                prefixes.push(p);
        }
        if (!valid()) return;
        if (prefixes.length !== 1) {
            update({ state: "unavailable" });
            return;
        }
        const prefix = prefixes[0];
        let result = await helper(prefix, "status");
        if (!valid()) return;
        if (
            result.state === "unpublished" &&
            (refresh ||
                ((automatic() || manual()) && Date.now() >= nextRefresh))
        ) {
            nextRefresh = Date.now() + 3600000;
            result = await helper(prefix, "refresh");
            if (!valid()) return;
        }
        if (
            (manual() && [...failure, "available"].includes(result.state)) ||
            (result.state === "interrupted" &&
                (automatic() || getPref("brewInFlight"))) ||
            (automatic() && result.state === "available")
        ) {
            busy = true;
            listener?.();
            const decision = await helper(prefix, manual() ? "manual" : "auto");
            result =
                decision.state === "checking"
                    ? { ...result, ...decision }
                    : decision;
        }
        if (!valid()) return;
        if (!result.installed && result.state === "failed" && view.installed)
            result = { ...view, state: "failed" };
        update(result);
        if (
            manual() &&
            !["waiting", "unpublished", "network", "checking"].includes(
                result.state,
            )
        )
            setPref("brewManualTarget", "");
        if (active.includes(result.state)) {
            setPref("brewInFlight", true);
            schedule(2000);
        } else if (result.state === "checking") schedule(2000);
        else if (result.state === "waiting") schedule(60000);
        else if (
            (automatic() || manual()) &&
            ["unpublished", "network", "unavailable"].includes(result.state)
        )
            schedule(3600000);
        if (
            !active.includes(result.state) &&
            result.installed &&
            getPref("brewInFlight")
        )
            setPref("brewInFlight", false);
    } catch {
        if (valid()) {
            update({ ...view, state: "failed" });
            if (manual()) setPref("brewManualTarget", "");
        }
    } finally {
        running = busy = false;
        listener?.();
        if (epoch !== revision && eligible()) schedule(0);
    }
}
export function startBrewUpdates() {
    stopped = false;
    if (!Zotero.isMac) return;
    for (const pref of ["new_serverip", "engineManagement"])
        observers.push(
            Zotero.Prefs.registerObserver(
                config.prefsPrefix + "." + pref,
                brewConnectionChanged,
                true,
            ),
        );
    if (listener || automatic() || manual() || getPref("brewInFlight"))
        schedule(0);
}
export function stopBrewUpdates() {
    stopped = true;
    revision++;
    clearTimeout(timer);
    for (const observer of observers.splice(0))
        Zotero.Prefs.unregisterObserver(observer);
    listener = undefined;
}
export function brewConnectionChanged() {
    revision++;
    clearTimeout(timer);
    if (!eligible()) {
        if (manual()) setPref("brewManualTarget", "");
        update({ state: "unavailable" });
    } else if (listener || manual() || automatic() || getPref("brewInFlight"))
        schedule(0);
}
export function observeBrewUpdates(callback: () => void) {
    listener = callback;
    callback();
    if (!running && !active.includes(view.state) && view.state !== "waiting")
        void run();
    return () => {
        if (listener === callback) listener = undefined;
    };
}
export function brewControls() {
    return {
        ...view,
        visible: eligible() && !!view.installed,
        disabled:
            busy || active.includes(view.state) || view.state === "checking",
        updating: active.includes(view.state),
        follow: !!getPref("brewFollowPlugin"),
        target: version,
        retry: failure.includes(view.state),
    };
}
export function followBrewUpdates(follow: boolean) {
    if (!eligible() || busy) return;
    setPref("brewFollowPlugin", follow);
    if (follow) setPref("brewSkipTarget", "");
    revision++;
    schedule(0);
    listener?.();
}
export function brewUpdateAction() {
    if (!eligible() || busy || active.includes(view.state)) return;
    if (view.state === "waiting") {
        setPref("brewManualTarget", "");
        setPref("brewSkipTarget", version);
        revision++;
        schedule(0);
        return;
    }
    if (view.state === "available" || failure.includes(view.state)) {
        setPref("brewManualTarget", version);
    }
    if (running) {
        revision++;
        return;
    }
    void run(true);
}
