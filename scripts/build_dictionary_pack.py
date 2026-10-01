#!/usr/bin/env python3
"""Build an explicit user-supplied dictionary into a separate download artifact."""
import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d+)?", args.version):
        parser.error("version must be YYYY.MM.DD[.revision]")
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    valid = [entry for entry in raw.values() if any(re.search(r"[\u3400-\u9fff]", d.get("def_cn", "")) for d in entry["defs"])]
    data = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    name = f"collins-en-zh-{args.version}.json.gz"
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / name).write_bytes(compressed)
    pack = {"id": "collins-en-zh", "version": args.version,
            "url": f"https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/glossary-data/dictionaries/{name}",
            "sha256": hashlib.sha256(compressed).hexdigest(), "sizeBytes": len(compressed),
            "entryCount": len(valid), "aiEntries": sum(e.get("_pdf2zhSupplement", {}).get("kind") == "ai" for e in valid)}
    (args.output / "catalog.json").write_text(json.dumps({"schemaVersion": 1, "packs": [pack]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(pack, ensure_ascii=False))


if __name__ == "__main__":
    main()
