// Live smoke test of actual plugin download/store modules using a Node adapter
// for Zotero HTTP/IO. No Zotero profile, browser extension or local server is used.
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const ts = require("../plugin/node_modules/typescript");
const root = path.resolve(__dirname, "..");
const directory = path.join(root, ".local-dev/dictionary-download-smoke");
(async () => {
  await fsp.mkdir(directory, { recursive: true });
  const prefs = new Map();
  let requests = 0,
    resets = 0;
  const globals = {
    Zotero: {
      DataDirectory: { dir: directory },
      getMainWindow: () => globalThis,
      HTTP: {
        request: async (method, url, options) => {
          requests++;
          assert.equal(method, "GET");
          // curl follows the host's network configuration; production uses Zotero.HTTP.
          return new Promise((resolve, reject) => {
            const child = require("node:child_process").spawn("curl", [
              "--fail",
              "--location",
              "--silent",
              "--show-error",
              "--connect-timeout",
              "15",
              "--max-time",
              String(options.timeout / 1000),
              url,
            ]);
            const chunks = [];
            let length = 0,
              error = "";
            const abort = () => child.kill();
            options.cancellerReceiver(abort);
            const xhr = { abort };
            options.requestObserver(xhr);
            child.stdout.on("data", (chunk) => {
              chunks.push(chunk);
              length += chunk.length;
              xhr.onprogress({ loaded: length });
            });
            child.stderr.on("data", (chunk) => {
              error += chunk;
            });
            child.on("error", reject);
            child.on("close", (code) =>
              code === 0
                ? resolve({
                    response: Uint8Array.from(Buffer.concat(chunks)).buffer,
                  })
                : reject(Error(error || "Download cancelled")),
            );
          });
        },
      },
    },
    PathUtils: { join: path.join, parent: path.dirname },
    IOUtils: {
      exists: async (p) => fs.existsSync(p),
      readUTF8: async (p) => fsp.readFile(p, "utf8"),
      makeDirectory: async (p) => fsp.mkdir(p, { recursive: true }),
      write: async (p, b) => fsp.writeFile(p, b),
      writeUTF8: async (p, text, { tmpPath }) => {
        await fsp.writeFile(tmpPath, text);
        await fsp.rename(tmpPath, p);
      },
      computeHexDigest: async (p) =>
        crypto
          .createHash("sha256")
          .update(await fsp.readFile(p))
          .digest("hex"),
      remove: async (p) => fsp.rm(p, { force: true }),
    },
  };
  const imports = {
    "../../package.json": { config: { addonRef: "pdf2zhpro" } },
    "../utils/prefs": { setPref: (k, v) => prefs.set(k, v) },
    "./selectionTranslate": {
      resetSelectionTranslation() {
        resets++;
      },
    },
    "./diagnostics": {
      recordDiagnostic(event) {
        console.log(event);
      },
    },
  };
  const load = (name) => {
    const exports = {};
    const code = ts.transpileModule(
      fs.readFileSync(
        path.join(root, "plugin/src/modules/" + name + ".ts"),
        "utf8",
      ),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
        },
      },
    ).outputText;
    new Function("require", "exports", ...Object.keys(globals), code)(
      (key) => imports[key],
      exports,
      ...Object.values(globals),
    );
    return exports;
  };
  const store = (imports["./selectionDictionaryStore"] = load(
    "selectionDictionaryStore",
  ));
  const downloader = load("selectionDictionaryDownload");
  await downloader.refreshDictionaryDownload();
  assert.equal(downloader.dictionaryDownloadState().phase, "idle");
  await downloader.downloadDictionary();
  const result = downloader.dictionaryDownloadState();
  assert.equal(result.phase, "idle");
  assert.equal(result.installed.entries, 37375);
  assert.equal(result.installed.aiEntries, 945);
  assert.equal(prefs.get("selectionDictionary"), "collins");
  assert.equal(resets, 1);
  assert.equal(requests, 2);
  globals.Zotero.HTTP.request = async () => {
    throw Error("offline");
  };
  // Recreate the store to prove persisted data works after a fresh load.
  const offline = load("selectionDictionaryStore");
  assert.match(
    (await offline.lookupImportedDictionary("Unconditional")).senses[0].chinese,
    /无条件/,
  );
  assert.equal(
    (await offline.lookupImportedDictionary("gramme")).aiGenerated,
    true,
  );
  console.log(
    JSON.stringify(
      {
        result: "PASS",
        version: result.installed.version,
        entries: result.installed.entries,
        aiEntries: result.installed.aiEntries,
        requests,
        offlineAfterReload: true,
        sha256: result.installed.sha256,
      },
      null,
      2,
    ),
  );
  await fsp.writeFile(
    path.join(directory, "result.json"),
    JSON.stringify(result.installed, null, 2) + "\n",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
