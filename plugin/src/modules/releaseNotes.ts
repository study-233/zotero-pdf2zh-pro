export type ReleaseAction = "profiles" | "selection" | "tasks";
export interface ReleaseNote {
    id: string;
    message: string;
    priority: number;
    action?: ReleaseAction;
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
                action: "profiles",
            },
            {
                id: "selection-tabs",
                message: "release-selection",
                priority: 1,
                action: "selection",
            },
            {
                id: "task-feedback",
                message: "release-tasks",
                priority: 2,
                action: "tasks",
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
