#!/usr/bin/env python3
"""One off probe (8 Nov 2026): where are SBP's yearly analysis files of listed companies, and how are they laid out?
SBP data is public, so printing its layout here is fine."""
import io, re, subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "openpyxl", "xlrd"], capture_output=True)
import openpyxl

UA = "Mozilla/5.0"


def get(url, binary=False):
    p = subprocess.run(["curl", "-sL", "-m", "120", "-A", UA, "-w", "\n%{http_code}", url], capture_output=True)
    body, _, code = p.stdout.rpartition(b"\n")
    return code.decode(), (body if binary else body.decode("latin1"))


def links(url):
    code, h = get(url)
    hs = re.findall(r'href\s*=\s*"([^"]+)"', h, re.I)
    return code, hs


for base in ["https://www.sbp.org.pk", "https://archive.sbp.org.pk"]:
    for y in range(2005, 2027):
        u = f"{base}/reports/annual/FSANFC/{y}/{y}.htm"
        code, hs = links(u)
        x = [h for h in hs if re.search(r"\.(xlsx?|zip)$", h, re.I)]
        if code == "200":
            print("PAGE", u, "xls:", x[:6])
for u in ["https://www.sbp.org.pk/departments/stats/index2.htm", "https://archive.sbp.org.pk/departments/stats/index2.htm",
          "https://www.sbp.org.pk/departments/stats/FSA.htm", "https://archive.sbp.org.pk/publications/index2.asp",
          "https://www.sbp.org.pk/our-operations/publications/financial-statements-analysis-non-financial-companies-listed-on-pakistan-stock-exchange/428"]:
    code, hs = links(u)
    print("INDEX", u, code, [h for h in hs if re.search(r"fsa|financial.?statement|FSANFC", h, re.I)][:40])
