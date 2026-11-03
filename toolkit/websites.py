#!/usr/bin/env python3
"""Website report finder (3 Nov 2026): every PSX listed company must publish its annual reports on its own website.

KHI Stocks, where nearly every report read so far came from, often holds only a short extract of the company only
accounts, so group companies never got consolidated figures (625 company years), and 1,915 company years had no
report online at all. This finds the full annual reports on the companies' own websites.

For each listed company: its website from its open PSX company page, then the site's investor, financial and
annual report pages (two levels at most), keeping PDF links that are plainly annual reports and reading the year
from the link. It obeys each site's robots.txt, identifies itself honestly and waits between requests.
A site is crawled again after 14 days; the whole sweep stops after the minutes given.

Writes data/files/annual/web_catalogue.json: {"asOf", "co": {ticker: {"site", "at", "years": {year: url}}}}.
The reader (annual.py) uses these reports as a source, ahead of KHI Stocks for group companies and wherever the
KHI file failed. The reader still checks every report's own year end, company name and statements itself.

  python3 toolkit/websites.py [MINUTES]
"""
import json, os, re, sys, time, threading
import urllib.request, urllib.parse, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "files", "annual", "web_catalogue.json")
SCREENER = os.path.join(ROOT, "data", "db", "market", "screener.json")
UA = "psx-tracker-report-finder/1.0 (personal investment research; reads published annual reports)"
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 50
REFRESH_DAYS = 14
GAP = 1.0                      # seconds between requests to the same site
LINK_WORDS = re.compile(r"investor|financ|annual|report|shareholder|stakeholder|download|corporate|publication|result", re.I)
DEEP_WORDS = re.compile(r"annual|financ|report|archive|result", re.I)
NOT_ANNUAL = re.compile(r"quarter|\bq[1-4]\b|1st|2nd|3rd|first|second|third|half|interim|sustainab|esg|csr|"
                        r"policy|code.of|proxy|notice|agm|egm|form|pattern|shareholding|dividend|unclaimed|"
                        r"brochure|profile|presentation|briefing|transcript|urdu|rating|prospectus|circular", re.I)
IS_ANNUAL = re.compile(r"annual|\bar[\s_-]?\d{2,4}\b|\bar\d{2,4}|yearly|year.?end|financial.?statements", re.I)


def get(url, timeout=40, limit=3_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("content-type", "")
        return r.geturl(), ctype, r.read(limit).decode("utf-8", "ignore")


def website(tk):
    """the company's website as printed on its open PSX company page"""
    try:
        _, _, s = get(f"https://dps.psx.com.pk/company/{urllib.parse.quote(tk)}")
    except Exception:
        return None
    m = re.search(r'WEBSITE</div>\s*<p>\s*<a href="([^"]+)"', s)
    if not m:
        return None
    u = m.group(1).strip()
    if not u or "psx.com.pk" in u:
        return None
    return u if u.startswith("http") else "http://" + u


def year_of(url, text):
    """the report's year from its link: 4 digits (2006 to next year), else 2 digits after 'annual' or 'AR'"""
    hay = f"{text} {urllib.parse.unquote(url.rsplit('/', 1)[-1])}"
    ys = [int(y) for y in re.findall(r"(?<!\d)(20[0-2]\d)(?!\d)", hay) if 2000 <= int(y) <= time.gmtime().tm_year + 1]
    if ys:
        return max(ys)   # "2024-25" or "2024_2025": the later year is the year end the reader will confirm
    m = re.search(r"(?:annual|\bar)[^0-9a-z]{0,3}(?:report)?[^0-9a-z]{0,3}(\d{2})(?!\d)", hay, re.I)
    if m and 0 <= int(m.group(1)) <= (time.gmtime().tm_year + 1) % 100:
        return 2000 + int(m.group(1))
    return None


def links(base, html):
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, text = m.group(1).strip(), unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(2))).strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        yield urllib.parse.urljoin(base, href), text


def crawl(tk, site, stop):
    host = urllib.parse.urlparse(site).netloc.lower().removeprefix("www.")
    rp = urllib.robotparser.RobotFileParser()
    try:
        _, _, rtxt = get(urllib.parse.urljoin(site, "/robots.txt"), timeout=20, limit=200_000)
        rp.parse(rtxt.splitlines())
    except Exception:
        rp.parse([])   # no robots.txt: everything allowed
    seen, queue, years = set(), [(site, 0)], {}
    fetched = 0
    while queue and fetched < 14 and not stop():
        url, depth = queue.pop(0)
        if url in seen or not rp.can_fetch(UA, url):
            continue
        seen.add(url)
        try:
            real, ctype, html = get(url)
        except Exception:
            continue
        fetched += 1
        time.sleep(GAP)
        if "html" not in ctype:
            continue
        if fetched == 1:   # the site may redirect to another host name
            host = urllib.parse.urlparse(real).netloc.lower().removeprefix("www.")
        for link, text in links(real, html):
            lh = urllib.parse.urlparse(link).netloc.lower().removeprefix("www.")
            path = urllib.parse.urlparse(link).path.lower()
            if path.endswith(".pdf"):
                both = f"{text} {urllib.parse.unquote(path)}"
                if IS_ANNUAL.search(both) and not NOT_ANNUAL.search(both):
                    y = year_of(link, text)
                    if y and rp.can_fetch(UA, link):
                        years.setdefault(str(y), link)
            elif lh == host and depth < 2 and link not in seen:
                words = LINK_WORDS if depth == 0 else DEEP_WORDS
                if words.search(f"{text} {path}"):
                    queue.append((link.split("#")[0], depth + 1))
    return years


def main():
    t0 = time.time()
    stop = lambda: time.time() - t0 > MINUTES * 60
    rows = (json.load(open(SCREENER)).get("rows") or [])
    tks = [str(r["s"]).split()[0] for r in sorted(rows, key=lambda r: -(r.get("mc") or 0))]
    try:
        cat = json.load(open(OUT))
    except Exception:
        cat = {"co": {}}
    co = cat.setdefault("co", {})
    now = time.time()
    due = [t for t in tks if now - (co.get(t) or {}).get("at", 0) > REFRESH_DAYS * 86400]
    lock = threading.Lock()
    done = [0]

    def one(tk):
        if stop():
            return
        prev = co.get(tk) or {}
        site = prev.get("site") or website(tk)
        found = crawl(tk, site, stop) if site else {}
        if stop() and not found:
            return   # cut off by the time limit: try this site again next run
        with lock:
            years = dict(prev.get("years") or {})
            years.update(found)   # a report found once stays known even if a later visit misses it
            co[tk] = {"site": site, "at": now, "years": dict(sorted(years.items()))}
            done[0] += 1
            if done[0] % 25 == 0:
                save(cat)

    with ThreadPoolExecutor(8) as pool:   # 8 different sites at once; each site is still visited one page at a time
        list(pool.map(one, due))
    save(cat)
    sites = sum(1 for v in co.values() if v.get("site"))
    reps = sum(len(v.get("years") or {}) for v in co.values())
    left = sum(1 for t in tks if time.time() - (co.get(t) or {}).get("at", 0) > REFRESH_DAYS * 86400)
    print(f"done: websites visited {done[0]}, {left} still due; {sites} companies with a website, "
          f"{reps} annual reports found on {sum(1 for v in co.values() if v.get('years'))} of them", flush=True)


def save(cat):
    cat["asOf"] = time.strftime("%Y-%m-%d")
    tmp = OUT + ".tmp"
    json.dump(cat, open(tmp, "w"), indent=0, sort_keys=True)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
