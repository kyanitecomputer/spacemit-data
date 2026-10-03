#!/usr/bin/env python3
"""Verify reviewed input bytes in an existing research cache; no network/import."""
import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: verify-sources.py RESEARCH_CACHE')
root = Path(__file__).resolve().parent.parent
cache = Path(sys.argv[1])
count = 0
for source in json.loads((root / 'provenance/sources.json').read_text()):
    for fact in source['files']:
        path = cache / source['cache_dir'] / fact['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != fact['sha256']:
            raise SystemExit(f'changed source: {path}')
        count += 1
print(f'{count} pinned-source input hashes verified (no hardware validation)')
