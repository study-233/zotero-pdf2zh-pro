import { defineConfig } from "zotero-plugin-scaffold";
import pkg from "./package.json";

const devRuntime = process.env.PDF2ZH_DEV_RUNTIME || "";
// Scaffold uses dist in glob patterns; backslashes escape characters on Windows.
const dist = devRuntime
    ? `${devRuntime.replace(/\\/g, "/")}/plugin-build`
    : "build";

export default defineConfig({
    source: ["src", "addon"],
    dist,
    name: pkg.config.addonName,
    xpiName: "zotero-pdf2zh-pro",
    id: pkg.config.addonID,
    namespace: pkg.config.addonRef,
    server: {
        startArgs: devRuntime ? ["-no-remote"] : [],
        devtools: !devRuntime,
    },
    build: {
        assets: ["addon/**/*.*"],
        hooks: {
            "build:init": (ctx) => {
                // Refresh on every build, including development hot reloads.
                ctx.build.define.profileUIBuild = String(Date.now());
            },
        },
        // Zotero 9 requires update_url even for directly distributed XPI files.
        // Keep the source manifest so its stable release-manifest URL is packaged.
        makeManifest: {
            enable: false,
        },
        define: {
            ...pkg.config,
            addonName: pkg.config.addonName + (devRuntime ? "（开发版）" : ""),
            author: pkg.author,
            description: pkg.description,
            homepage: pkg.homepage,
            buildVersion: pkg.version,
            buildTime: "{{buildTime}}",
        },
        esbuildOptions: [
            {
                entryPoints: ["src/index.ts"],
                define: {
                    __env__: `"${process.env.NODE_ENV}"`,
                    __devRuntime__: JSON.stringify(devRuntime),
                },
                bundle: true,
                target: "firefox115",
                outfile: `${dist}/addon/content/scripts/${pkg.config.addonRef}.js`,
            },
        ],
    },

    // If you need to see a more detailed log, uncomment the following line:
    // logLevel: "trace",
});
