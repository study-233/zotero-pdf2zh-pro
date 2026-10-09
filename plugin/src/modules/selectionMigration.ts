import { config } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";

/** Bootstrap reasons distinguish a new install from an old implicit default. */
export function migrateSelectionPreferences(reason?: number) {
    if (Number(getPref("selectionPreferenceVersion") || 0) >= 1) return;
    const key = `${config.prefsPrefix}.selectionDictionary`;
    if (!Services.prefs.prefHasUserValue(key))
        setPref("selectionDictionary", reason === 5 ? "youdao" : "ecdict");
    setPref("selectionPreferenceVersion", 1);
}
