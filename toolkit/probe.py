#!/usr/bin/env python3
"""One off probe (8 Nov 2026): does PSX keep daily market prices of companies that left the market?"""
import json, subprocess

for sym in ["PSMC", "SHEL", "FFBL", "DAWH", "LUCK"]:
    p = subprocess.run(["curl", "-s", "-m", "60", "-w", "\n%{http_code}", f"https://dps.psx.com.pk/timeseries/eod/{sym}"], capture_output=True)
    body, _, code = p.stdout.decode("latin1").rpartition("\n")
    try:
        d = json.loads(body).get("data") or []
        print("PSX", sym, code, len(d), d[0] if d else "", d[-1] if d else "")
    except Exception:
        print("PSX", sym, code, body[:120])
