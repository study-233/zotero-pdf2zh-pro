import { getPref, setPref } from "../utils/prefs";
import {
    migrateProfiles,
    isRemovedService,
    selectedProfile,
    resolveProviderPreset,
    type LLMApiData,
} from "./llmApiManager";

export function loadProfiles(): LLMApiData[] {
    const raw = getPref("llmApis")?.toString() || "[]";
    let profiles: LLMApiData[];
    try {
        const parsed: unknown = JSON.parse(raw);
        if (
            !Array.isArray(parsed) ||
            parsed.some(
                (api) =>
                    !api ||
                    typeof api.key !== "string" ||
                    typeof api.service !== "string",
            )
        )
            throw new Error();
        profiles = parsed;
    } catch {
        throw new Error("翻译配置无法读取，请检查配置备份；原数据未修改。");
    }
    if (Number(getPref("profileSchemaVersion") || 0) < 1) {
        const oldService = getPref("service")?.toString() || "";
        if (!getPref("llmApisLegacyBackup")) {
            setPref(
                "llmApisLegacyBackup",
                JSON.stringify({
                    llmApis: raw,
                    service: oldService,
                    selectedApiKey: getPref("selectedApiKey") || "",
                }),
            );
        }
        const migrated = migrateProfiles(profiles, oldService);
        saveProfiles(migrated.profiles);
        setPref("selectedApiKey", migrated.selectedApiKey);
        setPref("profileSchemaVersion", 1);
        profiles = migrated.profiles;
    }
    if (Number(getPref("profileSchemaVersion") || 0) < 3) {
        const removedKeys = new Set(
            profiles
                .filter((api) => isRemovedService(api.service))
                .map((api) => api.key),
        );
        profiles = profiles.filter((api) => !isRemovedService(api.service));
        saveProfiles(profiles);
        if (removedKeys.has(getPref("selectedApiKey")?.toString() || ""))
            setPref("selectedApiKey", "");
        if (isRemovedService(getPref("service")?.toString() || ""))
            setPref("service", "");
        if (isRemovedService(getPref("serviceSelect")?.toString() || ""))
            setPref("serviceSelect", "");
        // Keep an explicit selection-model ID dangling so the existing missing
        // model notice asks for a choice instead of silently using another model.
        const backup = getPref("llmApisLegacyBackup")?.toString();
        if (backup) setPref("llmApisLegacyBackup", cleanLegacyBackup(backup));
        setPref("profileSchemaVersion", 3);
    }
    if (Number(getPref("profileSchemaVersion") || 0) < 4) {
        profiles = profiles.map((api) => {
            const preset = resolveProviderPreset(api);
            return preset ? { ...api, providerPreset: preset.id } : api;
        });
        saveProfiles(profiles);
        // Retired service rows and explicit references remain available for repair.
        setPref("profileSchemaVersion", 4);
    }
    return profiles;
}
export function saveProfiles(profiles: LLMApiData[]) {
    setPref("llmApis", JSON.stringify(profiles));
}
export function getSelectedProfile(key?: string): LLMApiData | null {
    const profiles = loadProfiles();
    return selectedProfile(
        profiles,
        key ?? (getPref("selectedApiKey")?.toString() || ""),
    );
}
export function removeProfile(key: string) {
    saveProfiles(loadProfiles().filter((api) => api.key !== key));
    if (getPref("selectedApiKey") === key) setPref("selectedApiKey", "");
}

// Leave malformed backups intact: deleting unrelated data cannot repair them.
function cleanLegacyBackup(raw: string): string {
    try {
        const backup = JSON.parse(raw);
        const rows = JSON.parse(backup.llmApis);
        if (
            !Array.isArray(rows) ||
            rows.some((row) => !row || typeof row.service !== "string")
        )
            return raw;
        const removedKeys = new Set(
            rows
                .filter((row) => isRemovedService(row.service))
                .map((row) => row.key),
        );
        const retained = rows.filter((row) => !isRemovedService(row.service));
        let changed = retained.length !== rows.length;
        if (changed) backup.llmApis = JSON.stringify(retained);
        if (removedKeys.has(backup.selectedApiKey)) {
            backup.selectedApiKey = "";
            changed = true;
        }
        if (
            typeof backup.service === "string" &&
            isRemovedService(backup.service)
        ) {
            backup.service = "";
            changed = true;
        }
        return changed ? JSON.stringify(backup) : raw;
    } catch {
        return raw;
    }
}
