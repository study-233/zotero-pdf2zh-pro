PDF.js 6.3.289, from the official `pdfjs-dist` npm package.
The package integrity is recorded in source.json; LICENSE.txt is Apache-2.0.

`legacy/build/pdf.min.mjs` and `legacy/build/pdf.worker.min.mjs` retain their
original bytes and use `.js` filenames so Windows static preview servers return
a JavaScript MIME type. They are still loaded as ES modules. CMaps, standard
fonts and WASM resources are served locally; no CDN or credential is used.
