#!/usr/bin/env python3
"""Timing check for page picking on GitHub under load (1 Nov 2026): 12 page pickings at once, as in a real run."""
import os, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["annual.py"]
import annual as A
urls = ["166/2025", "734/2025", "1510/2025", "1369/2025", "738/2025", "644/2026"]
jobs = [(f"{u}#{i}", f"https://www.khistocks.com/assets/annual_reports/{u}") for i in range(2) for u in urls]


def one(j):
    jid, url = j
    w = tempfile.mkdtemp()
    t0 = time.time()
    code, msg = A.curl(url + ".pdf", w + "/r.pdf", timeout=600)
    t1 = time.time()
    t = subprocess.run(["pdftotext", "-layout", w + "/r.pdf", "-"], capture_output=True, text=True, errors="ignore").stdout
    t2 = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(A.__file__), "lib", "prep.py"), w + "/r.pdf", w + "/b.txt"],
                           capture_output=True, text=True, timeout=900, cwd=w)
        r = f"prep rc {p.returncode} {p.stdout[-160:]} {p.stderr[-300:]}"
    except subprocess.TimeoutExpired:
        r = "prep TIMEOUT"
    t3 = time.time()
    return f"{jid} dl {code} {msg} {round(t1 - t0)}s, text {round(t2 - t1)}s ({t.count(chr(12))} pages), prep {round(t3 - t2)}s: {r}"


with ThreadPoolExecutor(12) as ex:
    for line in ex.map(one, jobs):
        print(line, flush=True)
