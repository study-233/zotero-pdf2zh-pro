export interface ReleaseNote {
    id: string;
    message: string;
    cardMessage?: string;
    priority: number;
}
export interface ReleaseSummary {
    version: string;
    notes: ReleaseNote[];
}
export interface ReleaseNoticeState {
    version: string;
    shown: string[];
}

// Explicit versions make a forgotten release review detectable in CI.
// Archive this entry and add an empty entry for releases with no user-facing news.
export const releaseSummaries: ReleaseSummary[] = [
    {
        version: "1.9.1",
        notes: [
            {
                id: "chatgpt-login",
                message: "release-chatgpt",
                priority: 0,
            },
            {
                id: "selection-tabs",
                message: "release-selection",
                priority: 1,
            },
            {
                id: "task-feedback",
                message: "release-tasks",
                priority: 2,
            },
        ],
    },
    {
        version: "1.9.2",
        notes: [
            {
                id: "task-recovery",
                message: "release-task-recovery",
                priority: 0,
            },
            {
                id: "task-record-protection",
                message: "release-task-record-protection",
                priority: 1,
            },
            {
                id: "memory-background-sync",
                message: "release-memory-background-sync",
                priority: 2,
            },
        ],
    },
    {
        version: "1.9.3",
        notes: [
            {
                id: "task-settings-layout",
                message: "release-task-settings-layout",
                cardMessage: "release-card-task-settings-layout",
                priority: 0,
            },
            {
                id: "translated-attachment-tag",
                message: "release-translated-attachment-tag",
                cardMessage: "release-card-translated-attachment-tag",
                priority: 1,
            },
            {
                id: "diagnostic-request-evidence",
                message: "release-diagnostic-request-evidence",
                cardMessage: "release-card-diagnostic-request-evidence",
                priority: 2,
            },
        ],
    },
    {
        version: "1.9.4",
        notes: [
            {
                id: "service-upgrade-data-protection",
                message: "release-service-upgrade-data-protection",
                cardMessage: "release-card-service-upgrade-data-protection",
                priority: 0,
            },
        ],
    },
    {
        version: "1.9.5",
        notes: [
            {
                id: "recommended-translation-defaults",
                message: "release-recommended-translation-defaults",
                cardMessage: "release-card-recommended-translation-defaults",
                priority: 0,
            },
            {
                id: "mimo-glm-providers",
                message: "release-mimo-glm-providers",
                cardMessage: "release-card-mimo-glm-providers",
                priority: 1,
            },
            {
                id: "unified-translation-benchmark",
                message: "release-unified-translation-benchmark",
                cardMessage: "release-card-unified-translation-benchmark",
                priority: 2,
            },
        ],
    },
];

export function compareVersions(a: string, b: string): number {
    const left = a.split(".").map(Number),
        right = b.split(".").map(Number);
    for (let i = 0; i < Math.max(left.length, right.length); i++) {
        const diff = (left[i] || 0) - (right[i] || 0);
        if (diff) return Math.sign(diff);
    }
    return 0;
}

export function readReleaseState(raw: unknown): ReleaseNoticeState | undefined {
    try {
        const state = JSON.parse(String(raw || ""));
        if (
            !/^\d+\.\d+\.\d+$/.test(state.version) ||
            !Array.isArray(state.shown) ||
            state.shown.some((id: unknown) => typeof id !== "string")
        )
            return;
        return state;
    } catch {
        return;
    }
}

export function pendingReleaseNotes(
    summaries: ReleaseSummary[],
    current: string,
    state: ReleaseNoticeState | undefined,
    fresh: boolean,
): ReleaseNote[] {
    if (fresh || (state && compareVersions(current, state.version) <= 0))
        return [];
    return summaries
        .filter(
            (release) =>
                compareVersions(release.version, current) <= 0 &&
                (state
                    ? compareVersions(release.version, state.version) > 0
                    : release.version === current),
        )
        .flatMap((release) =>
            release.notes.map((note) => ({
                ...note,
                version: release.version,
            })),
        )
        .filter((note) => !state?.shown.includes(note.id))
        .sort(
            (a, b) =>
                a.priority - b.priority ||
                compareVersions(b.version, a.version),
        )
        .slice(0, 3);
}

export function completeReleaseNotice(
    current: string,
    previous: ReleaseNoticeState | undefined,
    notes: ReleaseNote[],
): ReleaseNoticeState {
    return {
        version:
            previous && compareVersions(previous.version, current) > 0
                ? previous.version
                : current,
        shown: Array.from(
            new Set([...(previous?.shown || []), ...notes.map((n) => n.id)]),
        ),
    };
}
