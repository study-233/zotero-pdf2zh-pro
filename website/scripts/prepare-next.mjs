import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
const root = process.cwd();
await mkdir("public", { recursive: true });
await cp(".generated-public", "public", { recursive: true });
const routes = JSON.parse(await readFile("routes.json", "utf8"));
let group = "";
const pages = [];
for (const route of routes) {
  if (route.group !== group) {
    group = route.group;
    pages.push(`---${group}---`);
  }
  pages.push(route.slug || "preparation");
}
await writeFile(
  "src/content/docs/guide/meta.json",
  JSON.stringify({ title: "使用教程", pages }, null, 2) + "\n",
);
console.log("Prepared Next public assets and Fumadocs navigation.");
