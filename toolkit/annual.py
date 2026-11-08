#!/usr/bin/env python3
"""Annual report reading robot for the PSX Tracker (website batch 6, 1 Nov 2026).

Goal: the longest possible, fully checked yearly history for every company, read from the companies' own annual
reports by free AI models, with no Claude usage.

Where the reports come from (all public, no sign in):
  K  KHI Stocks annual report archive: https://www.khistocks.com/assets/annual_reports/<id>/<year>.pdf (or .html for
     the oldest years), company found by matching the archive's company name to the screener's listed name
  P  PSX filings already identified in his Drive library (files named by their PSX document number), downloaded
     straight from dps.psx.com.pk, so no Drive access is needed

Which reports are read (to spend the free quota where it adds history, newest first):
  - the basis his rules require: consolidated (owners' share) when the report has consolidated statements, else
    company only; group companies, whose history is short because KHI Stocks has only company only figures, come
    first after his held and reviewed companies, then the rest by market cap
  - only reports that add years the long history does not already hold with checked figures; each report gives two
    years of statements (current and prior), so every other year is enough, plus the oldest report for its multi year
    summary, which reaches further back than any other source

How a figure is checked before it can reach the website:
  1. the key pages are picked by toolkit/lib/prep.py (multi year summary, profit and loss, balance sheet, cash flow);
     only the pages on the right basis are sent
  2. two free models of different makers copy the figures exactly as printed; the code, not the AI, converts units
     and adds up borrowings; a third model settles any figure the first two read differently
  3. a figure is kept only when two models read the same number AND that number is printed on the pages
  4. arithmetic checks: sales less cost of sales equals gross profit, profit before tax less tax equals profit,
     owners' profit plus minority equals profit, equity and current assets within total assets, EPS sign and size
     against profit and shares, summary years against the statements; a failed check drops the figures involved
  5. the printed company name must match the ticker (also checked again when the histories are built)
  6. when the histories are rebuilt (toolkit/history_build.py), a year where two sources disagree by more than 1%
     is left out unless it is a restated comparative, and a fall in companies or figures saves nothing
Pages read by OCR (scanned reports) keep only figures that pass an arithmetic check.

Usage: python3 toolkit/annual.py [--minutes N] [--max N] [--db DIR] [--plan]
  --minutes  stop starting new reports after N minutes (default 25)
  --max      read at most N reports this run
  --plan     no downloads or AI: print how many reports are waiting, by tier
Writes data/files/report_extracts.json (checked reads, same shape as Claude's report reads), data/files/annual/
(state.json, jobs.json, khi_catalogue.json, reads/<id>.json with every model's raw read) and prints one result line.
"""
import json, os, re, subprocess, sys, tempfile, threading, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
A = sys.argv[1:]
opt = lambda k, d: A[A.index(k) + 1] if k in A else d
DB = opt("--db", os.path.join(ROOT, "data", "db"))
FILES = os.path.join(ROOT, "data", "files")
AN = os.path.join(FILES, "annual")
RAW = os.path.join(AN, "reads")
EXTRACTS = os.path.join(FILES, "report_extracts.json")
MINUTES, MAXN, PLAN = float(opt("--minutes", 25)), int(opt("--max", 100000)), "--plan" in A
# several readers side by side (8 Nov 2026): each takes its own share of companies (--shard i of --shards n); the
# workflow's merge job joins their results, so no two readers ever touch the same company
SHARD, SHARDS = int(opt("--shard", 0)), int(opt("--shards", 1))


def mine(tk):
    import zlib
    return SHARDS <= 1 or zlib.crc32(str(tk).encode()) % SHARDS == SHARD
WORKERS = int(opt("--workers", 6))  # reports read at the same time
# Page picking at once: on GitHub's 4 core machines, twelve page pickings at the same time all stalled past 15 minutes
# (each takes about 10 seconds alone), so only a few run at once; downloads and model calls stay fully parallel.
PREP = threading.Semaphore(int(opt("--prep", 2)))
KHI = "https://www.khistocks.com"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Mobile Safari/537.36"

import reader as R  # the free model panel and the printed number checks of batch 5
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from textfix import fix as tfix  # joined letters in PDF fonts (8 Nov 2026)

_http = R.http


def _slow_http(url, body=None, headers=None, timeout=100):
    """annual report pages are long: give the models up to five minutes to answer (first run timed out at 100 s)"""
    return _http(url, body, headers, max(timeout, 300) if body is not None else timeout)


R.http = _slow_http

MONEY = ["rev", "cos", "gp", "op", "fin", "pbt", "tax", "pat", "ni", "nci", "eq", "surplus", "mi", "assets", "ca", "cl",
         "cash", "sti", "cfo", "capex", "divpaid", "paidup", "dep", "adv", "inv", "nii", "nonint", "prov", "gpw", "npr", "uw",
         "invinc"]
PERSH = ["eps", "dps", "face", "px"]
POS = {"cos", "fin", "capex", "divpaid", "prov"}  # printed in brackets by some reports and plain by others: kept as positive

PROMPT = """You copy figures from the key pages of one Pakistan Stock Exchange company's annual report.
Return only JSON in exactly this shape:
{"company":"company name exactly as printed","fye":"YYYY-MM (the financial year end of this report)",
 "kind":"nonfin|bank|insurer|other_fin",
 "st":{"basis":"consolidated|company only","unit":"unit printed above the statements, like Rupees, Rupees in thousand, Rupees in million",
   "years":{"YYYY":{...fields...},"YYYY":{...}}},
 "sum":{"basis":"consolidated|company only|not stated","unit":"unit printed on the summary page",
   "years":{"YYYY":{...fields...}}},
 "surplus_in_equity":true|false|null}
Every field value is a pair: ["the number exactly as printed", "the row label exactly as printed on that row"], for
example "pat":["1,814,286","Profit for the year"]. The row label is required: a figure without its label is thrown away.
"st" is the statements (profit and loss, balance sheet, cash flow) on the basis given at the top of the pages:
both columns, this year and the prior year. "sum" is the multi year summary page (every year column), or null if no
such page is given. Year keys are the year in which each financial year ENDS (year ended 30 June 2024 is "2024").
Fields (leave a field out when the page does not print it):
 rev net sales or revenue (leave out for banks and insurers); cos cost of sales; gp gross profit; op operating profit (before finance cost); fin finance cost;
 pbt profit before tax: the line from which taxation is deducted to give profit after tax (after any levy or final tax
 lines); tax that taxation; pat profit after tax for the year; ni profit attributable to owners (equity holders)
 of the holding or parent company, only where that line is printed; nci profit attributable to non controlling interest;
 eps basic earnings per share; dps cash dividend per share for the year; bonus bonus shares percent for the year;
 eq total equity attributable to owners (or total shareholders' equity); surplus revaluation surplus; mi non controlling
 interest (balance sheet); assets total assets; ca total current assets; cl total current liabilities;
 debt a list of the interest bearing borrowing lines on the balance sheet face, each as ["number","label as printed"]
 (long term financing, its current portion, short term borrowings, running finance, lease liabilities and similar);
 cash cash and bank balances; sti short term investments; cfo net cash from operating activities; capex purchase of
 property, plant and equipment (fixed capital expenditure); divpaid dividends paid; paidup issued, subscribed and paid up
 share capital; face face (par) value per share;
 banks: dep deposits, adv advances net, inv investments, nii net mark up or interest income, nonint total non mark up income,
 prov provisions and write offs; insurers: gpw gross premium written, npr net premium revenue or net insurance revenue,
 uw underwriting result, invinc investment income; px (summary only) market price per share at year end.
Rules:
- Copy every number exactly as printed, digits and decimals, in the printed unit. Never add, convert, round or guess.
- Take each figure from the row that is exactly that figure for the WHOLE company: total profit for the year (after
  every income line, not a part such as profit from one business, one segment or continuing operations), total assets,
  total equity. When a statement shows a part result and then a final total, use the final total.
- A number printed in brackets is negative: write it with a minus sign. Costs and tax as printed (positive if printed plain).
- "surplus_in_equity": true if the revaluation surplus is part of total equity on the balance sheet, false if it sits outside.
- Use only these pages. If they are not the annual accounts of a company, return {"company":"what it is","skip":"reason"}.
PAGES:
"""


# ---------------------------------------------------------------- small helpers
def jl(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d


def jw(obj, p, **kw):
    t = p + ".tmp"
    json.dump(obj, open(t, "w"), ensure_ascii=False, **(kw or {"separators": (",", ":")}))
    os.replace(t, p)


def curl(url, out=None, data=None, timeout=240):
    cmd = ["curl", "-s", "-L", "-m", str(timeout), "-A", UA, "-w", "%{http_code}"]
    if data:
        cmd += ["-X", "POST", "-d", data]
    cmd += (["-o", out] if out else []) + [url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        return 0, ""
    if out:
        if p.returncode != 0:  # cut off part way (slow link from GitHub to Pakistan): a broken file, not a 200
            return 0, f"curl {p.returncode}"
        return int(p.stdout[-3:] or 0) if p.stdout[-3:].isdigit() else 0, ""
    code = p.stdout[-3:]
    return (int(code) if code.isdigit() else 0), p.stdout[:-3]


STOP = {"limited", "ltd", "company", "the", "co", "pakistan", "of", "and", "plc", "pvt", "private", "corporation", "corp", "inc"}


def toks(x):
    x = (x or "").lower().replace("&", " and ").replace("(pvt)", " ")
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", x).split() if w not in STOP and len(w) > 1}


def name_ok(printed, listed):
    a, b = toks(printed), toks(listed)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.5


GENERIC = {"limited", "ltd", "pakistan", "company", "co", "the", "of", "and", "industries", "mills", "corporation", "group",
           "holdings", "pvt", "private", "plc", "international", "textile", "textiles", "sugar", "cement", "power", "bank"}
ALIASES = {}  # ticker -> former names (KHI Stocks' archive names), filled at the start of a run


def close_name(printed, listed):
    """nearly every distinctive word of both names matches: strong enough to move a read to another company"""
    a = {w for w in toks(printed) if w not in GENERIC and len(w) > 2}
    b = {w for w in toks(listed) if w not in GENERIC and len(w) > 2}
    return bool(a and b) and len(a & b) / max(len(a), len(b)) >= 0.8


def name_ok2(printed, listed, pages):
    """the model's company name matches the listed name, or is its initials (PNSC, HUBCO), or most words of the listed
    name are printed on the pages themselves (models sometimes answer "unknown" or a short name)"""
    if name_ok(printed, listed):
        return True
    lt = toks(listed)
    words = [w for w in re.sub(r"[^a-z ]", " ", (listed or "").lower()).split() if w not in ("limited", "ltd", "the", "of", "and", "co")]
    p = re.sub(r"[^a-z]", "", (printed or "").lower())
    if p and len(p) >= 3 and (p == "".join(w[0] for w in words) or "".join(words).startswith(p)):
        return True
    head = re.sub(r"[^a-z0-9 ]", " ", pages.lower())
    return bool(lt) and sum(1 for w in lt if re.search(r"\b" + re.escape(w) + r"\b", head)) / len(lt) >= 0.75


# ---------------------------------------------------------------- what the tracker already holds
def load_rows(prefix):
    out = {}
    d = os.path.join(DB, "market")
    for f in sorted(os.listdir(d)):
        if re.fullmatch(prefix + r"_\d+\.json", f):
            out.update((jl(os.path.join(d, f), {}) or {}).get("rows", {}))
    return out


def screener():
    rows = (jl(os.path.join(DB, "market", "screener.json"), {}) or {}).get("rows", [])
    return {str(r["s"]).split()[0]: r for r in rows}


def important():
    out = set()
    for d in ("holdings", "reviews"):
        p = os.path.join(DB, d)
        if os.path.isdir(p):
            out |= {f[:-5] for f in os.listdir(p) if f.endswith(".json") and not re.search(r"_\d{8}$", f[:-5])}
    return out


# ---------------------------------------------------------------- report sources
def khi_catalogue(sc):
    """KHI Stocks archive: ticker -> {"id", "name", "years": {year: ext}}; refreshed every 30 days"""
    p = os.path.join(AN, "khi_catalogue.json")
    cat = jl(p, {}) or {}
    if cat.get("asOf") and (date.today() - date.fromisoformat(cat["asOf"])).days < 30 and cat.get("v", 1) >= 2:
        return cat["co"]
    code, body = curl(f"{KHI}/annual_reports/get_company_report", data="symbol=LUCK", timeout=90)
    lst = (R.jload(body) or {}).get("annual_report") if code == 200 else None
    if not isinstance(lst, list) or len(lst) < 200:
        return cat.get("co", {})
    known = {t: str(v["id"]) for t, v in ((jl(os.path.join(FILES, "job_state.json"), {}) or {}).get("khi_ids") or {}).items()
             if isinstance(v, dict) and v.get("id")}
    byid = {str(x.get("brcode")): x for x in lst}
    names = {t: toks(r.get("n")) for t, r in sc.items()}
    co = {}
    for x in lst:
        a = toks(x.get("company"))
        if not a:
            continue
        hits = []
        for t, b in names.items():
            if not b:
                continue
            j = len(a & b) / len(a | b)
            if a == b or j >= 0.75:
                hits.append((j, t))
        hits.sort(reverse=True)
        # one clear match only: two listings with the same words (or near) are left out
        if hits and (len(hits) == 1 or hits[0][0] - hits[1][0] >= 0.2):
            co.setdefault(hits[0][1], []).append(str(x["brcode"]))
    out = {}
    for t, ids in co.items():
        i = known.get(t) if known.get(t) in ids else (ids[0] if len(ids) == 1 else None)
        if not i:
            # the same company listed twice on KHI Stocks (8 Nov 2026: Fauji Foods, Unity Foods, J.A. Textile and others
            # were never read): when every listing carries exactly the company's name, the one with the most years
            exact = [x for x in ids if toks(byid[x].get("company")) == names.get(t)]
            if len(exact) == len(ids):
                i = max(exact, key=lambda x: len(re.findall(r"\d{4}", str(byid[x].get("years") or ""))))
        if not i:
            continue
        ys = sorted({int(y) for y in re.findall(r"\d{4}", str(byid[i].get("years") or ""))})
        out[t] = {"id": i, "name": byid[i].get("company"), "years": ys}
    for t, i in known.items():  # the ids Claude confirmed by hand on 22 to 27 Oct 2026
        if t not in out and i in byid:
            out[t] = {"id": i, "name": byid[i].get("company"),
                      "years": sorted({int(y) for y in re.findall(r"\d{4}", str(byid[i].get("years") or ""))})}
    jw({"asOf": date.today().isoformat(), "v": 2, "n": len(out), "co": out}, p, indent=0)
    return out


def psx_docs():
    """annual reports in his Drive library that are PSX filings (file named by the PSX document number)"""
    out = {}
    lib = [r for r in jl(os.path.join(FILES, "library_index.json"), []) or []
           if r.get("kind") == "A" and r.get("tk") and r.get("y")]
    # the robot's own index of everything in the folder (toolkit/drive_index.py, 8 Nov 2026): reports he uploaded
    # after 27 Oct 2026 had never been used
    seen = {r.get("id") for r in lib}
    for fid, r in ((jl(os.path.join(FILES, "drive_ident.json"), {}) or {}).items()):
        if fid not in seen and r.get("kind") == "A" and r.get("tk") and r.get("y") and not r.get("err"):
            lib.append({"id": fid, "title": r.get("title"), "tk": r["tk"], "kind": "A", "y": r["y"]})
    # his other Drive annual reports (2 Nov 2026), once the PSX Library folder is shared "anyone with the link":
    # checked with one test download per run, so nothing is tried (and no attempt wasted) while it is still private
    withid = [r for r in lib if r.get("id")]
    if any(drive_open(r["id"]) for r in withid[:6]):  # any one opening shows the folder is shared
        for r in withid:
            out.setdefault(r["tk"], {}).setdefault(int(r["y"]), (r["id"], DRIVE.format(r["id"])))
    for r in lib:  # after Drive, so his Drive copy wins over the PSX site
        m = re.fullmatch(r"(\d{5,7})\.pdf", r.get("title") or "")
        if m and not r.get("id"):  # PSX site only when the file is not in his Drive
            out.setdefault(r["tk"], {}).setdefault(int(r["y"]), (r["id"], f"https://dps.psx.com.pk/download/document/{m.group(1)}.pdf"))
    return out


DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"


def drive_open(fid):
    w = tempfile.mkdtemp()
    try:
        code, _ = curl(DRIVE.format(fid), os.path.join(w, "t.pdf"), timeout=120)
        return code == 200 and open(os.path.join(w, "t.pdf"), "rb").read(5) == b"%PDF-"
    except Exception:
        return False
    finally:
        subprocess.run(["rm", "-rf", w])


# ---------------------------------------------------------------- planning
RECHECK = set()  # (ticker, year) the tracker's audit left empty because sources disagree: read that year's report


def site_json(name):
    """one market document from his locked tracker site, signed in with SITE_PASSWORD (an Actions secret);
    None when the secret is missing, the document does not exist or the site cannot be reached"""
    pw = os.environ.get("SITE_PASSWORD")
    if not pw:
        return None
    import hashlib
    cookie = "psx_auth=" + hashlib.sha256(("psx-tracker:" + pw).encode()).hexdigest()
    try:
        p = subprocess.run(["curl", "-s", "-m", "60", "-H", "Cookie: " + cookie, "-w", "\n%{http_code}",
                            f"https://psx-tracker.pages.dev/db/market/{name}.json"], capture_output=True, text=True, timeout=90)
        body, _, code = p.stdout.rpartition("\n")
        d = R.jload(body) if code == "200" else None
    except Exception:
        d = None
    return d.get("data", d) if isinstance(d, dict) else None


def load_recheck():
    """the audit's re-read list (and, from 3 Nov 2026, the coverage map's gap years) from his tracker site;
    tickers and years only. An empty set when the secret is missing or the site cannot be reached."""
    d = site_json("recheck")
    return {(str(t), int(y)) for t, y in (d or {}).get("recheck", []) if str(y).isdigit()}


def active_only(sc):
    """only companies that traded in the last 90 days (8 Nov 2026): delisted, merged and long untraded companies
    (Suzuki, Shell, FFBL and about 270 more) had stayed in the reading list. Trading comes from the tracker's daily
    market prices (pxstats). Safeguards: a broken or short price file changes nothing (the saved list is used), and
    a company that trades again comes back on the next run, since nothing is deleted, it is only left unread."""
    ap = os.path.join(DB, "market", "active.json")
    d = site_json("pxstats")
    rows = (d or {}).get("rows") or {}
    if len(rows) >= 300:
        last = max((r.get("d") or "") for r in rows.values())
        from datetime import date as _d, timedelta
        cut = (_d.fromisoformat(last) - timedelta(days=90)).isoformat() if last else ""
        act = sorted(t for t, r in rows.items() if (r.get("d") or "") >= cut)
        jw({"asOf": last, "note": "companies traded in the last 90 days (tickers only)", "s": act}, ap, indent=0)
    act = set((jl(ap, {}) or {}).get("s") or [])
    if len(act) < 300:
        print("universe: no trading list, every listed company kept", flush=True)
        return sc
    out = {t: r for t, r in sc.items() if t in act}
    print(f"universe: {len(out)} companies traded in the last 90 days; {len(sc) - len(out)} delisted, merged or untraded left out", flush=True)
    return out


def refresh_flags():
    """Bring the reader's picture of the tracker up to date every run (3 Nov 2026: it had been copied once on 1 Nov,
    so the reader planned from stale years and did not know newer group companies). Only flags leave the private
    tracker: per company its basis, years, and whether profit and equity are filled (1 or None); no figures. The
    company list keeps name, market cap and market price, as before. Nothing changes when the site cannot be read
    or the fresh copy looks broken (far fewer companies than the copy here)."""
    rows = {}
    for i in range(1, 13):
        d = site_json(f"long_{i}")
        if d is None:
            break
        rows.update(d.get("rows") or {})
    lp = os.path.join(DB, "market", "long_1.json")
    old = (jl(lp, {}) or {}).get("rows", {})
    if rows and len(rows) >= 0.9 * len(old):
        flag = lambda xs: [1 if x is not None else None for x in (xs or [])]
        jw({"note": "Which years already hold checked profit and equity figures (1) in the tracker, so the reader skips "
                    "them; refreshed from the tracker every run", "asOf": date.today().isoformat(),
            "rows": {t: {"basis": r.get("basis"), "fy": r.get("fy") or [], "ni": flag(r.get("ni")), "eq": flag(r.get("eq"))}
                     for t, r in rows.items() if isinstance(r, dict)}}, lp, indent=0)
    sc = site_json("screener")
    sp = os.path.join(DB, "market", "screener.json")
    srows = (sc or {}).get("rows") or []
    if len(srows) >= 0.9 * len((jl(sp, {}) or {}).get("rows", [])):
        jw({"asOf": (sc or {}).get("asOf"), "note": "listed companies (name, market cap, market price) from the tracker",
            "rows": [{"s": r.get("s"), "n": r.get("n"), "mc": r.get("mc"), "p": r.get("p")} for r in srows]}, sp, indent=0)
    return len(rows), len(srows)


def covered(tk, longrows, extracts):
    """years that already hold a checked profit figure on the company's basis: the long history (profit and equity),
    report statements, and multi year summary years on the same basis (one summary page gives 6 to 10 years)"""
    r = longrows.get(tk) or {}
    group = r.get("basis") == "group"
    fy, ni, eq = r.get("fy") or [], r.get("ni") or [], r.get("eq") or []
    cov = {y for i, y in enumerate(fy) if i < len(ni) and ni[i] is not None and i < len(eq) and eq[i] is not None}
    for d in extracts:
        for b in ("C", "S"):
            # a group company's year is filled only by group accounts (3 Nov 2026: company only extracts from KHI Stocks
            # had marked 124 group years as done, so their full website reports were never read); its early company only
            # years that the tracker accepted are already in the flags above
            if (b == "C") == bool(d.get("cons")) and not (group and b == "S" and not d.get("nogroup")):
                # only years whose profit was read (8 Nov 2026: a read that gave only assets or cash had marked the
                # year as done, so the year's full report on his Drive or the company's website was never read)
                # a group year needs the owners' share (a consolidated total alone leaves the history empty)
                cov |= {int(y) for y, row in (d.get(b) or {}).items() if str(y).isdigit() and isinstance(row, dict)
                        and (row.get("ni") is not None or (row.get("pat") is not None and not (group and b == "C")))}
        for k in ("SUM", "SUM_C", "SUM_X"):
            sm = d.get(k) or {}
            ok = sm.get("basis") == "consolidated" if group else sm.get("basis") in ("standalone", "unclear")
            if ok:
                cov |= {int(y) for y, row in (sm.get("years") or {}).items()
                        if str(y).isdigit() and isinstance(row, dict) and (row.get("ni") is not None or row.get("pat") is not None)}
    return cov - {y for t, y in RECHECK if t == tk}


WEB = {}       # ticker -> {year: url}: full annual reports on the companies' own websites (toolkit/websites.py)
ARC = {}       # ticker -> {year: url}: the same, as kept by the Internet Archive (toolkit/archive_finder.py)
GROUPS = set()  # companies whose histories must be consolidated (group basis)
STATE = {}


def candidates(tk, cat, docs):
    cands = {}
    for y in (cat.get(tk) or {}).get("years", []):
        cands[y] = ("khi", f"{KHI}/assets/annual_reports/{cat[tk]['id']}/{y}")
    if tk in BAD_KHI:  # KHI Stocks files this ticker under another company's reports (8 Nov 2026)
        cands = {}
    # full reports (his Drive library, then the company's own website) carry the consolidated statements that KHI
    # Stocks' short extracts lack: they replace KHI for group companies, and any source whose read failed for good
    alts = [(int(y), "psx" if "dps.psx" in url else "drive", url) for y, (fid, url) in (docs.get(tk) or {}).items()]
    alts += [(int(y), "web", url) for y, url in (WEB.get(tk) or {}).items()]
    # the company's own reports kept by the Internet Archive (8 Nov 2026), for years its website no longer shows
    alts += [(int(y), "arc", url) for y, url in (ARC.get(tk) or {}).items() if y not in (WEB.get(tk) or {})]
    for y, src, url in sorted(alts, key=lambda a: a[1] != "drive"):  # Drive first: his own copy always downloads
        cur = cands.get(y)
        st = STATE.get(f"{cur[0]}:{tk}:{y}") or {} if cur else {}
        if cur is None or (tk in GROUPS and cur[0] == "khi") or (not st.get("done") and (st.get("tries", 0) >= 2 or st.get("retry"))):
            cands[y] = (src, url)
    return cands


BAD_KHI = set()


def second_chance(state):
    """one more try for reports given up for reasons that can change (8 Nov 2026: 2,134 reports had been dropped for
    good, so the reader sat idle). Scanned reports are now read with OCR; failed downloads, missing statement pages
    and reads where no figure passed get one fresh go with models that have not read them. Once only per report
    (the "second" mark), so nothing can loop. KHI tickers that twice gave another company's report lose KHI."""
    again = re.compile(r"scanned|download failed|no statement pages|no figure passed|no two models|page picking|broken PDF")
    n = 0
    bad = {}
    for k, st in state.items():
        why = st.get("why") or ""
        if k.startswith("khi:") and why.startswith("company name does not match"):
            bad[k.split(":")[1]] = bad.get(k.split(":")[1], 0) + 1
        if not st.get("done") and st.get("tries", 0) >= 2 and not st.get("second") and again.search(why):
            st.update(retry=1, second=1, tries=1)
            n += 1
    BAD_KHI.update(t for t, c in bad.items() if c >= 2)
    return n


def requeue_8nov(state, ext):
    """once (8 Nov 2026): reports read before the joined letters fix and the profit rules get one more go. Reads that
    gave no profit, and reports given up for reasons those fixes change (statement pages not found, not annual
    accounts, no figure passed, no company name found). Saved model answers are reused where the pages are unchanged,
    so most cost no model calls. The old figures stay until a better read replaces them; once per report, so nothing
    loops. Returns how many were requeued."""
    mp = os.path.join(AN, "migrations.json")
    done = jl(mp, {}) or {}
    if done.get("requeue_8nov"):
        return 0
    fixable = re.compile(r"no statement pages|no figure passed|not annual accounts|year end not readable|no two models|"
                         r"fewer than two|page picking|^error:|company name does not match: (not|no |company name|cannot|unknown)", re.I)
    n = 0
    for k, st in state.items():
        if st.get("redo8"):
            continue
        d = ext.get(k) or {}
        if st.get("done"):
            blk = d.get("C") if d.get("cons") else d.get("S")
            if d.get("by") == "ai" and isinstance(blk, dict) and not any(
                    isinstance(r, dict) and (r.get("ni") is not None or r.get("pat") is not None) for r in blk.values()):
                st.pop("done", None)
                st.update(retry=1, tries=1, redo8=1)
                n += 1
        elif fixable.search(st.get("why") or "") and "interim" not in (st.get("why") or ""):
            st.update(retry=1, tries=1, redo8=1)
            n += 1
    done["requeue_8nov"] = f"{date.today().isoformat()}: {n} reports"
    jw(done, mp, indent=0)
    return n


def requeue_groups(state, ext, longrows):
    """once (8 Nov 2026): group companies' reports read before the reader noted whether a report has group accounts
    at all (standalone reads) or before owners' profit was taken from statements with no minority interest
    (consolidated reads with total profit only). Saved model answers are reused; once per report."""
    mp = os.path.join(AN, "migrations.json")
    done = jl(mp, {}) or {}
    if done.get("requeue_groups"):
        return 0
    groups = {t for t, r in longrows.items() if (r or {}).get("basis") == "group"}
    n = 0
    for k, d in ext.items():
        st = state.get(k)
        if not st or not st.get("done") or d.get("by") != "ai" or d.get("ticker") not in groups or "pages" in d:
            continue
        rows = (d.get("C") if d.get("cons") else d.get("S")) or {}
        need = (not d.get("cons")) or any(isinstance(r, dict) and r.get("ni") is None and r.get("pat") is not None
                                          for r in rows.values())
        if need:
            st.pop("done", None)
            st.update(retry=1, tries=1, redo8=1)
            n += 1
    done["requeue_groups"] = f"{date.today().isoformat()}: {n} reports"
    jw(done, mp, indent=0)
    return n


def audit_years(tk, y, cands):
    """the audit years this report settles: its own year, and last year when that year has no report of its own
    (every report shows last year's figures beside this year's)"""
    return {x for t, x in RECHECK if t == tk and (x == y or (x == y - 1 and x not in cands))}


def audit_due(tk, y, cands, st):
    """a report already read once that the audit wants read again (2 Nov 2026 fix: before this, a finished report was
    never read twice, so 59 flagged years waited forever). The re-read sets the earlier model answers aside and asks
    two models that have not read it (read_one); the first pass that day reused saved answers, so its old "audited"
    mark does not count. Once per flagged year, so it can never loop: if the fresh
    read still disagrees, the year stays an empty slot."""
    if not st.get("done") or st.get("tries", 0) >= 2:
        return False
    return bool(audit_years(tk, y, cands) - set(st.get("reread") or []))


def audit_counts(sc, cat, docs, state):
    """the audit's flagged years by where they stand, so the log never repeats one number while nothing moves"""
    n = {"to re-read": 0, "re-read": 0, "waiting for a first read": 0, "no usable report": 0}
    for tk, x in RECHECK:
        cands = candidates(tk, cat, docs) if tk in sc else {}
        ys = [y for y in (x, x + 1) if y in cands and (y == x or x not in cands)]
        sts = [(y, state.get(f"{cands[y][0]}:{tk}:{y}") or {}) for y in ys]
        if any(x in (st.get("reread") or []) for _, st in sts):
            n["re-read"] += 1
        elif any(audit_due(tk, y, cands, st) for y, st in sts):
            n["to re-read"] += 1
        elif any(not st.get("done") and st.get("tries", 0) < 2 for _, st in sts):
            n["waiting for a first read"] += 1
        else:
            n["no usable report"] += 1  # no report for that year or the next, or the report could not be read
    return n


def audit_summary(sc, cat, docs, state):
    n = audit_counts(sc, cat, docs, state)
    return f"{len(RECHECK)} company years flagged: " + ", ".join(f"{v} {k}" for k, v in n.items())


def next_job(tk, sc, longrows, cat, docs, state, extracts, imp):
    """the one report of this company most worth reading next, or None. Newest missing year first: its statements give
    two years and its summary page often 6 to 10 more, so the next pick jumps past everything it covered."""
    cands = candidates(tk, cat, docs)
    if not cands or tk not in sc:
        return None
    cov = covered(tk, longrows, extracts)
    group = (longrows.get(tk) or {}).get("basis") == "group"
    tier = 0 if tk in imp or any(t == tk for t, _ in RECHECK) else (1 if group else 2)
    ys = sorted(cands, reverse=True)
    for y in ys:
        st = state.get(f"{cands[y][0]}:{tk}:{y}") or {}
        if audit_due(tk, y, cands, st):
            # the audit found two sources disagreeing on this year: read the report once more with fresh models
            src, url = cands[y]
            return (tier, -float(sc[tk].get("mc") or 0), -y, f"{src}:{tk}:{y}", tk, y, src, url, "audit")
        if st.get("retry") and not st.get("done") and y not in cov:
            src, url = cands[y]
            return (tier, -float(sc[tk].get("mc") or 0), -y, f"{src}:{tk}:{y}", tk, y, src, url, "retry")
        if st.get("done") or st.get("tries", 0) >= 2:
            cov |= {y} if st.get("done") else set()
            continue
        older = [x for x in ys if x < y]
        # read it when its own year is missing, when the prior year is missing and has no report, or when it is the
        # oldest report and its summary may reach years nothing else covers
        if y not in cov or ((y - 1) not in cov and (y - 1) not in cands) or (not older and min(cov or {9999}) > y - 5):
            src, url = cands[y]
            return (tier, -float(sc[tk].get("mc") or 0), -y, f"{src}:{tk}:{y}", tk, y, src, url)
    return None


def plan(sc, longrows, cat, docs, state, extracts_by_tk):
    imp = important()
    jobs = [j for tk in sorted(set(cat) | set(docs) | set(WEB) | set(ARC)) if mine(tk)
            for j in [next_job(tk, sc, longrows, cat, docs, state, extracts_by_tk.get(tk, []), imp)] if j]
    jobs.sort()
    return jobs, imp


# ---------------------------------------------------------------- getting the key pages
def html_to_pages(src, dst):
    s = open(src, encoding="latin-1", errors="ignore").read()
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h\d|li|pre)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", "   ", s)
    s = re.sub(r"<[^>]+>", "", s)
    import html as H
    s = H.unescape(s).replace("\xa0", " ")
    out, n = [], 0
    head = re.compile(r"^\s*(balance sheet|profit and loss|income statement|cash flow|statement of (changes|financial|profit|comprehensive)"
                      r"|notes to the|(six|ten) years?|years? at a glance|financial highlights|key (operating|financial)|directors'? report"
                      r"|auditors'? report|consolidated (balance|profit|cash))", re.I)
    for ln in s.splitlines():
        if head.search(ln) and len(ln.strip()) < 110 and n > 8:
            out.append("\f")
            n = 0
        out.append(ln.rstrip() + "\n")
        n += 1
    open(dst, "w").write("".join(out))


def get_pages(jid, src, url, work):
    """download one report and keep its key pages -> (pages text, prep info) or (None, reason)"""
    pdf = os.path.join(work, "r.pdf")
    if src == "khi":
        for _ in range(2):  # one retry: downloads from Pakistan sometimes stall
            code, _ = curl(url + ".pdf", pdf, timeout=600)
            if code in (200, 404):
                break
        ok = code == 200 and os.path.exists(pdf) and open(pdf, "rb").read(5) == b"%PDF-"
        if not ok:
            code, _ = curl(url + ".html", os.path.join(work, "r.html"), timeout=120)
            if code != 200 or os.path.getsize(os.path.join(work, "r.html")) < 5000:
                return None, f"not on KHI Stocks ({code})"
            pdf = os.path.join(work, "r.txt")
            html_to_pages(os.path.join(work, "r.html"), pdf)
    else:
        for _ in range(2):
            code, _ = curl(url, pdf, timeout=600)
            if code in (200, 404):
                break
        if code != 200 or open(pdf, "rb").read(5) != b"%PDF-":
            return None, f"{ {'psx': 'PSX', 'web': 'Company website', 'arc': 'Internet Archive'}.get(src, 'Drive') } download failed ({code})"
    if pdf.endswith(".pdf") and os.path.getsize(pdf) > 80e6:
        return None, "file too large"
    return _pages(pdf, work)


def _pages(pdf, work):
    if pdf.endswith(".pdf"):
        info0 = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, errors="ignore")
        if info0.returncode != 0 or not re.search(r"Pages:\s+\d+", info0.stdout):
            return None, "broken PDF (download incomplete); tried again next run"
        # a scanned report (no text layer) needs every page OCRed, which costs 10 or more GitHub minutes: left for later
        t = tfix(subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, errors="ignore").stdout).split("\f")
        textp = sum(1 for x in t if len(x.strip()) > 200)
        if len(t) > 40 and textp < len(t) * 0.3:
            # scanned (8 Nov 2026): read with OCR, at most two at a time so normal reports keep flowing
            if len(t) > 450:
                return None, "scanned report too long for OCR"
            return _prep(pdf, work, OCR, 1800)
        b, info = _prep(pdf, work, PREP, 600)
        if b is not None and isinstance(info, dict):
            # the company's name as printed on the cover and first pages (8 Nov 2026): 133 reports were given up as
            # "company name not found in provided pages" because the statement pages alone did not print it
            info["cover"] = re.sub(r"[ \t]{3,}", "   ", "\n".join(x.strip() for x in t[:3] if x.strip()))[:1500]
        return b, info
    return _prep(pdf, work, PREP, 600)


OCR = threading.Semaphore(2)


def _prep(pdf, work, sem, limit):
    out = os.path.join(work, "bundle.txt")
    try:
        t0 = time.time()
        with sem:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "toolkit", "lib", "prep.py"), pdf, out],
                               capture_output=True, text=True, timeout=limit, cwd=work,
                               env=dict(os.environ, OMP_THREAD_LIMIT="1"))  # OCR: one core each
        print(f"pages {os.path.basename(work)} {round(time.time() - t0)}s", flush=True)
    except subprocess.TimeoutExpired:
        return None, "page picking took too long (scanned report)"
    info = R.jload((p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else "") or {}
    if p.returncode != 0 or not os.path.exists(out):
        return None, "page picking failed"
    return open(out, errors="ignore").read(), info


def pick_pages(bundle, cons):
    """only the pages on the basis to read (consolidated when the report has them) plus the summary"""
    parts = re.split(r"(?m)^(===== \S+ page \d+ =====)$", bundle)
    keep = [parts[0].strip()]
    for i in range(1, len(parts) - 1, 2):
        k = parts[i].split()[1]
        if k == "SUM" or (k.endswith("C") if cons else k.endswith("S")):
            keep.append(parts[i] + parts[i + 1])
    text = "\n".join(keep)
    return re.sub(r"[ \t]{3,}", "   ", text)[:60000]


# ---------------------------------------------------------------- one read -> figures in Rs m
def factor(unit):
    u = (unit or "").lower()
    if re.search(r"billion|\bbn\b", u):
        return 1000.0
    if re.search(r"million|\bmn\b|\bmln\b|\bm\b", u):
        return 1.0
    if re.search(r"thousand|'000|000|\bk\b", u):
        return 0.001
    if re.search(r"rupee|\brs\.?\b|pkr", u):
        return 1e-6
    return None


def yearkey(y):
    m = re.fullmatch(r"(19|20)\d\d", str(y).strip())
    return m.group(0) if m else None


# what each figure's row label must (allow) and must not (ban) say; the code checks this, not the model
LBL = {
    "rev": (r"\b(sales|revenue|revenues|turnover)\b", r"cost of|gross|other (income|revenue)|segment"),
    "cos": (r"cost of (sales|revenue|goods)|cost of sale", r"^$"),
    "gp": (r"gross (profit|loss)", r"^$"),
    "op": (r"operating (profit|loss|result)|(profit|loss) from operations", r"before tax|segment|discontinued"),
    "fin": (r"finance (cost|costs|charges|expense)|financial charges|financial cost|mark ?up|interest expense|borrowing cost", r"income|earned"),
    "pbt": (r"before (taxation|tax|income tax|final tax|levy|levies)", r"operations|continuing|discontinued|segment|refinery"),
    "tax": (r"tax", r"before|after|asset|liabilit|payable|refund|paid"),
    # 8 Nov 2026: "after income tax", "after final tax and levies" and a standalone "attributable to ordinary
    # shareholders" are the whole profit; 57 reports had all their models agree on profit and still lost it here.
    # "attributable" to owners, the parent or minority stays banned, and so does profit "available for appropriation"
    "pat": (r"((profit|loss|income|earnings).*(after (income |final |minimum |and |turnover |super |levy |levies |tax |taxation )*"
            r"(tax|taxes|taxation|levy|levies)|for (the )?(year|period)))|net (profit|loss|income)",
            r"before|operations|continuing|discontinued|refinery|segment|comprehensive|per share|retained|"
            r"attributable.*(owners|equity holders|holding|parent|non ?controlling|minority)|appropriation|available|"
            r"prior period|unappropriated|transferred|brought|carried"),
    "ni": (r"owners|equity holders|shareholders|holding company|parent", r"non ?controlling|minority|comprehensive|per share"),
    "nci": (r"non ?controlling|minority", r"^$"),
    "eps": (r"per share", r"dividend|book value|break ?up|market"),
    "dps": (r"dividend", r"paid|payable|received"),
    "bonus": (r"bonus", r"^$"),
    "eq": (r"equity|shareholders funds|net assets|capital and reserves",
           r"liabilities and equity|equity and liabilities|non ?controlling|minority|surplus|changes in equity|accounted"),
    "surplus": (r"surplus", r"^$"),
    "mi": (r"non ?controlling|minority", r"^$"),
    "assets": (r"total assets", r"non ?current|current assets"),
    "ca": (r"current assets", r"non ?current"),
    "cl": (r"current liabilities", r"non ?current"),
    "cash": (r"cash and bank|cash and cash equivalents|cash in hand|bank balances|cash at bank", r"beginning|end of|start of|net (increase|decrease)"),
    "sti": (r"short ?term investment", r"^$"),
    "cfo": (r"operating activities", r"investing|financing"),
    "capex": (r"property|fixed assets|capital expenditure|capital work|operating assets", r"disposal|sale|proceeds"),
    "divpaid": (r"dividend", r"received|income"),
    "paidup": (r"paid ?up|share capital|issued", r"authori[sz]ed|reserve"),
    "face": (r"each|face|par value|nominal", r"^$"),
    "dep": (r"deposits", r"interbank|placement|lending|security deposit"),
    "adv": (r"advances", r"against|prepayment|deposits and"),
    "inv": (r"investments", r"income|property|associat|subsidiar"),
    "nii": (r"net (mark ?up|interest|spread)|(mark ?up|interest).*\bnet\b", r"^$"),
    "nonint": (r"non ?(mark ?up|interest|funded)|other income", r"expense"),
    "prov": (r"provision|credit loss|impairment|write ?off", r"tax"),
    "gpw": (r"gross (premium|written|contribution)", r"^$"),
    "npr": (r"net (premium|insurance revenue|contribution)|insurance revenue", r"expense"),
    "uw": (r"underwriting|insurance service result", r"^$"),
    "invinc": (r"investment income", r"^$"),
    "px": (r"market|price|quoted", r"^$"),
    "debt": (r"borrowing|financing|loan|finance|lease|running|redeemable|sukuk|term finance|musharak|murabah|debt|certificates",
             r"deposit|payable|creditor|deferred|income|grant|receivable"),
}
TOTALS = {"pat", "assets", "eq", "cfo"}  # must be the bottom matching row of its statement page
STOPW = {"the", "and", "for", "from", "with", "year", "rupees", "note", "total", "net", "of", "to", "in", "on"}


def norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", tfix(t or "").lower().replace("-", " "))).strip()


def label_ok(field, label):
    a, b = LBL.get(field, (r".", r"^$"))
    l = norm(label)
    return bool(l) and re.search(a, l) is not None and re.search(b, l) is None


def page_rows(pages):
    """[(page number, kind, normalised text, numbers on the row)] for every line of the pages sent"""
    rows, pg, kind = [], 0, ""
    for ln in pages.splitlines():
        m = re.match(r"===== (\S+) page (\d+) =====", ln)
        if m:
            kind, pg = m.group(1), int(m.group(2))
            continue
        rows.append((pg, kind, norm(ln), R.numbers(ln)))
    return rows


def row_proof(field, value, label, rows):
    """the number sits on a row whose words (with the row above, for wrapped labels) carry most of the label's words;
    for totals the row must be the last matching row of its statement page"""
    if rows is None:
        return True
    lw = [w for w in norm(label).split() if w not in STOPW and len(w) > 2] or norm(label).split()
    if not lw:
        return False
    v = abs(value)
    for i, (pg, kind, text, nums) in enumerate(rows):
        if not any(abs(n - v) <= max(1e-9, v * 1e-9) for n in nums):
            continue
        ctx = text + " " + (rows[i - 1][2] if i and rows[i - 1][0] == pg else "")
        if sum(1 for w in lw if re.search(r"\b" + re.escape(w) + r"\b", ctx)) < max(1, round(0.6 * len(lw))):
            continue
        # the words actually printed on the row must pass the same ban list (a model may give a tidier label than the
        # row really has, e.g. "Profit before taxation" for "Profit before taxation from refinery operations")
        if field in LBL and LBL[field][1] != r"^$" and re.search(LBL[field][1], text):
            continue
        if field in TOTALS and kind != "SUM":
            a, b = LBL[field]
            # a matching row further down the page with other numbers is the real total (8 Nov 2026: a later row
            # sharing this row's numbers, such as the statement printed twice or profit repeated in the changes in
            # equity, no longer rejects the figure)
            mine = [abs(n) for n in nums]
            later = [r for r in rows[i + 1:] if r[0] == pg and r[3] and re.search(a, r[2]) and not re.search(b, r[2])
                     and not any(abs(abs(n) - m) <= max(1e-9, m * 1e-9) for n in r[3] for m in mine)]
            if later:
                continue
        return True
    return False


def pair(v):
    """a model's ["number", "label"] (or {"v":..,"label":..}) -> (number, label); a bare number has no label"""
    if isinstance(v, (list, tuple)) and len(v) >= 2 and not isinstance(v[0], (list, dict)):
        return R.fnum(v[0]), str(v[1] or "")
    if isinstance(v, dict):
        return R.fnum(v.get("v", v.get("value"))), str(v.get("label") or "")
    return R.fnum(v), ""


def convert(block, nums, rows=None):
    """{year: fields as printed} -> {year: {field: value in Rs m}}. A figure is kept only when its number is printed,
    its row label fits the figure (label rules above) and the number sits on that labelled row (row proof)."""
    out = {}
    if not isinstance(block, dict):
        return out
    f = factor(block.get("unit"))
    for y, row in (block.get("years") or {}).items():
        y = yearkey(y)
        if not y or not isinstance(row, dict):
            continue
        r = {}
        for k, v in row.items():
            if k == "debt" and isinstance(v, list) and f:
                items = [pair(x) for x in v]
                if items and all(x is not None and R.printed(x, nums) and label_ok("debt", l) and row_proof("debt", x, l, rows)
                                 for x, l in items):
                    r["debt"] = round(sum(abs(x) for x, _ in items) * f, 3)
                continue
            x, label = pair(v)
            if x is None or not R.printed(x, nums) or k not in LBL:
                continue
            if not label_ok(k, label) or not row_proof(k, x, label, rows):
                continue
            if k in MONEY and f:
                r[k] = round((abs(x) if k in POS else x) * f, 3)
            elif k in PERSH or k == "bonus":
                r[k] = x
        if r:
            out[y] = r
    return out


def same(a, b, k):
    if k in PERSH or k == "bonus":
        return abs(a - b) <= 0.005
    return abs(a - b) <= max(1e-6, abs(a) * 1e-9)  # the same printed number: exact


def facts(j, nums, cons, sumbasis, rows=None):
    """the code decides the basis: the statements sent are all on one basis; the summary's basis comes from its page
    heading when every summary page says the same, otherwise from the model (and must then agree like any figure)"""
    if not isinstance(j, dict) or j.get("skip"):
        return {}
    f = {}
    st = j.get("st") or {}
    b = "C" if cons else "S"
    for y, r in convert(st, nums, rows).items():
        for k, v in r.items():
            f[f"{b}|{y}|{k}"] = v
    sm = j.get("sum") or {}
    sb = str(sm.get("basis") or "").lower()
    sb = sumbasis or ("consolidated" if sb.startswith("consol") else "standalone" if sb.startswith("company") else "unclear")
    for y, r in convert(sm, nums, rows).items():
        for k, v in r.items():
            f[f"SUM:{sb}|{y}|{k}"] = v
    return f


def agree(fs):
    ok, bad = {}, set()
    for k in set().union(*[set(x) for x in fs]) if fs else set():
        vals = [x[k] for x in fs if k in x]
        fld = k.rsplit("|", 1)[1]
        hit = next((v for i, v in enumerate(vals) if any(same(v, w, fld) for w in vals[i + 1:])), None)
        if hit is not None:
            ok[k] = hit
        elif len(vals) >= 2:
            bad.add(k)
    return ok, bad


# ---------------------------------------------------------------- arithmetic checks on the agreed figures
def close(a, b, tol=0.02, absmin=1.0):
    return abs(a - b) <= max(absmin, tol * max(abs(a), abs(b)))


def eps_fits(r, prof, rows):
    """EPS matches profit for a share count from this year's or the other column's paid up capital, or between them
    (a bonus issue restates last year's EPS on this year's shares; a rights issue gives a weighted count in between)"""
    caps = sorted({x["paidup"] for x in rows.values() if x.get("paidup", 0) > 0} | {r["paidup"]})
    faces = [r["face"]] if r.get("face") else [10, 5, 1, 2, 2.5, 100]
    implied = prof / r["eps"]
    for f in faces:
        lo, hi = caps[0] / f, caps[-1] / f
        if implied > 0 and lo * 0.9 <= implied <= hi * 1.1:
            return True
    return False


def checks(rows, kind, ocr):
    """drop figures that fail an identity; with OCR pages keep only figures an identity confirms"""
    dropped = []
    for y, r in rows.items():
        good = set()
        def drop(keys, why):
            for k in keys:
                if k in r:
                    r.pop(k)
                    dropped.append(f"{y} {k}: {why}")
        if all(k in r for k in ("rev", "cos", "gp")):
            if close(r["rev"] - abs(r["cos"]), r["gp"], 0.002, 0.05):
                good |= {"rev", "cos", "gp"}
            else:
                drop(("rev", "cos", "gp"), "sales less cost of sales is not gross profit")
        if "ni" in r and "pat" in r and close(r["ni"] + r.get("nci", 0), r["pat"], 0.002, 0.05):
            good |= {"ni", "nci", "pat"}  # owners' share plus minority add up to the profit both models read
        if all(k in r for k in ("pbt", "tax", "pat")):
            t = abs(r["tax"])
            if close(r["pbt"] - t, r["pat"], 0.002, 0.05):
                r["tax"] = t       # an expense, as the earlier reads store it
                good |= {"pbt", "tax", "pat"}
            elif close(r["pbt"] + t, r["pat"], 0.002, 0.05):
                r["tax"] = -t      # a tax credit
                good |= {"pbt", "tax", "pat"}
            else:
                drop(("pbt", "tax") if "pat" in good else ("pbt", "tax", "pat", "ni", "nci", "eps"),
                     "profit before tax less tax is not profit")
        if "ni" in r and "pat" in r:
            if close(r["ni"] + r.get("nci", 0), r["pat"], 0.002, 0.05):
                good |= {"ni", "nci"}
            else:
                drop(("ni", "nci"), "owners' profit plus minority is not profit")
        if "assets" in r:
            for k in ("eq", "ca"):
                if k in r and r[k] > r["assets"] * 1.001:
                    drop((k, "assets"), f"{k} above total assets")
            if "eq" in r and "cl" in r and r["eq"] + r["cl"] <= r["assets"] * 1.001:
                good |= {"eq", "cl", "assets"}
        if "gp" in r and "rev" in r and kind == "nonfin" and r["gp"] > r["rev"] * 1.001:
            drop(("gp",), "gross profit above sales")
        prof = r.get("ni", r.get("pat"))
        if "eps" in r and prof is not None and abs(prof) > 1 and r["eps"] and (r["eps"] > 0) != (prof > 0):
            drop(("eps",), "EPS and profit have opposite signs")
        # EPS must match profit and the paid up capital (face value 10 unless printed; 5, 1, 2, 2.5 or 100 also tried).
        # Catches a model copying a part profit (ATRL 2015: refinery operations Rs 405m, not total profit Rs 1,814m)
        # and a wrong EPS; a share issue in the year can also trip it, and then the year is left out (a gap, not an error).
        if "eps" in r and prof is not None and "paidup" in r and r["paidup"] > 0 and abs(r["eps"]) > 0.05:
            if eps_fits(r, prof, rows):
                good |= {"eps", "paidup", "face", "ni", "pat"}
            else:
                drop(("pat", "ni", "nci", "eps", "pbt", "tax", "paidup", "face"),
                     "EPS does not match profit and paid up capital")
        for k in ("rev", "ni", "pat", "eq", "assets", "cfo"):
            if k in r and abs(r[k]) > 5e6:
                drop((k,), "looks like a unit error")
        if ocr:
            drop([k for k in list(r) if k not in good], "OCR page and no identity confirms it")
    return dropped


# ---------------------------------------------------------------- read one report
def read_one(job, readers, sc, work):
    tier, _, _, jid, tk, y, src, url = job[:8]
    bundle, info = get_pages(jid, src, url, work)
    if bundle is None:
        return {"skip": info}
    cons = bool(info.get("cons"))
    ocr = bool(info.get("ocr") or info.get("ocr_pages"))
    pages = pick_pages(bundle, cons)
    if len(pages) < 800:
        return {"skip": "no statement pages found"}
    if info.get("cover"):
        pages = "===== COVER (first pages, for the company's name) =====\n" + info["cover"] + "\n" + pages
    low = pages.lower()
    interim = r"(half[\s-]+year|six\s+months|nine\s+months|three\s+months|quarter|period)\s+ended"
    if re.search(interim, low) and not re.search(r"year\s+ended", re.sub(interim, " ", low)):
        # interim accounts (3 Nov 2026): company websites file half year and quarterly statements beside the annual
        # ones, sometimes named only by month; annual statements always say "for the year ended"
        return {"skip": "not annual accounts (interim statements)"}
    nums = R.numbers(pages)
    rows = page_rows(pages)
    sb = set()
    for m in re.finditer(r"(?ms)^===== SUM page \d+ =====\n(.{0,700})", pages):
        h = m.group(1).lower()
        sb.add("consolidated" if re.search(r"(?<!un)(?<!un-)consolidated", h) else "standalone")
    sumb = sb.pop() if len(sb) == 1 else None
    rawp = os.path.join(RAW, jid.replace(":", "_") + ".json")
    rec = jl(rawp, {}) or {}
    import hashlib
    h = hashlib.md5((PROMPT + pages).encode()).hexdigest()[:12]
    if rec.get("h") != h:  # different pages or a newer prompt: earlier reads no longer count
        rec = {"h": h}
    reads = rec.setdefault("reads", {})
    audit = len(job) > 8
    before = {}
    if audit:  # an audit re-read: the earlier answers are set aside and two models that have not read it answer fresh
        before = rec.get("reads_before") or dict(reads)
        rec["reads_before"] = before
        reads = rec["reads"] = {k: v for k, v in reads.items() if k not in before}
    # models that gave no usable answer for these pages are not asked again (2 Nov 2026: one model kept failing on
    # the same report every run, spending paid credit while the others were never tried)
    failed = rec.setdefault("failed", {})
    spent = [0]
    prompt = PROMPT + pages
    fam = {r.name: r.family() for r in readers}

    def go(r):
        j = r.ask(prompt)
        if j is not None:
            reads[r.name] = {"at": date.today().isoformat(), "j": j}
        elif not r.out:  # it answered, but nothing usable: try another model instead
            failed[r.name] = date.today().isoformat()
            spent[0] += 1

    # a model that answers "skip" (cannot find the company or the pages) is not a read: keep asking other makers until
    # two real reads exist (2 Nov 2026: 658 reports were given up with one real read and one skip), unless two skip
    usable = lambda: [v for v in reads.values() if isinstance(v.get("j"), dict) and not v["j"].get("skip")]
    for _ in range(5):
        okr = usable()
        if len(reads) - len(okr) >= 2 and len(okr) < 2:
            break
        fs = [facts(v["j"], nums, cons, sumb, rows) for v in okr]
        ok, bad = agree(fs) if len(fs) >= 2 else ({}, set())
        # profit is the anchor of every history (8 Nov 2026): when only one model gave it, or two gave different
        # numbers, one more model is asked (up to four reads), instead of losing the year as before (192 reports);
        # when no model gave it, a third model is asked
        part = "C|" if cons else "S|"
        anchor = {k for x in fs for k in x if k.startswith(part) and k.rsplit("|", 1)[1] in ("pat", "ni")}
        open_anchor = bool(anchor - set(ok))
        want = 2 - len(okr) if len(okr) < 2 else (1 if (bad and len(okr) < 3) or (open_anchor and len(okr) < 4)
                                                      or (not anchor and len(okr) < 3) else 0)
        free = [r for r in readers if not r.out and r.name not in reads and r.name not in failed and r.name not in before]
        if want <= 0 or not free:
            break
        used = {fam.get(n) for n in reads} | {fam.get(n) for n in before}
        batch = []
        for _ in range(want):  # a different maker first
            fresh = [r for r in free if r not in batch and r.family() not in used | {b.family() for b in batch}]
            r = (fresh or [r for r in free if r not in batch] or [None])[0]
            if r:
                batch.append(r)
        ts = [threading.Thread(target=go, args=(r,)) for r in batch]
        [t.start() for t in ts]
        [t.join() for t in ts]
    rec.update({"id": jid, "tk": tk, "y": y, "url": url, "cons": cons, "ocr": ocr, "chars": len(pages)})
    jw(rec, rawp)
    allj = [v["j"] for v in reads.values() if isinstance(v.get("j"), dict)]
    if sum(1 for j in allj if j.get("skip")) >= 2 and len(usable()) < 2:
        return {"skip": "not annual accounts: " + str(next(j for j in allj if j.get("skip")).get("skip"))[:80]}
    if len(usable()) < 2:
        if audit and not [r for r in readers if r.name not in reads and r.name not in failed and r.name not in before]:
            # every model has read or failed this report: no fresh pair can ever settle it, so close the re-read
            # with a reason instead of retrying it every run (the year stays empty, as the audit decided)
            return {"skip": "audit: no fresh model left to settle the disagreement"}
        return {"wait": "fewer than two models gave a real read", "spent": spent[0]}
    firsts = [v["j"] for v in usable()]
    fs = [facts(j, nums, cons, sumb, rows) for j in firsts]
    ok, bad = agree(fs)
    company = next((j.get("company") for j in firsts if j.get("company")), "")
    # a renamed company (8 Nov 2026): the report prints its former name, which KHI Stocks' archive still lists for the
    # ticker, or the pages say "formerly" with the listed name; it is the same company, so the read counts
    alias = ALIASES.get(tk) or set()
    renamed = any(name_ok(company, a) for a in alias) or bool(
        re.search(r"formerly", pages[:6000], re.I) and name_ok2(company, sc[tk].get("n"), pages[:6000]))
    other = None if renamed else next((t for t, rr in sc.items() if t != tk and name_ok(company, rr.get("n"))
                                       and not name_ok(company, sc[tk].get("n"))), None)
    # no company name found at all (8 Nov 2026: "not printed on provided pages", a slogan, an auditor's name) in a report
    # from his own Drive library (named by its cover) or the company's own website: the source names the company. A
    # report that prints any company name (listed or not) still has to match, as before.
    noname = not re.search(r"\b(limited|ltd|corporation|corp|plc|bank|modaraba|fund|company|mills|industries|insurance)\b",
                           company or "", re.I)
    trusted = src in ("drive", "web") and noname and not other
    if not renamed and not trusted and ((src != "khi" and not name_ok2(company, sc[tk].get("n"), pages)) or (other and len(toks(company)) >= 2)):
        # a report filed under the wrong company (8 Nov 2026): when the printed name is clearly another listed
        # company's (nearly every distinctive word matches), its figures are kept for that company instead of thrown away
        strong = other and len(toks(company)) >= 2 and close_name(company, sc[other].get("n"))
        if not strong:
            return {"skip": f"company name does not match: {company[:60]}" + (f" (names {other})" if other else "")}
        tk, moved = other, other
    else:
        moved = None
    fye = next((j.get("fye") for j in firsts if re.fullmatch(r"(19|20)\d\d-\d\d", str(j.get("fye") or ""))), None)
    fy = int(fye[:4]) if fye else None
    if not fy or str(fy) not in pages:
        return {"skip": "year end not readable"}
    kind = next((j.get("kind") for j in firsts if j.get("kind") in ("nonfin", "bank", "insurer", "other_fin")), "nonfin")
    st, sums = {}, {}
    for k, v in ok.items():
        part, yy, fld = k.split("|")
        if part in ("C", "S"):
            if part != ("C" if cons else "S") or int(yy) not in (fy, fy - 1):
                continue  # only the basis we asked for, and only this year and the prior year
            st.setdefault(yy, {})[fld] = v
        else:
            sums.setdefault(part.split(":")[1], {}).setdefault(yy, {})[fld] = v
    dropped = checks(st, kind, ocr)
    for sb, rows in sums.items():
        dropped += checks(rows, kind, ocr)
        for yy, r in rows.items():  # a summary year must agree with the statements of the same year and basis
            s2 = st.get(yy) or {}
            if (sb == "consolidated") != cons:
                continue
            for fld in list(r):
                if fld in s2 and not close(r[fld], s2[fld], 0.01, 0.2 if fld not in PERSH else 0.02) and fld not in ("eq", "eps", "dps"):
                    r.pop(fld)
                    dropped.append(f"summary {yy} {fld}: differs from the statements")
    sie = [j.get("surplus_in_equity") for j in firsts if isinstance(j.get("surplus_in_equity"), bool)]
    sie = sie[0] if len(sie) >= 2 and sie[0] == sie[1] else None
    # 8 Nov 2026, group companies: consolidated statements with no non controlling (minority) interest anywhere in
    # them belong to a group whose subsidiaries are wholly owned, so the owners' profit is the whole profit
    # (IFRS prints the split between owners and minority on the face of the income statement, so that page must be in)
    nominority = bool(cons and "===== PLC page" in pages and not re.search(r"non\s*-?\s*controlling|minority", pages, re.I))
    for r in st.values():
        if nominority and "pat" in r and "ni" not in r and "nci" not in r:
            r["ni"] = r["pat"]
        if "surplus" in r and sie is not None:
            r["surplus_in_eq"] = sie
        elif "surplus" in r and "eq" in r:
            r.pop("eq")  # where the surplus sits is not agreed: equity left out
        if "paidup" in r and "face" in r and r["face"] > 0:
            r["shares"] = round(r["paidup"] / r["face"], 3)
        if not cons and "pat" in r and "ni" not in r:
            r["ni"] = r["pat"]
    if not st and not sums:
        return {"skip": "no figure passed the checks", "disputed": len(bad)}
    unit = "Rs m"
    out = {"file": jid, "ticker": tk, "company": company, "fye": fye, "unit": unit, "kind": kind, "cons": cons,
           ("C" if cons else "S"): st, "restated": "",
           "notes": f"read by free AI models ({', '.join(sorted(reads))[:200]}); figures kept only when two agree and the "
                    f"number is printed" + ("; OCR pages, only figures confirmed by arithmetic" if ocr else ""),
           "src": url, "by": "ai", "v": 3, "dropped": dropped[:40], "disputed": sorted(bad)[:40],
           # a full annual report (40 pages or more) with no consolidated statements: the company had no group accounts
           # that year, so its standalone figures are the whole business (8 Nov 2026)
           # and the statements must not call themselves unconsolidated, separate or standalone, nor mention a
           # subsidiary: IFRS makes a company with subsidiaries name its own accounts that way, so a standalone file of a
           # group (Lucky Cement publishes one) is never taken for the whole business
           "pages": info.get("pages"), "nogroup": bool(not cons and (info.get("pages") or 0) >= 40 and not re.search(
               r"un\s*-?\s*consolidated|consolidated|separate\s+financial|stand\s*-?\s*alone|subsidiar",
               re.sub(r"(?m)^FILE .*$", "", pages), re.I)),   # not the page picker's own header line
           "nominority": nominority}
    if moved:
        out["moved_from"] = job[4]
    if audit:
        # figures the fresh pair and the earlier pair agree on: four models, two separate reads. The tracker's audit
        # then keeps the report figure even where an outside source differs (definitions and basis often differ)
        old = [facts(v["j"], nums, cons, sumb, page_rows(pages)) for v in before.values()
               if isinstance(v.get("j"), dict) and not v["j"].get("skip")]
        ok_old, _ = agree(old) if len(old) >= 2 else ({}, set())
        part = "C" if cons else "S"
        out["confirmed"] = sorted(f"{k.split('|')[1]}|{k.split('|')[2]}" for k, v in ok.items() if k.startswith(part + "|")
                                  and k in ok_old and same(v, ok_old[k], k.rsplit("|", 1)[1]))
        out["notes"] += f"; audit re-read by {', '.join(sorted(reads))[:120]}, earlier read by {', '.join(sorted(before))[:120]}"
    for sb, rows in sums.items():
        if rows:
            out["SUM_C" if sb == "consolidated" else ("SUM" if "SUM" not in out else "SUM_X")] = {"basis": sb, "years": rows}
    return {"rec": out}


# ---------------------------------------------------------------- run
def recheck(ext):
    """apply checks added after a report was read (EPS against profit and paid up capital, 1 Nov 2026) to saved reads"""
    for k, v in ext.items():
        if v.get("by") != "ai" or v.get("rechecked") == 1:
            continue
        rows = v.get("C" if v.get("cons") else "S") or {}
        for y, r in rows.items():
            prof = r.get("ni", r.get("pat"))
            if "eps" in r and prof is not None and r.get("paidup", 0) > 0 and abs(r["eps"]) > 0.05:
                if not eps_fits(r, prof, rows):
                    for f in ("pat", "ni", "nci", "eps", "pbt", "tax", "paidup", "face", "shares"):
                        r.pop(f, None)
                    v.setdefault("dropped", []).append(f"{y}: EPS does not match profit and paid up capital (re-checked)")
        v["rechecked"] = 1


def estimate(sc, longrows, cat, docs, state, by_tk, imp):
    """how many reports the whole plan still needs, simulating that each report covers its two years and a
    summary as long as the company's earlier summaries (6 years if none yet)"""
    tot = 0
    for tk in set(cat) | set(docs) | set(WEB):  # website only companies too (3 Nov 2026)
        if tk not in sc:
            continue
        cands = candidates(tk, cat, docs)
        cov = covered(tk, longrows, by_tk.get(tk, []))
        span = 6
        for d in by_tk.get(tk, []):
            for k in ("SUM", "SUM_C"):
                n = len((d.get(k) or {}).get("years") or {})
                span = max(span, n) if n else span
        for y in sorted(cands, reverse=True):
            st = state.get(f"{cands[y][0]}:{tk}:{y}") or {}
            if st.get("done") or st.get("tries", 0) >= 2:
                continue
            if y not in cov or ((y - 1) not in cov and (y - 1) not in cands):
                tot += 1
                cov |= set(range(y - span + 1, y + 1))
    return tot


# ---- Spending guard (2 Nov 2026, his rule: paid credit and robot time must never again be spent for nothing) ----
# Every run notes the OpenRouter credit spent and the reports saved. Paid models are switched off for a day, and his
# phone is told, when in the last 24 hours (a) more than 10 cents went on runs that saved nothing, or (b) a saved
# report cost over 2 cents on average (normal is about a third of a cent). The free models carry on regardless.
GUARD = os.path.join(AN, "guard.json")
IDLE_MAX, PER_REPORT_MAX, PAUSE_H = 0.10, 0.02, 24


def credit_left():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    code, body = R.http("https://openrouter.ai/api/v1/credits", headers={"authorization": f"Bearer {key}"}, timeout=40)
    try:
        d = json.loads(body).get("data") or {}
        return float(d.get("total_credits") or 0) - float(d.get("total_usage") or 0) if code == 200 else None
    except Exception:
        return None


def phone(title, text):
    """an alert through his locked site (the public reader never holds the alert topic); never stops the run"""
    pw = os.environ.get("SITE_PASSWORD")
    if not pw:
        return
    import hashlib
    c = "psx_auth=" + hashlib.sha256(("psx-tracker:" + pw).encode()).hexdigest()
    subprocess.run(["curl", "-s", "-m", "60", "-H", "Cookie: " + c, "-H", "content-type: application/json",
                    "-d", json.dumps({"title": title, "text": text}), "https://psx-tracker.pages.dev/api/wake"],
                   capture_output=True, timeout=90)


def idle_alert(done, sc, longrows, by_tk):
    """never idle in silence (8 Nov 2026: every run "succeeded" for three days while reading nothing). When no annual
    report has been read for 24 hours his phone gets one message a day saying so, with how many traded companies
    still have short histories."""
    g = jl(GUARD, {}) or {}
    now = time.time()
    if done or "last_read" not in g:
        g["last_read"] = now
    elif now - g["last_read"] > 86400 and now - g.get("idle_alert", 0) > 86400:
        short = sum(1 for tk in sc if len({y for y in covered(tk, longrows, by_tk.get(tk, [])) if y >= 2005}) < 10)
        phone("Report reader idle", f"No annual report read for {round((now - g['last_read']) / 3600)} hours. "
                                    f"{short} of {len(sc)} traded companies still have under 10 years. Needs a look in Claude.")
        g["idle_alert"] = now
        print("idle alert sent to his phone", flush=True)
    jw(g, GUARD)


def guard_start():
    g = jl(GUARD, {}) or {}
    until = g.get("paid_off_until") or ""
    if until > time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()):
        os.environ.pop("OPENROUTER_PAID", None)
        print(f"guard: paid models paused until {until} UTC ({g.get('why')})", flush=True)
    return credit_left() if os.environ.get("OPENROUTER_PAID") else None


def guard_end(c0, saved):
    if c0 is None:
        return
    c1 = credit_left()
    if c1 is None:
        return
    g = jl(GUARD, {}) or {}
    now = time.time()
    log = [x for x in g.get("runs", []) if now - x["t"] < 48 * 3600]
    log.append({"t": round(now), "spent": round(max(0.0, c0 - c1), 4), "saved": saved})
    g["runs"] = log
    day = [x for x in log if now - x["t"] < 24 * 3600]
    spent, got = sum(x["spent"] for x in day), sum(x["saved"] for x in day)
    idle = sum(x["spent"] for x in day if not x["saved"])
    why = None
    if idle > IDLE_MAX:
        why = f"${idle:.2f} spent in the last day on runs that saved no report"
    elif spent > 0.30 and spent / max(got, 1) > PER_REPORT_MAX:
        why = f"${spent:.2f} spent for {got} reports in the last day, over 2 cents a report"
    print(f"guard: this run ${max(0.0, c0 - c1):.3f} for {saved} reports; last day ${spent:.2f} for {got}; credit ${c1:.2f}",
          flush=True)
    if why:
        g["paid_off_until"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now + PAUSE_H * 3600))
        g["why"] = why
        g["runs"] = []  # a fresh count after the pause
        print(f"guard: paid models paused for {PAUSE_H} hours: {why}", flush=True)
        phone("PSX reader: paid models paused", f"{why}. I paused the paid models for {PAUSE_H} hours to protect your "
              f"credit (${c1:.2f} left); the free ones keep reading. Tell Claude in the next chat.")
    jw(g, GUARD, indent=0)


def main():
    t0 = time.time()
    os.makedirs(RAW, exist_ok=True)
    nl, ns = refresh_flags()
    print(f"tracker flags: {nl} companies with histories, {ns} listed" if nl else "tracker flags: kept the saved copy", flush=True)
    sc = active_only(screener())
    longrows = load_rows("long")
    ext = jl(EXTRACTS, {}) or {}
    state0 = jl(os.path.join(AN, "state.json"), {}) or {}
    stale = [k for k, v in ext.items() if v.get("by") == "ai" and v.get("v") != 3]
    # older reads are checked again under the latest rules; the saved model answers are reused (same pages and prompt),
    # so this costs no model calls
    stale_names = [k for k, v in state0.items() if v.get("rev") != 5 and
                   re.search(r"company name does not match|no figure passed|company name not (found|printed)|"
                             r"cannot determine company name", v.get("why") or "")]
    for k in stale_names:
        state0.pop(k, None)
    for k in stale:
        ext.pop(k)
        state0.pop(k, None)
    if stale or stale_names:
        jw(state0, os.path.join(AN, "state.json"), indent=0)
        print(f"re-reading {len(stale)} reports read before the row proof rules", flush=True)
    recheck(ext)
    by_tk = {}
    for d in ext.values():
        if d.get("ticker") and not d.get("skip"):
            by_tk.setdefault(d["ticker"], []).append(d)
    state = jl(os.path.join(AN, "state.json"), {}) or {}
    nre = requeue_8nov(state, ext) + requeue_groups(state, ext, load_rows("long"))
    if nre:
        jw(state, os.path.join(AN, "state.json"), indent=0)
        print(f"requeue: {nre} reports read again under the 8 Nov 2026 fixes (joined letters, profit rules)", flush=True)
    RECHECK.update(load_recheck())
    nsec = second_chance(state)
    if nsec:
        jw(state, os.path.join(AN, "state.json"), indent=0)
        print(f"second chance: {nsec} reports given up earlier get one more try", flush=True)
    STATE.update(state)
    WEB.update({t: v.get("years") or {} for t, v in ((jl(os.path.join(AN, "web_catalogue.json"), {}) or {}).get("co") or {}).items()})
    ARC.update({t: v.get("years") or {} for t, v in ((jl(os.path.join(AN, "archive_catalogue.json"), {}) or {}).get("co") or {}).items()})
    GROUPS.update(t for t, r in longrows.items() if (r or {}).get("basis") == "group")
    print(f"sources: company websites give {sum(len(v) for v in WEB.values())} reports for {sum(1 for v in WEB.values() if v)} companies; "
          f"the Internet Archive {sum(len(v) for v in ARC.values())} for {sum(1 for v in ARC.values() if v)}", flush=True)
    cat = khi_catalogue(sc)
    # former names: KHI Stocks' archive name for the ticker, and every name printed in reports already accepted for it
    for t, r in cat.items():
        if r.get("name"):
            ALIASES.setdefault(t, set()).add(r["name"])
    for t, ds in by_tk.items():
        for d in ds:
            if d.get("company") and not d.get("moved_from"):
                ALIASES.setdefault(t, set()).add(d["company"])
    docs = psx_docs()
    print("audit: " + audit_summary(sc, cat, docs, state), flush=True)
    audited_now = [0]
    jobs, imp = plan(sc, longrows, cat, docs, state, by_tk)
    left0 = estimate(sc, longrows, cat, docs, state, by_tk, imp)
    if PLAN:
        tiers = [sum(1 for j in jobs if j[0] == t) for t in range(3)]
        print(f"plan: about {left0} reports in all; next round {len(jobs)} companies ({tiers[0]} held or reviewed, "
              f"{tiers[1]} group, {tiers[2]} others); KHI archive {len(cat)} companies, PSX filings {len(docs)}")
        for j in jobs[:15]:
            print("  ", j[3], j[7])
        return 0
    c0 = guard_start()
    if SHARDS > 1:
        jw({"c0": c0}, os.path.join(AN, f"credit_start_{SHARD}.json"))
        c0 = None  # the merge job measures the spend of all readers together
    readers = R.panel()
    if not readers:
        print("annual: no free AI key is set (GEMINI_API_KEY); nothing read")
        return 0
    jobsmap = jl(os.path.join(AN, "jobs.json"), {}) or {}
    done = skipped = waiting = 0
    import heapq
    from concurrent.futures import ThreadPoolExecutor, wait as fwait, FIRST_COMPLETED
    heap = list(jobs)
    heapq.heapify(heap)
    busy, started, slot = {}, 0, [0]
    stop = lambda: time.time() - t0 > MINUTES * 60 or all(r.out for r in readers) or started >= MAXN

    def work(job, k):
        w = tempfile.mkdtemp(prefix="ar_")
        try:
            # two separate teams (his rule, 1 Nov 2026): every third report goes to the free models alone, the rest to
            # the paid models alone, so free models never wait behind paid ones and paid credit is never spent on
            # checking a free read. With no paid models left (credit at the reserve), every report goes to the free team.
            paid = [r for r in readers if getattr(r, "paid", False)]
            free = [r for r in readers if not getattr(r, "paid", False)]
            rot = lambda L: L[k % len(L):] + L[:k % len(L)] if L else []
            if len(job) > 8:
                # an audit re-read needs two models that never read this report: offer every model, free ones first
                # (3 Nov 2026: re-reads always landed in slots 1 and 2, so only the 4 paid models were ever offered;
                # once those had all read a report, its re-read waited forever and was retried every run)
                order = rot(free) + paid
            elif paid and (k % 3 or not any(not r.out for r in free)):
                order = paid[:2] + rot(paid[2:])
            else:
                order = rot(free)
            return read_one(job, order, sc, w)
        except Exception as e:  # one bad report never stops the run
            return {"skip": f"error: {type(e).__name__}: {str(e)[:80]}"}
        finally:
            subprocess.run(["rm", "-rf", w])

    pool = ThreadPoolExecutor(WORKERS)
    if True:  # no "with": leaving it would wait for reports still hanging after the hard stop
        while heap or busy:
            while heap and len(busy) < WORKERS and not stop():
                job = heapq.heappop(heap)
                if any(j[4] == job[4] for j in busy.values()):
                    continue
                slot[0] += 1
                busy[pool.submit(work, job, slot[0])] = job
                started += 1
            if not busy:
                break
            if time.time() - t0 > MINUTES * 60 + 600:
                print(f"hard stop: {len(busy)} slow reports left for the next run", flush=True)
                break
            fin, _ = fwait(list(busy), timeout=60, return_when=FIRST_COMPLETED)
            for f in fin:
                job = busy.pop(f)
                res = f.result()
                jid, tk = job[3], job[4]
                st = state.setdefault(jid, {})
                st["at"] = date.today().isoformat()
                if len(job) > 8 and job[8] == "retry" and "wait" not in res:
                    st.pop("retry", None)  # the second chance is used up, whatever came back
                if len(job) > 8 and job[8] == "audit" and "wait" not in res:  # an audit re-read counts once, whatever came back
                    st["reread"] = sorted(set(st.get("reread") or []) | audit_years(tk, job[5], candidates(tk, cat, docs)))
                    audited_now[0] += 1
                if "rec" in res:
                    tk = res["rec"].get("ticker") or tk  # a report filed under the wrong company counts for its own
                    ext[jid] = res["rec"]
                    by_tk.setdefault(tk, []).append(res["rec"])
                    jobsmap[jid] = [tk, job[5], job[7]]
                    st.update(done=True, n=sum(len(v) for v in (res["rec"].get("C") or res["rec"].get("S") or {}).values()))
                    st.pop("why", None)
                    done += 1
                elif "wait" in res and not (res.get("spent") and st.get("waits", 0) >= 2):
                    # the models were busy: this company waits for the next run. A wait where models answered but
                    # nothing usable came back counts, and the third such wait counts as one failed try, so a report
                    # no model can read is dropped after a few runs instead of spending credit forever.
                    if res.get("spent"):
                        st["waits"] = st.get("waits", 0) + 1
                    waiting += 1
                    jw(state, os.path.join(AN, "state.json"), indent=0)
                    continue
                else:
                    if "wait" in res:
                        res = {"skip": "no two models gave a usable read"}
                        st["waits"] = 0
                    st["tries"] = st.get("tries", 0) + 1
                    st["rev"] = 5
                    st["why"] = res["skip"]
                    skipped += 1
                nxt = next_job(tk, sc, longrows, cat, docs, state, by_tk.get(tk, []), imp)
                if nxt:
                    heapq.heappush(heap, nxt)
            jw(ext, EXTRACTS)
            jw(state, os.path.join(AN, "state.json"), indent=0)
            jw(jobsmap, os.path.join(AN, "jobs.json"), indent=0)
    json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "minutes": round((time.time() - t0) / 60, 1),
               "panel": [r.name for r in readers], "calls": R.CALLS[-400:]},
              open(os.path.join(AN, "calls.json"), "w"), indent=0)
    spent = [r for r in readers if r.out]
    left = estimate(sc, longrows, cat, docs, state, by_tk, imp) + audit_counts(sc, cat, docs, state)["to re-read"]
    print(f"done: read {done} annual reports ({audited_now[0]} audit re-reads), {skipped} skipped, about {left} still to read; "
          f"models used: " + ", ".join(f"{r.model} {r.n}" for r in readers if r.n)
          + (f"; resting until tomorrow: {len(spent)} models" if spent else ""))
    try:
        guard_end(c0, done)
    except Exception as e:  # the guard never stops the reader
        print(f"guard: skipped ({e})", flush=True)
    if SHARDS > 1:
        jw({"done": done}, os.path.join(AN, f"shard_done_{SHARD}.json"))
    try:
        if SHARDS <= 1:
            idle_alert(done, sc, longrows, by_tk)
    except Exception as e:  # the alert never stops the reader
        print(f"idle alert: skipped ({e})", flush=True)
    sys.stdout.flush()
    pool.shutdown(wait=False, cancel_futures=True)
    os._exit(0)  # leave any report still hanging after the hard stop; everything finished is already saved


if __name__ == "__main__":
    sys.exit(main())
