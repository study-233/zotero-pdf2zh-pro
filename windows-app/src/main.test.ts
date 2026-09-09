// @vitest-environment jsdom
/// <reference types="vite/client" />
import page from "../index.html?raw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), confirm: vi.fn(), open: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ confirm: mocks.confirm, open: mocks.open }));
const state = {
    installation: "current", service: "stopped", appVersion: "1.6.7",
    installedVersion: "1.6.7", serviceVersion: null, address: "http://127.0.0.1:8890",
    autostartEnabled: false, dataDir: "C:\\Product\\data", logFile: "C:\\Product\\logs\\server.log",
    controlLog: "C:\\Product\\logs\\control-panel.log", runningFromInstalledPath: true,
    installRoot: "C:\\Product", defaultInstallRoot: "C:\\Product", canRelocate: true,
    lastOperationError: null,
};
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const click = (id: string) => document.getElementById(id)!.click();
const uninstalls = () => mocks.invoke.mock.calls.filter(([command]) => command === "uninstall_product");
beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    mocks.confirm.mockReset();
    document.body.innerHTML = page;
    vi.spyOn(window, "setInterval").mockReturnValue(0);
    // Tauri overrides window.confirm with an async function despite DOM typings.
    vi.spyOn(window, "confirm").mockImplementation((message) => mocks.confirm(message) as unknown as boolean);
    mocks.invoke.mockImplementation(async (command: string) => command === "get_state" ? state :
        command === "check_for_update" ? { available: false, currentVersion: "1.6.7", latestVersion: "1.6.7" } : undefined);
    await import("./main");
    await flush();
});
afterEach(() => vi.restoreAllMocks());
describe("control-center confirmations", () => {
    it("does not uninstall when the first confirmation is cancelled", async () => {
        mocks.confirm.mockResolvedValue(false);
        click("uninstall");
        await flush();
        expect(uninstalls()).toEqual([]);
        expect(mocks.confirm).toHaveBeenCalledTimes(1);
        expect((document.getElementById("uninstall") as HTMLButtonElement).disabled).toBe(false);
    });
    it.each([false, true])("sends boolean purgeData=%s only after both answers", async (purgeData) => {
        let answer!: (value: boolean) => void;
        mocks.confirm.mockResolvedValueOnce(true).mockImplementationOnce(() => new Promise<boolean>(resolve => { answer = resolve; }));
        click("uninstall");
        await flush();
        expect(uninstalls()).toEqual([]);
        document.getElementById("uninstall")!.dispatchEvent(new MouseEvent("click"));
        expect(mocks.confirm).toHaveBeenCalledTimes(2);
        answer(purgeData);
        await flush();
        expect(uninstalls()).toEqual([["uninstall_product", { purgeData }]]);
        expect(JSON.stringify(uninstalls()[0][1])).toBe(`{"purgeData":${purgeData}}`);
    });
    it("keeps the installation when a dialog fails", async () => {
        mocks.confirm.mockResolvedValueOnce(true).mockRejectedValueOnce(new Error("dialog unavailable"));
        click("uninstall");
        await flush();
        expect(uninstalls()).toEqual([]);
        expect(document.getElementById("error-message")!.textContent).toBe("dialog unavailable");
        expect((document.getElementById("uninstall") as HTMLButtonElement).disabled).toBe(false);
    });
    it("does not migrate when the asynchronous confirmation is cancelled", async () => {
        mocks.open.mockResolvedValue("D:\\Apps");
        mocks.confirm.mockResolvedValue(false);
        click("choose-location");
        await flush();
        expect(mocks.invoke.mock.calls.some(([command]) => command === "relocate_installation")).toBe(false);
        expect(mocks.confirm).toHaveBeenCalledTimes(1);
    });
});
