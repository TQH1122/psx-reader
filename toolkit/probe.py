#!/usr/bin/env python3
"""One off probe (2 Nov 2026): are his Drive annual reports downloadable now that the folder is link shared?"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["annual.py"]
import annual as A
lib = [r for r in json.load(open(os.path.join(A.FILES, "library_index.json"))) if r.get("id")]
ok = sum(1 for r in lib[:8] if A.drive_open(r["id"]))
print(f"drive test: {ok} of {min(8, len(lib))} downloads worked; {len(lib)} Drive annual reports listed")
docs = A.psx_docs()
print("sources from library:", sum(len(v) for v in docs.values()), "company years")
