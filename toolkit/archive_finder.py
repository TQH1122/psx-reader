#!/usr/bin/env python3
"""Old report finder (8 Nov 2026): the Internet Archive (web.archive.org) has kept copies of the listed companies'
websites for twenty years, including annual report PDFs the companies have since taken down. Company websites today
mostly show their last few years only (the earliest report found on them is around 2016 for a typical company), so
this is where the older years are.

For each company whose website is known (web_catalogue.json), it asks the Archive's public index (CDX) once for
every PDF it ever saved from that site, keeps the ones whose link is plainly an annual report, reads the year from
the link (websites.year_of) and keeps one per year (the largest file: the full report rather than an extract).
The reader then downloads the Archive's untouched copy and checks the year end, the company's printed name and the
statements itself, like any other report (source "arc", always held to the full name check).

Several finders run side by side: finder i of n looks only at its own share of companies.
Writes data/files/annual/archive_<i>.json: {"co": {ticker: {"site", "at", "years": {year: url}}}}.

  python3 toolkit/archive_finder.py MINUTES I N
"""
import json, os, sys, time, urllib.parse, urllib.request, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websites as W  # noqa: E402  year_of, IS_ANNUAL, NOT_ANNUAL, UA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AN = os.path.join(ROOT, "data", "files", "annual")
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 45
SHARD, SHARDS = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 1)
OUT = os.path.join(AN, f"archive_{SHARD}.json")
MERGED = os.path.join(AN, "archive_catalogue.json")
REFRESH_DAYS = 30
CDX = "https://web.archive.org/cdx/search/cdx?url={}&matchType=domain&filter=mimetype:application/pdf" \
      "&output=json&fl=original,timestamp,length&collapse=urlkey&limit=50000"


def jl(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def host(site):
    h = urllib.parse.urlparse(site if "//" in site else "http://" + site).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def cdx(h):
    """every PDF the Archive saved from this site: [(link, timestamp, size)]; polite: a pause after each ask, and a
    longer wait and one more try when the Archive says it is busy"""
    for wait in (0, 20, 60):
        time.sleep(wait)
        try:
            req = urllib.request.Request(CDX.format(urllib.parse.quote(h)), headers={"User-Agent": W.UA})
            rows = json.load(urllib.request.urlopen(req, timeout=120))
            time.sleep(2)
            return [(r[0], r[1], int(r[2]) if str(r[2]).isdigit() else 0) for r in rows[1:]]
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503, 504):
                return []
        except Exception:
            pass
    return None  # the Archive did not answer: tried again next run


def annual_years(rows):
    best = {}
    for link, ts, size in rows:
        name = urllib.parse.unquote(link.rsplit("/", 1)[-1])
        if not W.IS_ANNUAL.search(name) or W.NOT_ANNUAL.search(name):
            continue
        y = W.year_of(link, "")
        if not y:
            continue
        if size < 300_000:   # a full annual report is never this small (covers, extracts, notices)
            continue
        if y not in best or size > best[y][2]:
            best[y] = (link, ts, size)
    return {str(y): f"https://web.archive.org/web/{ts}id_/{link}" for y, (link, ts, size) in sorted(best.items())}


def main():
    t0 = time.time()
    web = (jl(os.path.join(AN, "web_catalogue.json"), {}) or {}).get("co") or {}
    act = set((jl(os.path.join(ROOT, "data", "db", "market", "active.json"), {}) or {}).get("s") or web)
    own = jl(OUT, {}) or {}
    merged = (jl(MERGED, {}) or {}).get("co") or {}
    co = own.setdefault("co", {})
    for t, v in merged.items():   # what the earlier runs found, so a ticker is not asked again before it is due
        co.setdefault(t, v) if zlib.crc32(t.encode()) % SHARDS == SHARD else None
    now = time.time()
    todo = [t for t in sorted(act) if zlib.crc32(t.encode()) % SHARDS == SHARD and (web.get(t) or {}).get("site")
            and now - (co.get(t) or {}).get("at", 0) > REFRESH_DAYS * 86400]
    hosts_done, asked, found = {}, 0, 0
    for t in todo:
        if time.time() - t0 > MINUTES * 60:
            break
        h = host(web[t]["site"])
        if not h or W.NOT_SITE.search(h):
            continue
        rows = hosts_done[h] if h in hosts_done else cdx(h)
        if rows is None:
            continue
        hosts_done[h] = rows
        asked += 1
        ys = annual_years(rows)
        found += len(ys)
        co[t] = {"site": h, "at": now, "years": ys}
        json.dump(own, open(OUT, "w"), indent=0, sort_keys=True)
    json.dump(own, open(OUT, "w"), indent=0, sort_keys=True)
    left = sum(1 for t in todo if now - (co.get(t) or {}).get("at", 0) > REFRESH_DAYS * 86400)
    print(f"done: finder {SHARD}: {asked} websites looked up in the Internet Archive, {found} archived annual reports "
          f"found; {left} still to look up", flush=True)


if __name__ == "__main__":
    main()
