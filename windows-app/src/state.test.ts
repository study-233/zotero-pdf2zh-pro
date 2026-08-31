import { describe, expect, it } from "vitest";
import { ControlState, toViewModel } from "./state";

const base: ControlState = {
    installation: "current",
    service: "stopped",
    appVersion: "1.1.0",
    installedVersion: "1.1.0",
    serviceVersion: null,
    address: "http://127.0.0.1:8890",
    autostartEnabled: true,
    dataDir: "C:\\data",
    logFile: "C:\\logs\\server.log",
    controlLog: "C:\\logs\\control-panel.log",
    runningFromInstalledPath: true,
};

describe("toViewModel", () => {
    it("offers installation when the product is missing", () => {
        expect(toViewModel({ ...base, installation: "notInstalled" }).primaryAction).toBe("install");
    });

    it("offers an upgrade before service controls", () => {
        const view = toViewModel({
            ...base,
            installation: "updateAvailable",
            installedVersion: "1.0.1",
            service: "running",
        });
        expect(view.primaryAction).toBe("upgrade");
        expect(view.badge).toBe("发现新版");
    });

    it("never offers stop for an unknown listener", () => {
        const view = toViewModel({ ...base, service: "portConflict" });
        expect(view.primaryAction).toBe("refresh");
        expect(view.tone).toBe("danger");
    });

    it("toggles the main action for a managed service", () => {
        expect(toViewModel(base).primaryAction).toBe("start");
        expect(toViewModel({ ...base, service: "running" }).primaryAction).toBe("stop");
    });
});
