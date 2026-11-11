#!/usr/bin/env python3
"""One off probe (11 Nov 2026): SCS Trade's fund flow and mutual fund calls with their real inputs."""
import json, urllib.request
UA = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36",
      "content-type": "application/json; charset=utf-8"}
G = {"_search": False, "nd": 1, "rows": 500, "page": 1, "sidx": "", "sord": "asc"}
for url, d1, d2 in [("https://scstrade.com/FIPILIPI.aspx/loadfipiInvestor", "11/10/2026", "11/10/2026"),
                    ("https://scstrade.com/FIPILIPI.aspx/loadlipi", "11/10/2026", "11/10/2026"),
                    ("https://scstrade.com/FIPILIPI.aspx/loadfipisector", "11/10/2026", "11/10/2026"),
                    ("https://scstrade.com/FIPILIPI.aspx/loadmainsum", "11/10/2026", "11/10/2026"),
                    ("https://scstrade.com/FIPILIPI.aspx/loadlipi", "01/02/2012", "01/02/2012"),
                    ("https://scstrade.com/FIPILIPI.aspx/loadlipi", "10/01/2026", "10/31/2026"),
                    ("https://scstrade.com/research/RE_Mutual_Fund_Reports.aspx/chart", "09/01/2026", "11/10/2026"),
                    ("https://scstrade.com/research/RE_Mutual_Fund_Reports.aspx/chart", "01/01/2015", "12/31/2015")]:
    for body in ({"date1": d1, "date2": d2, **G}, {"date1": d1, "date2": d2}):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=UA)
            out = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
            print(f"\nOK {url.rsplit('/',1)[1]} {d1}-{d2} {len(body)}: {len(out)} {out[:1500]}")
            break
        except urllib.error.HTTPError as e:
            print(f"\nERR {url.rsplit('/',1)[1]} {d1}-{d2} {len(body)}: {e.code} {e.read().decode('utf-8','ignore')[:200]}")
