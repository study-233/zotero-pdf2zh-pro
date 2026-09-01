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
  it("maps installation and service states to safe primary actions", () => {
    const cases: Array<[ControlState, string]> = [
      [{ ...base, installation: "notInstalled" }, "install"],
      [
        {
          ...base,
          installation: "updateAvailable",
          installedVersion: "1.0.1",
          service: "running",
        },
        "upgrade",
      ],
      [{ ...base, service: "portConflict" }, "refresh"],
      [base, "start"],
      [{ ...base, service: "running" }, "stop"],
    ];

    for (const [state, expected] of cases) {
      expect(toViewModel(state).primaryAction).toBe(expected);
    }
    expect(toViewModel({ ...base, service: "portConflict" }).tone).toBe(
      "danger",
    );
    expect(
      toViewModel({
        ...base,
        installation: "updateAvailable",
        installedVersion: "1.0.1",
      }).badge,
    ).toBe("发现新版");
  });

  it("prioritizes a verified remote update for the installed control center", () => {
    const view = toViewModel(base, {
      available: true,
      currentVersion: "1.1.0",
      latestVersion: "1.2.0",
    });
    expect(view.primaryAction).toBe("update");
    expect(view.primaryLabel).toBe("更新到 v1.2.0");
    expect(
      toViewModel({ ...base, runningFromInstalledPath: false }, {
        available: true,
        currentVersion: "1.1.0",
        latestVersion: "1.2.0",
      }).primaryAction,
    ).toBe("start");
  });
});
