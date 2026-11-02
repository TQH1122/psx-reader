#!/usr/bin/env python3
"""One off source probe (2 Nov 2026): how far back the PSX announcements search lists annual report filings."""
import re, subprocess
def post(data):
    p = subprocess.run(["curl", "-s", "-m", "90", "-X", "POST", "https://dps.psx.com.pk/announcements",
                        "-H", "X-Requested-With: XMLHttpRequest", "-H", "Referer: https://dps.psx.com.pk/announcements/companies",
                        "-w", "\n%{http_code}"] + sum([["--data-urlencode", f"{k}={v}"] for k, v in data.items()], []),
                       capture_output=True, text=True)
    body, code = p.stdout.rsplit("\n", 1)
    return code, body
for tk in ("LUCK", "HUBC", "KTML", "MLCF", "SHEL"):
    for q in ("Annual Report", ""):
        code, body = post({"type": "C", "symbol": tk, "query": q, "count": 500, "offset": 0, "date_from": "", "date_to": "", "page": "annc"})
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
        found = []
        for r in rows:
            c = [re.sub("<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            if re.search(r"document/\d+", r):
                found.append((c[0] if c else "", " ".join(c[1:3])[:70]))
        ann = [f for f in found if re.search(r"annual report|annual accounts|year ended", f[1], re.I)]
        print(tk, repr(q), "http", code, "rows", len(found), "annual", len(ann), "oldest", found[-1] if found else None, "| sample", ann[-3:], flush=True)
