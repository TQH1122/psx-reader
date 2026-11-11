#!/usr/bin/env python3
"""One off probe (11 Nov 2026), round 2: PSX notice search call, Gemini's own YouTube video reading (Google's official
route, free daily allowance), and briefing videos on the known channels. Prints only short status lines."""
import json, os, re, subprocess, sys, urllib.request

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36"
VID = "0Mt95KPrw_I"


def sh(cmd, t=180):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


print("== PSX briefing page")
jar = "/tmp/psxjar"
sh(["curl", "-s", "-m", "60", "-A", UA, "-c", jar, "-o", "/dev/null", "https://dps.psx.com.pk/announcements/companies"])
for u in ["https://dps.psx.com.pk/announcements/cbs", "https://dps.psx.com.pk/corporate-briefing"]:
    code, out, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "-w", "\\n%{http_code}", u])
    print(u, out[-3:], len(out), [m for m in re.findall(r"<option[^>]*>([^<]{2,40})<", out)][:5])
for data in ["type=C&symbol=&query=&count=50&offset=0&date_from=&date_to=&page=cbs", "type=C&symbol=OGDC&query=&count=50&offset=0&date_from=&date_to=&page=cbs"]:
    code, out, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "-b", jar, "-X", "POST", "-H", "X-Requested-With: XMLHttpRequest",
                       "-H", "Referer: https://dps.psx.com.pk/announcements/companies", "-H", "Origin: https://dps.psx.com.pk", "-w", "\\nHTTP%{http_code}", "-d", data, "https://dps.psx.com.pk/announcements"])
    print("status", out[-7:])
    rows = re.findall(r"<tr[\s\S]*?</tr>", out)
    print(data[:40], "bytes", len(out), "rows", len(rows), re.findall(r"data-total=\"(\d+)\"", out)[:1])
    for r in rows[:5]:
        print("  ", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", r))[:220], re.findall(r"(?:href|data-images)=\"([^\"]+)\"", r)[:3])
print("done: probe")
