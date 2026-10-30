import { cp, mkdir, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const site = fileURLToPath(new URL("../", import.meta.url));
const output = path.join(site, "out");
// Next 16's Windows exporter writes segment payloads as directories. The
// browser requests dot-separated names; retain both forms for portable hosting.
async function segmentAliases(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = path.join(directory, entry.name);
    if (entry.isDirectory()) await segmentAliases(name);
    else if (entry.name.endsWith(".txt")) {
      const parts = path.relative(output, name).split(path.sep);
      const start = parts.findIndex((part) => part.startsWith("__next."));
      if (start >= 0 && start < parts.length - 1) {
        const alias = path.join(
          output,
          ...parts.slice(0, start),
          parts.slice(start).join("."),
        );
        await cp(name, alias);
      }
    }
  }
}
await segmentAliases(output);
await writeFile(path.join(output, ".nojekyll"), "");
console.log("Prepared portable Next static payloads.");
if (process.argv.includes("--prepare-only")) process.exit(0);
const result = spawnSync(
  process.execPath,
  [
    path.join(site, "scripts/python.mjs"),
    "check_website_v2.py",
    "--output",
    output,
  ],
  { cwd: site, stdio: "inherit", windowsHide: true },
);
if (result.status !== 0) process.exit(result.status ?? 1);
// Promotion runs only after validation. Preview continues serving the previous
// build while Next creates its candidate; retained recovery archives are separate.
const target = path.resolve(site, "../dist/site");
if (target !== path.resolve(site, "..", "dist", "site"))
  throw new Error("Unexpected output path");
await mkdir(target, { recursive: true });
await cp(output, target, { recursive: true });
console.log("Validated Next export copied to dist/site.");
