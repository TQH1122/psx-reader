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
WORKERS = int(opt("--workers", 6))  # reports read at the same time
KHI = "https://www.khistocks.com"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Mobile Safari/537.36"

import reader as R  # the free model panel and the printed number checks of batch 5

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
 debt a list of the interest bearing borrowing lines on the balance sheet face, each as {"label":"as printed","value":n}
 (long term financing, its current portion, short term borrowings, running finance, lease liabilities and similar);
 cash cash and bank balances; sti short term investments; cfo net cash from operating activities; capex purchase of
 property, plant and equipment (fixed capital expenditure); divpaid dividends paid; paidup issued, subscribed and paid up
 share capital; face face (par) value per share;
 banks: dep deposits, adv advances net, inv investments, nii net mark up or interest income, nonint total non mark up income,
 prov provisions and write offs; insurers: gpw gross premium written, npr net premium revenue or net insurance revenue,
 uw underwriting result, invinc investment income; px (summary only) market price per share at year end.
Rules:
- Copy every number exactly as printed, digits and decimals, in the printed unit. Never add, convert, round or guess.
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
    if cat.get("asOf") and (date.today() - date.fromisoformat(cat["asOf"])).days < 30:
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
            continue
        ys = sorted({int(y) for y in re.findall(r"\d{4}", str(byid[i].get("years") or ""))})
        out[t] = {"id": i, "name": byid[i].get("company"), "years": ys}
    for t, i in known.items():  # the ids Claude confirmed by hand on 22 to 27 Oct 2026
        if t not in out and i in byid:
            out[t] = {"id": i, "name": byid[i].get("company"),
                      "years": sorted({int(y) for y in re.findall(r"\d{4}", str(byid[i].get("years") or ""))})}
    jw({"asOf": date.today().isoformat(), "n": len(out), "co": out}, p, indent=0)
    return out


def psx_docs():
    """annual reports in his Drive library that are PSX filings (file named by the PSX document number)"""
    out = {}
    for r in jl(os.path.join(FILES, "library_index.json"), []) or []:
        m = re.fullmatch(r"(\d{5,7})\.pdf", r.get("title") or "")
        if m and r.get("kind") == "A" and r.get("tk") and r.get("y"):
            out.setdefault(r["tk"], {}).setdefault(int(r["y"]), (r["id"], f"https://dps.psx.com.pk/download/document/{m.group(1)}.pdf"))
    return out


# ---------------------------------------------------------------- planning
def covered(tk, longrows, extracts):
    """years that already hold a checked profit figure on the company's basis: the long history (profit and equity),
    report statements, and multi year summary years on the same basis (one summary page gives 6 to 10 years)"""
    r = longrows.get(tk) or {}
    group = r.get("basis") == "group"
    fy, ni, eq = r.get("fy") or [], r.get("ni") or [], r.get("eq") or []
    cov = {y for i, y in enumerate(fy) if i < len(ni) and ni[i] is not None and i < len(eq) and eq[i] is not None}
    for d in extracts:
        for b in ("C", "S"):
            if (b == "C") == bool(d.get("cons")):
                cov |= {int(y) for y in (d.get(b) or {}) if str(y).isdigit()}
        for k in ("SUM", "SUM_C", "SUM_X"):
            sm = d.get(k) or {}
            ok = sm.get("basis") == "consolidated" if group else sm.get("basis") in ("standalone", "unclear")
            if ok:
                cov |= {int(y) for y, row in (sm.get("years") or {}).items()
                        if str(y).isdigit() and isinstance(row, dict) and (row.get("ni") is not None or row.get("pat") is not None)}
    return cov


def candidates(tk, cat, docs):
    cands = {}
    for y in (cat.get(tk) or {}).get("years", []):
        cands[y] = ("khi", f"{KHI}/assets/annual_reports/{cat[tk]['id']}/{y}")
    for y, (fid, url) in (docs.get(tk) or {}).items():
        cands.setdefault(y, ("psx", url))
    return cands


def next_job(tk, sc, longrows, cat, docs, state, extracts, imp):
    """the one report of this company most worth reading next, or None. Newest missing year first: its statements give
    two years and its summary page often 6 to 10 more, so the next pick jumps past everything it covered."""
    cands = candidates(tk, cat, docs)
    if not cands or tk not in sc:
        return None
    cov = covered(tk, longrows, extracts)
    group = (longrows.get(tk) or {}).get("basis") == "group"
    tier = 0 if tk in imp else (1 if group else 2)
    ys = sorted(cands, reverse=True)
    for y in ys:
        st = state.get(f"{cands[y][0]}:{tk}:{y}") or {}
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
    jobs = [j for tk in sorted(set(cat) | set(docs))
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
        code, _ = curl(url + ".pdf", pdf, timeout=300)
        ok = code == 200 and os.path.exists(pdf) and open(pdf, "rb").read(5) == b"%PDF-"
        if not ok:
            code, _ = curl(url + ".html", os.path.join(work, "r.html"), timeout=120)
            if code != 200 or os.path.getsize(os.path.join(work, "r.html")) < 5000:
                return None, f"not on KHI Stocks ({code})"
            pdf = os.path.join(work, "r.txt")
            html_to_pages(os.path.join(work, "r.html"), pdf)
    else:
        code, _ = curl(url, pdf, timeout=300)
        if code != 200 or open(pdf, "rb").read(5) != b"%PDF-":
            return None, f"PSX download failed ({code})"
    if pdf.endswith(".pdf") and os.path.getsize(pdf) > 80e6:
        return None, "file too large"
    if pdf.endswith(".pdf"):
        # a scanned report (no text layer) needs every page OCRed, which costs 10 or more GitHub minutes: left for later
        t = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, errors="ignore").stdout.split("\f")
        textp = sum(1 for x in t if len(x.strip()) > 200)
        if len(t) > 40 and textp < len(t) * 0.3:
            return None, "scanned report (left for later, OCR too slow)"
    out = os.path.join(work, "bundle.txt")
    try:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "toolkit", "lib", "prep.py"), pdf, out],
                           capture_output=True, text=True, timeout=600, cwd=work)
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


def convert(block, nums, per_unit=True):
    """{year: fields as printed} -> {year: {field: value in Rs m}}, only numbers printed on the pages"""
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
                vals = [R.fnum(x.get("value")) for x in v if isinstance(x, dict)]
                if vals and all(x is not None and R.printed(x, nums) for x in vals):
                    r["debt"] = round(sum(abs(x) for x in vals) * f, 3)
                continue
            x = R.fnum(v)
            if x is None or not R.printed(x, nums):
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


def facts(j, nums, cons, sumbasis):
    """the code decides the basis: the statements sent are all on one basis; the summary's basis comes from its page
    heading when every summary page says the same, otherwise from the model (and must then agree like any figure)"""
    if not isinstance(j, dict) or j.get("skip"):
        return {}
    f = {}
    st = j.get("st") or {}
    b = "C" if cons else "S"
    for y, r in convert(st, nums).items():
        for k, v in r.items():
            f[f"{b}|{y}|{k}"] = v
    sm = j.get("sum") or {}
    sb = str(sm.get("basis") or "").lower()
    sb = sumbasis or ("consolidated" if sb.startswith("consol") else "standalone" if sb.startswith("company") else "unclear")
    for y, r in convert(sm, nums).items():
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
        if "paidup" in r and "face" in r and r["face"] > 0:
            sh = r["paidup"] / r["face"]
            if "eps" in r and prof is not None and abs(r["eps"]) > 0.05 and abs(prof / sh - r["eps"]) > max(0.06, abs(r["eps"]) * 0.06):
                drop(("paidup", "face"), "profit per share on year end shares differs from EPS (share issue in the year)")
            elif "eps" in r:
                good |= {"eps", "paidup", "face"}
        for k in ("rev", "ni", "pat", "eq", "assets", "cfo"):
            if k in r and abs(r[k]) > 5e6:
                drop((k,), "looks like a unit error")
        if ocr:
            drop([k for k in list(r) if k not in good], "OCR page and no identity confirms it")
    return dropped


# ---------------------------------------------------------------- read one report
def read_one(job, readers, sc, work):
    tier, _, _, jid, tk, y, src, url = job
    bundle, info = get_pages(jid, src, url, work)
    if bundle is None:
        return {"skip": info}
    cons = bool(info.get("cons"))
    ocr = bool(info.get("ocr") or info.get("ocr_pages"))
    pages = pick_pages(bundle, cons)
    if len(pages) < 800:
        return {"skip": "no statement pages found"}
    nums = R.numbers(pages)
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
    prompt = PROMPT + pages
    fam = {r.name: r.family() for r in readers}

    def go(r):
        j = r.ask(prompt)
        if j is not None:
            reads[r.name] = {"at": date.today().isoformat(), "j": j}

    for _ in range(3):
        fs = [facts(v["j"], nums, cons, sumb) for v in reads.values()]
        ok, bad = agree(fs) if len(fs) >= 2 else ({}, set())
        want = 2 - len(reads) if len(reads) < 2 else (1 if bad and len(reads) < 3 else 0)
        free = [r for r in readers if not r.out and r.name not in reads]
        if want <= 0 or not free:
            break
        used = {fam.get(n) for n in reads}
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
    if len(reads) < 2:
        return {"wait": "fewer than two models answered"}
    firsts = [v["j"] for v in reads.values() if isinstance(v["j"], dict)]
    if sum(1 for j in firsts if j.get("skip")) >= 2:
        return {"skip": "not annual accounts: " + str(firsts[0].get("skip"))[:80]}
    fs = [facts(j, nums, cons, sumb) for j in firsts]
    ok, bad = agree(fs)
    company = next((j.get("company") for j in firsts if j.get("company")), "")
    if not name_ok(company, sc[tk].get("n")):
        return {"skip": f"company name does not match: {company[:60]}"}
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
    for r in st.values():
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
           "src": url, "by": "ai", "dropped": dropped[:40], "disputed": sorted(bad)[:40]}
    for sb, rows in sums.items():
        if rows:
            out["SUM_C" if sb == "consolidated" else ("SUM" if "SUM" not in out else "SUM_X")] = {"basis": sb, "years": rows}
    return {"rec": out}


# ---------------------------------------------------------------- run
def estimate(sc, longrows, cat, docs, state, by_tk, imp):
    """how many reports the whole plan still needs, simulating that each report covers its two years and a
    summary as long as the company's earlier summaries (6 years if none yet)"""
    tot = 0
    for tk in set(cat) | set(docs):
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


def main():
    t0 = time.time()
    os.makedirs(RAW, exist_ok=True)
    sc = screener()
    longrows = load_rows("long")
    ext = jl(EXTRACTS, {}) or {}
    by_tk = {}
    for d in ext.values():
        if d.get("ticker") and not d.get("skip"):
            by_tk.setdefault(d["ticker"], []).append(d)
    state = jl(os.path.join(AN, "state.json"), {}) or {}
    cat = khi_catalogue(sc)
    docs = psx_docs()
    jobs, imp = plan(sc, longrows, cat, docs, state, by_tk)
    left0 = estimate(sc, longrows, cat, docs, state, by_tk, imp)
    if PLAN:
        tiers = [sum(1 for j in jobs if j[0] == t) for t in range(3)]
        print(f"plan: about {left0} reports in all; next round {len(jobs)} companies ({tiers[0]} held or reviewed, "
              f"{tiers[1]} group, {tiers[2]} others); KHI archive {len(cat)} companies, PSX filings {len(docs)}")
        for j in jobs[:15]:
            print("  ", j[3], j[7])
        return 0
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
            # spread the reports over the models: each worker starts its model search at a different place
            n = len(readers)
            order = readers[k % n:] + readers[:k % n]
            return read_one(job, order, sc, w)
        except Exception as e:  # one bad report never stops the run
            return {"skip": f"error: {type(e).__name__}: {str(e)[:80]}"}
        finally:
            subprocess.run(["rm", "-rf", w])

    with ThreadPoolExecutor(WORKERS) as pool:
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
            fin, _ = fwait(list(busy), return_when=FIRST_COMPLETED)
            for f in fin:
                job = busy.pop(f)
                res = f.result()
                jid, tk = job[3], job[4]
                st = state.setdefault(jid, {})
                st["at"] = date.today().isoformat()
                if "rec" in res:
                    ext[jid] = res["rec"]
                    by_tk.setdefault(tk, []).append(res["rec"])
                    jobsmap[jid] = [tk, job[5], job[7]]
                    st.update(done=True, n=sum(len(v) for v in (res["rec"].get("C") or res["rec"].get("S") or {}).values()))
                    st.pop("why", None)
                    done += 1
                elif "wait" in res:
                    waiting += 1
                    continue  # the models were busy: this company waits for the next run
                else:
                    st["tries"] = 2 if "scanned" in res["skip"] else st.get("tries", 0) + 1
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
    left = estimate(sc, longrows, cat, docs, state, by_tk, imp)
    print(f"done: read {done} annual reports, {skipped} skipped, about {left} still to read; "
          f"models used: " + ", ".join(f"{r.model} {r.n}" for r in readers if r.n)
          + (f"; resting until tomorrow: {len(spent)} models" if spent else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
