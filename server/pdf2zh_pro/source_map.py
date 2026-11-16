"""Freeze only the paragraph text needed to read a particular output PDF."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile

import pymupdf


def write_source_maps(files, input_path, source_hash, config, target_lang):
    # The worker has joined. current contains this run, entries also contains history.
    paragraphs = {}
    for entry in config.recovery.current.values():
        source = entry.get("source")
        translation = entry.get("readingTranslation")
        if (entry.get("status") == "succeeded" and source and translation
                and entry.get("quality", {}).get("status") != "failed"):
            paragraphs.setdefault(entry["page"], []).append([source, translation])

    with pymupdf.open(input_path) as document:
        original_widths = [page.rect.width for page in document]
        pages = list(range(1, len(document) + 1))
    if getattr(config, "only_include_translated_page", False):
        pages = [p for p in pages if config.should_translate_page(p)]
    result = {}
    for mode, output in files.items():
        layout = pages
        alternating = mode == "dual" and getattr(config, "use_alternating_pages_dual", False)
        if alternating:
            # Negative entries mark the original-language half of the pair.
            layout = [v for p in pages for v in ((p, -p) if config.dual_translate_first else (-p, p))]
        split = None
        with pymupdf.open(output.output_path) as document:
            if len(document) != len(layout):
                raise ValueError("Unexpected output page count")
            if mode == "dual" and not alternating:
                # A positive boundary puts the translation on the right; negative on the left.
                split = [original_widths[p - 1] if not getattr(config, "dual_translate_first", False)
                         else -(document[i].rect.width - original_widths[p - 1])
                         for i, p in enumerate(pages)]
        with output.output_path.open("rb") as stream:
            output_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        snapshot = dict(version=1, sourceHash=source_hash, outputHash=output_hash,
                        targetLang=target_lang, pages=layout, paragraphs=paragraphs)
        if split is not None:
            snapshot["split"] = split
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        temp = NamedTemporaryFile(dir=output.output_path.parent, delete=False)
        pending = Path(temp.name)
        try:
            with temp:
                temp.write(encoded)
            pending.replace(output.output_path.parent / (digest + ".source.json"))
        finally:
            pending.unlink(missing_ok=True)
        result[mode] = replace(output, source_map=digest)
    return result
