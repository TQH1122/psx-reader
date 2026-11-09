#!/usr/bin/env python3
"""Joins the results of the readers that ran side by side (8 Nov 2026). Each reader (--shard i of --shards n) owns
the companies whose ticker falls in its share, so for every report key only the owning reader's copy is taken; a key
it dropped is dropped. Then the spend guard runs once on the spend of all readers together.

Usage: shard_merge.py SHARDS_DIR N    (SHARDS_DIR/shard-<i>/ holds what reader i saved)
"""
import json, os, shutil, sys, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AN = os.path.join(ROOT, "data", "files", "annual")
EXTRACTS = os.path.join(ROOT, "data", "files", "report_extracts.json")
src, n = sys.argv[1], int(sys.argv[2])


def jl(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def owner(tk):
    return zlib.crc32(str(tk).encode()) % n


def tk_of(key):
    p = key.split(":")
    return p[1] if len(p) == 3 else None


def merge(base_path, name, indent):
    base = jl(base_path, {}) or {}
    for i in range(n):
        d = os.path.join(src, f"shard-{i}")
        part = jl(os.path.join(d, name), None)
        if not isinstance(part, dict):
            continue  # that reader saved nothing usable: its companies keep their saved state
        for k in [k for k in base if tk_of(k) and owner(tk_of(k)) == i and k not in part]:
            base.pop(k)
        for k, v in part.items():
            if tk_of(k) and owner(tk_of(k)) == i:
                base[k] = v
    with open(base_path, "w") as f:
        json.dump(base, f, indent=indent) if indent is not None else json.dump(base, f)
    return len(base)


ns = merge(os.path.join(AN, "state.json"), "state.json", 0)
ne = merge(EXTRACTS, "report_extracts.json", None)
merge(os.path.join(AN, "jobs.json"), "jobs.json", 0)

# model answers saved by each reader: file names are <source>_<ticker>_<year>.json
copied = 0
os.makedirs(os.path.join(AN, "reads"), exist_ok=True)
for i in range(n):
    rd = os.path.join(src, f"shard-{i}", "reads")
    for f in os.listdir(rd) if os.path.isdir(rd) else []:
        parts = f[:-5].split("_")
        if f.endswith(".json") and len(parts) >= 3 and owner("_".join(parts[1:-1])) == i:
            shutil.copy(os.path.join(rd, f), os.path.join(AN, "reads", f))
            copied += 1

mig = jl(os.path.join(AN, "migrations.json"), {}) or {}
for i in range(n):
    mig.update(jl(os.path.join(src, f"shard-{i}", "migrations.json"), {}) or {})
json.dump(mig, open(os.path.join(AN, "migrations.json"), "w"), indent=0)

# one log line per reader, then one summary line the restart step reads
done, gain, lefts, logs = 0, 0, [], []
for i in range(n):
    lines = open(os.path.join(src, f"shard-{i}", "log.txt"), errors="ignore").read().splitlines() \
        if os.path.exists(os.path.join(src, f"shard-{i}", "log.txt")) else []
    logs += [f"[reader {i}] {x}" for x in lines if not x.startswith("pages ")]
    sd = jl(os.path.join(src, f"shard-{i}", f"shard_done_{i}.json"), {}) or {}
    done += sd.get("done", 0)
    gain += sd.get("gain", 0)
    for x in lines:
        if x.startswith("done: read") and "about " in x:
            try:
                lefts.append(int(x.split("about ")[1].split()[0]))
            except ValueError:
                pass
left = min(lefts) if lefts else 0
summary = f"done: read {done} annual reports ({n} readers side by side), {gain} new figures, 0 skipped, about {left} still to read"
with open(os.path.join(AN, "log.txt"), "a") as f:
    f.write("\n".join(logs + [summary]) + "\n")
print(summary)
print(f"merged: {ns} report states, {ne} report figures, {copied} model answer files")

# the spend guard, once for all readers: the credit before the first reader started against the credit now
sys.argv = [sys.argv[0]]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import annual  # noqa: E402
c0s = [(jl(os.path.join(src, f"shard-{i}", f"credit_start_{i}.json"), {}) or {}).get("c0") for i in range(n)]
c0s = [c for c in c0s if isinstance(c, (int, float))]
try:
    annual.guard_end(max(c0s) if c0s else None, done, gain)
except Exception as e:
    print(f"guard: skipped ({e})")
try:
    annual.idle_alert(done, {}, {}, {})
except Exception as e:
    print(f"idle alert: skipped ({e})")
