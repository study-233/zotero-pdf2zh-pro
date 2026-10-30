import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const local = path.join(
  root,
  ".tmp/website-venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const candidates = process.env.PDF2ZH_WEBSITE_PYTHON
  ? [process.env.PDF2ZH_WEBSITE_PYTHON]
  : [existsSync(local) && local, "python3", "python"].filter(Boolean);
const python = candidates.find(
  (candidate) =>
    spawnSync(
      candidate,
      [
        "-X",
        "utf8",
        "-c",
        "import sys, jinja2, markdown_it, pygments; assert sys.version_info >= (3, 12)",
      ],
      { windowsHide: true, stdio: "ignore" },
    ).status === 0,
);
if (!python) {
  console.error(
    "Website preparation requires Python 3.12+ and website/requirements.txt. Set PDF2ZH_WEBSITE_PYTHON to its executable.",
  );
  process.exit(1);
}
const [script, ...args] = process.argv.slice(2);
const result = spawnSync(
  python,
  ["-X", "utf8", path.join(root, "scripts", script), ...args],
  {
    cwd: root,
    stdio: "inherit",
    windowsHide: true,
    env: { ...process.env, PYTHONUTF8: "1" },
  },
);
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
