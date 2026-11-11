#!/usr/bin/env python3
"""One off probe (11 Nov 2026): which free fund flow and fund holding sources answer GitHub's computers, and how their
data loads. Public pages only; prints status, size and the links and script calls each page makes."""
import json, os, re, urllib.request

UA = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Mobile Safari/537.36"}
URLS = ["https://scstrade.com/FIPILIPI.aspx", "https://scstrade.com/research/RE_Mutual_Fund_Reports.aspx"]
OLD = ["https://www.nccpl.com.pk/", "https://www.nccpl.com.pk/market-information", "https://www.nccpl.com.pk/fipi-lipi",
        "https://beta.nccpl.com.pk/market-information", "https://www.mufap.com.pk/", "https://www.mufap.com.pk/Industry/IndustryStatDaily",
        "https://www.mufap.com.pk/FundProfile/FundManagerReports", "https://www.mufap.com.pk/WebRegulations/FundManagerReport",
        "https://scstrade.com/research/RE_Mutual_Fund_Reports.aspx", "https://finhisaab.com/market-updates/fipi-lipi",
        "https://www.secp.gov.pk/data-and-statistics/capital-markets/", "https://dps.psx.com.pk/market-summary"]
for u in URLS:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40)
        h = r.read().decode("utf-8", "ignore")
        links = sorted(set(re.findall(r'(?:href|src|action|url)\s*[:=]\s*["\']([^"\'#]{4,160})["\']', h)))
        keep = [l for l in links if re.search(r"(?i)fipi|lipi|fund|holding|portfolio|api|ajax|json|\.aspx|report|flow|investor|xls|csv|pdf", l)]
        print(f"\n=== {u} {r.status} {len(h)} bytes")
        print("\n".join(keep[:70]))
        for m in re.findall(r"(?is)\$\.ajax\(\{.{0,500}?\}\)", h)[:12]:
            print("AJAX:", re.sub(r"\s+", " ", m)[:500])
        for m in sorted(set(re.findall(r"[\w/.-]+\.aspx/\w+", h))):
            print("METHOD:", m)
        for m in re.findall(r"(?is)<select[^>]*>.{0,600}", h)[:4]:
            print("SELECT:", re.sub(r"\s+", " ", m)[:600])
        print("TEXT:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", h)))[:900])
    except Exception as e:
        print(f"\n=== {u} FAILED {str(e)[:150]}")
tok = os.environ.get("GITHUB_MODELS_TOKEN")
if tok:
    req = urllib.request.Request("https://models.github.ai/inference/chat/completions", method="POST",
                                 data=json.dumps({"model": "openai/gpt-4.1", "messages": [{"role": "user", "content": "Reply as JSON {\"ok\": true}"}],
                                                  "response_format": {"type": "json_object"}}).encode(),
                                 headers={"authorization": f"Bearer {tok}", "content-type": "application/json"})
    try:
        print("\n=== github models", urllib.request.urlopen(req, timeout=60).read().decode()[:300])
    except urllib.error.HTTPError as e:
        print("\n=== github models error", e.code, e.read().decode()[:400])
