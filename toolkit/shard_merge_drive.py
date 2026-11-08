#!/usr/bin/env python3
"""Joins the Drive indexers that ran side by side (8 Nov 2026): for each file only the owning indexer's entry is
taken. Usage: shard_merge_drive.py SHARDS_DIR N"""
import json, os, sys, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "files", "drive_ident.json")
src, n = sys.argv[1], int(sys.argv[2])
try:
    db = json.load(open(OUT))
except Exception:
    db = {}
got = 0
for i in range(n):
    try:
        part = json.load(open(os.path.join(src, f"drive-{i}", "drive_ident.json")))
    except Exception:
        continue
    for fid, r in part.items():
        if zlib.crc32(fid.encode()) % n == i:
            if db.get(fid) != r:
                got += 1
            db[fid] = r
json.dump(db, open(OUT, "w"), indent=0)
print(f"merged: {got} files new or changed; {len(db)} files indexed")
