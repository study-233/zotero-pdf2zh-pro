import { getPref, setPref } from "../utils/prefs";
import {
    migrateProfiles,
    selectedProfile,
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
    if (getPref("profileSchemaVersion") !== 1) {
        const oldService = getPref("service")?.toString() || "siliconflowfree";
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
    return profiles;
}
export function saveProfiles(profiles: LLMApiData[]) {
    setPref("llmApis", JSON.stringify(profiles));
}
export function getSelectedProfile(): LLMApiData | null {
    const profiles = loadProfiles();
    return selectedProfile(
        profiles,
        getPref("selectedApiKey")?.toString() || "",
    );
}
export function removeProfile(key: string) {
    saveProfiles(loadProfiles().filter((api) => api.key !== key));
    if (getPref("selectedApiKey") === key) setPref("selectedApiKey", "");
}
