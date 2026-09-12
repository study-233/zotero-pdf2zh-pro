import { getPref, setPref } from "../utils/prefs";

/** Apply the opt-in default once, before menus or preferences become available. */
export function migrateReviewPreference() {
    if (Number(getPref("reviewPreferenceMigrationVersion") ?? 0) >= 1) return;
    setPref("semanticReview", false);
    // Only mark completion after the preference write succeeds.
    setPref("reviewPreferenceMigrationVersion", 1);
}
