#!/usr/bin/env python3
"""One off probe (11 Nov 2026): SCS Trade's fund flow (FIPI and LIPI) and mutual fund report data calls."""
import json, re, urllib.request
UA = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36"}
for page in ("https://scstrade.com/FIPILIPI.aspx", "https://scstrade.com/research/RE_Mutual_Fund_Reports.aspx"):
    h = urllib.request.urlopen(urllib.request.Request(page, headers=UA), timeout=40).read().decode("utf-8", "ignore")
    base = page.rsplit("/", 1)[0] + "/"
    for m in sorted(set(re.findall(r"([\w]+\.aspx)/(\w+)", h))):
        i = h.find(f"{m[0]}/{m[1]}")
        ctx = re.sub(r"\s+", " ", h[max(0, i - 700): i + 500])
        print(f"\n### {m[0]}/{m[1]} JS: {ctx[:1200]}")
    for m in re.findall(r"(?is)<(?:select|input)[^>]*id=\"([^\"]+)\"[^>]*>(.{0,300})", h)[:12]:
        print("FIELD:", m[0], re.sub(r"\s+", " ", m[1])[:300])
    for meth in sorted(set(re.findall(r"[\w]+\.aspx/(\w+)", h))):
        for body in ({}, {"date": "11/10/2026"}, {"par": "11/10/2026"}):
            try:
                req = urllib.request.Request(base + page.rsplit("/", 1)[1] + "/" + meth, data=json.dumps(body).encode(), method="POST",
                                             headers={**UA, "content-type": "application/json; charset=utf-8"})
                out = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
                print(f"CALL {meth} {body}: {len(out)} {out[:700]}")
                break
            except urllib.error.HTTPError as e:
                print(f"CALL {meth} {body}: {e.code} {e.read().decode('utf-8','ignore')[:200]}")
