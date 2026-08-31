import { defineConfig } from "zotero-plugin-scaffold";
import pkg from "./package.json";

export default defineConfig({
    source: ["src", "addon"],
    dist: "build",
    name: pkg.config.addonName,
    xpiName: "zotero-pdf2zh-pro",
    id: pkg.config.addonID,
    namespace: pkg.config.addonRef,
    build: {
        assets: ["addon/**/*.*"],
        // Zotero 9 requires update_url even for directly distributed XPI files.
        // Keep the source manifest so its stable release-manifest URL is packaged.
        makeManifest: {
            enable: false,
        },
        define: {
            ...pkg.config,
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
                },
                bundle: true,
                target: "firefox115",
                outfile: `build/addon/content/scripts/${pkg.config.addonRef}.js`,
            },
        ],
    },

    // If you need to see a more detailed log, uncomment the following line:
    // logLevel: "trace",
});
