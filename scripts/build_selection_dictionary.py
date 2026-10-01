"""Build the bundled 50k-entry ECDICT subset from an explicitly supplied CSV.

Usage: python3 scripts/build_selection_dictionary.py /path/to/ecdict.csv
No network access. The input checksum is recorded alongside the dictionary.
"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys


def build(source, destination):
    entries = {}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            word = row["word"].strip().lower()
            translation = row["translation"].strip().replace("\\n", "\n")
            if not translation or len(word) > 100 or not re.fullmatch(r"[a-z]+(?:[ '\-][a-z]+){0,2}", word):
                continue
            ranks = [int(row[key]) for key in ("bnc", "frq") if row[key].isdigit() and int(row[key]) > 0]
            rank = min(ranks, default=10**9)
            # Prefer the more frequent entry when case variants collide.
            if word not in entries or rank < entries[word][0]:
                entries[word] = (rank, row["phonetic"].strip(), translation)
    selected = sorted(entries, key=lambda word: (entries[word][0], word))[:50000]
    data = {word: list(entries[word][1:]) for word in sorted(selected)}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "ecdict.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    metadata = {
        "source": "https://github.com/skywind3000/ECDICT",
        "input": "ecdict.csv",
        "inputSHA256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "entries": len(data),
        "selection": "Minimum positive BNC/FRQ rank, then normalized word; first 50000 entries with a Chinese translation. See scripts/build_selection_dictionary.py.",
        "license": "MIT; see LICENSE-ECDICT.txt",
    }
    (destination / "source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"Built {len(data)} entries; unconditional: {data.get('unconditional')}")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(__file__).resolve().parent.parent / "plugin/addon/content/dictionaries")
