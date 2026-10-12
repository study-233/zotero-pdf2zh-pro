"""Verify installed distribution RECORD hashes before reusing a candidate."""
import base64
import csv
import hashlib
import importlib.metadata
import io
import sys

damaged = set()
for distribution in importlib.metadata.distributions():
    record = distribution.read_text("RECORD")
    if not record:
        continue
    for relative, expected, _size in csv.reader(io.StringIO(record)):
        if not expected:
            continue
        algorithm, encoded = expected.split("=", 1)
        path = distribution.locate_file(relative)
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, algorithm).digest()
            wanted = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if actual != wanted:
                damaged.add(distribution.metadata["Name"])
        except (OSError, ValueError):
            damaged.add(distribution.metadata["Name"])
for name in sorted(damaged):
    print(name)
sys.exit(2 if damaged else 0)
