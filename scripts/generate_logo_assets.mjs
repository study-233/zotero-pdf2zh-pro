// Install dependencies in plugin/ first, then run:
// node scripts/generate_logo_assets.mjs
// Synchronizes all logo assets from the vector source assets/logo.svg.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(join(root, "plugin/package.json"));
const { createCanvas, loadImage } = require("@napi-rs/canvas");
const svg = await readFile(join(root, "assets/logo.svg"), "utf8");

function svgAtSize(size) {
    return svg.replace(/<svg\b([^>]*)>/, (_, attributes) => {
        const withoutSize = attributes.replace(
            /\s(?:width|height)\s*=\s*(?:"[^"]*"|'[^']*')/g,
            "",
        );
        return `<svg${withoutSize} width="${size}" height="${size}">`;
    });
}

async function renderPng(size) {
    // Rasterize the SVG at each target size; never resize an existing PNG.
    const image = await loadImage(Buffer.from(svgAtSize(size)));
    const canvas = createCanvas(size, size);
    canvas.getContext("2d", { alpha: true }).drawImage(image, 0, 0);
    return canvas.toBuffer("image/png");
}

async function writeAsset(relativePath, contents) {
    const destination = join(root, relativePath);
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(destination, contents);
}

await writeAsset("plugin/addon/content/icons/favicon.svg", svgAtSize(256));
await writeAsset("plugin/addon/content/icons/favicon@0.5x.svg", svgAtSize(128));
await writeAsset("windows-app/public/logo.svg", svg);
await writeAsset("assets/logo.png", await renderPng(512));

const windowsIcons = {
    "32x32.png": 32,
    "64x64.png": 64,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "StoreLogo.png": 50,
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
};

for (const [filename, size] of Object.entries(windowsIcons)) {
    await writeAsset(
        `windows-app/src-tauri/icons/${filename}`,
        await renderPng(size),
    );
}

const icoSizes = [16, 24, 32, 48, 64, 256];
const icoHeader = Buffer.alloc(6);
icoHeader.writeUInt16LE(1, 2); // Type: icon.
icoHeader.writeUInt16LE(icoSizes.length, 4);
const icoDirectory = Buffer.alloc(16 * icoSizes.length);
const icoFrames = [];
let offset = icoHeader.length + icoDirectory.length;

for (const [index, size] of icoSizes.entries()) {
    const png = await renderPng(size);
    const entry = index * 16;
    icoDirectory.writeUInt8(size === 256 ? 0 : size, entry);
    icoDirectory.writeUInt8(size === 256 ? 0 : size, entry + 1);
    icoDirectory.writeUInt16LE(1, entry + 4); // Color planes.
    icoDirectory.writeUInt16LE(32, entry + 6); // RGBA bits per pixel.
    icoDirectory.writeUInt32LE(png.length, entry + 8);
    icoDirectory.writeUInt32LE(offset, entry + 12);
    icoFrames.push(png);
    offset += png.length;
}

await writeAsset(
    "windows-app/src-tauri/icons/icon.ico",
    Buffer.concat([icoHeader, icoDirectory, ...icoFrames]),
);

console.log(
    "Synchronized plugin, Windows, and PNG logo assets from assets/logo.svg.",
);
