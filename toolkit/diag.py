#!/usr/bin/env python3
"""Timing check for page picking on GitHub (1 Nov 2026): big reports timed out there but take seconds elsewhere."""
import os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["annual.py"]
import annual as A
for jid, url in [("khi:MCB:2025", "https://www.khistocks.com/assets/annual_reports/166/2025"),
                 ("khi:ENGROH:2025", "https://www.khistocks.com/assets/annual_reports/734/2025")]:
    w = tempfile.mkdtemp()
    t = time.time()
    code, msg = A.curl(url + ".pdf", w + "/r.pdf", timeout=600)
    print(jid, "download", code, msg, os.path.getsize(w + "/r.pdf") if os.path.exists(w + "/r.pdf") else 0, round(time.time() - t), "s", flush=True)
    t = time.time()
    p = subprocess.run(["pdfinfo", w + "/r.pdf"], capture_output=True, text=True)
    print(" pdfinfo", p.returncode, [l for l in p.stdout.splitlines() if l.startswith(("Pages", "Producer"))], round(time.time() - t, 1), flush=True)
    t = time.time()
    p = subprocess.run(["pdftotext", "-layout", w + "/r.pdf", "-"], capture_output=True, text=True, errors="ignore")
    pages = p.stdout.split("\f")
    print(" pdftotext", p.returncode, len(p.stdout), "chars", len(pages), "pages", sum(1 for x in pages if len(x.strip()) > 200),
          "text pages", round(time.time() - t, 1), "s", p.stderr[-300:], flush=True)
    t = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(A.__file__), "lib", "prep.py"), w + "/r.pdf", w + "/b.txt"],
                           capture_output=True, text=True, timeout=900, cwd=w)
        print(" prep", p.returncode, round(time.time() - t), "s", p.stdout[-400:], p.stderr[-600:], flush=True)
    except subprocess.TimeoutExpired as e:
        print(" prep TIMEOUT after 900 s; output so far:", (e.stdout or b"")[-400:], (e.stderr or b"")[-600:], flush=True)
    subprocess.run(["pdftotext", "-v"], capture_output=False)
    subprocess.run(["bash", "-c", "nproc; free -g | head -2; ps aux --sort=-%cpu | head -5"])
