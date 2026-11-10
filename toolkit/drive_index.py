#!/usr/bin/env python3
"""Drive library indexer (8 Nov 2026): lists his PSX Library Drive folder by itself (the folder is link shared, so
Google's embedded folder view lists every file without any key), downloads each file it has not seen, reads the
first pages (OCR when scanned) and works out company, kind (annual or quarter) and year. The report reader then
reads every annual report found here. Before this, the reader only knew the 1,224 annual reports indexed by hand
on 27 Oct 2026, so every report he uploaded afterwards was never used.

Usage: drive_index.py MINUTES. Writes data/files/drive_ident.json (file id -> what it is). Prints one "done:" line.
"""
import json, os, re, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from textfix import fix  # joined letters in PDF fonts (8 Nov 2026)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "data", "files")
OUT = os.path.join(FILES, "drive_ident.json")
FOLDER = "1KZBgru0osNuRHUyypDF1V8Pxhf7Db02F"   # PSX Library
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 40
# several indexers side by side (8 Nov 2026): indexer i of n looks only at its own share of files; the workflow's merge
# job joins them (toolkit/shard_merge_drive.py)
SHARD, SHARDS = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 1)


def mine(fid):
    import zlib
    return SHARDS <= 1 or zlib.crc32(fid.encode()) % SHARDS == SHARD


def jl(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def api_listing():
    """every file in the folder through the Drive API and his free Google key (8 Nov 2026: the folder view below stops
    at about 5,500 files, so his newest uploads were never seen). Needs the Drive API turned on for that key's
    project; until then it returns None and the folder view is used."""
    import urllib.parse, urllib.request
    # DRIVE_API_KEY: his own key for the Drive API (8 Nov 2026: the AI Studio key is locked to Gemini, so Google
    # blocks it for Drive); the Gemini key is tried only when no Drive key is set
    key = os.environ.get("DRIVE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    out, tok = {}, ""
    q = urllib.parse.quote(f"'{FOLDER}' in parents and trashed = false")
    try:
        for _ in range(60):
            u = (f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize=1000&key={key}"
                 f"&fields=nextPageToken,files(id,name,mimeType)" + (f"&pageToken={tok}" if tok else ""))
            d = json.load(urllib.request.urlopen(u, timeout=90))
            for f in d.get("files", []):
                if f.get("mimeType") != "application/vnd.google-apps.folder":
                    out[f["id"]] = f.get("name") or ""
            tok = d.get("nextPageToken")
            if not tok:
                return out
    except Exception as e:
        print(f"drive api: not available ({str(e)[:80]}); using the folder view", flush=True)
        return None
    return out


def listing():
    """every file in the folder: id -> title (subfolders left out)"""
    full = api_listing()
    if full:
        print(f"drive api: {len(full)} files listed", flush=True)
        return full
    p = subprocess.run(["curl", "-sL", "-m", "180", f"https://drive.google.com/embeddedfolderview?id={FOLDER}"],
                       capture_output=True, text=True, errors="ignore")
    html, out = p.stdout, {}
    for m in re.finditer(r'id="entry-([A-Za-z0-9_-]{20,})".*?flip-entry-title">([^<]*)<', html, re.S):
        fid, title = m.group(1), m.group(2).strip()
        if f"/drive/folders/{fid}" in html:
            continue
        out[fid] = title
    if not out:  # page layout changed: fall back to bare file links
        out = {fid: "" for fid in set(re.findall(r"/file/d/([A-Za-z0-9_-]{20,})", html))}
    return out


# ---------------------------------------------------------------- identifying a file (same rules as ident.py, 27 Oct 2026)
def norm(s):
    s = s.lower().replace("&", " and ").replace(".", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


NAMES = []
for r in (jl(os.path.join(ROOT, "data", "db", "market", "screener.json"), {}) or {}).get("rows", []):
    core = re.sub(r"\b(limited|ltd|company|co|the|plc)\b", " ", norm(r.get("n") or ""))
    core = re.sub(r"\s+", " ", core).strip()
    if len(core) >= 4 and r.get("s"):
        NAMES.append((str(r["s"]).split()[0], core))
# companies no longer listed (10 Nov 2026): KHI Stocks' list keeps their names, so their reports get a ticker too
_have = {t for t, _ in NAMES}
for t, r in ((jl(os.path.join(ROOT, "data", "files", "annual", "khi_catalogue.json"), {}) or {}).get("co") or {}).items():
    core = re.sub(r"\s+", " ", re.sub(r"\b(limited|ltd|company|co|the|plc)\b", " ", norm((r or {}).get("name") or ""))).strip()
    if t not in _have and len(core) >= 4 and not re.search(r"\bfund\b|\bmodaraba\b certificate", core):
        NAMES.append((t, core))
NAMES.sort(key=lambda x: -len(x[1]))


def squash(s):
    """letters printed apart join up ("b r r guardian" = "brr guardian", "k s b" = "ksb"), so both sides compare alike"""
    return re.sub(r"\b([a-z0-9]) (?=[a-z0-9]\b)", r"\1", s)


SQ = [(t, squash(c)) for t, c in NAMES]

MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
END = r"(?:ended|ending|to)\s+(?:on\s+)?(?:the\s+)?(\d{1,2})?(?:st|nd|rd|th)?\s*(%s)[a-z]*,?\s*(\d{1,2},?\s*)?(20\d\d|19\d\d)" % MONS


def period(text):
    t = re.sub(r"\s+", " ", text.lower())
    for p, k in [(r"(nine months|9 months|third quarter|3rd quarter)[^.]{0,60}?" + END, "Q3"),
                 (r"(half year|half-year|six months|6 months|second quarter|2nd quarter|half yearly)[^.]{0,60}?" + END, "Q2"),
                 (r"(first quarter|1st quarter|three months|3 months|quarter)[^.]{0,60}?" + END, "Q1"),
                 (r"(year ended|year ending|financial year)[^.]{0,30}?(\d{1,2})?(?:st|nd|rd|th)?\s*(%s)[a-z]*,?\s*(\d{1,2},?\s*)?(20\d\d|19\d\d)" % MONS, "A")]:
        m = re.search(p, t)
        if m:
            g = m.groups()
            mon = next((i + 1 for i, x in enumerate(MON) if g[-3].startswith(x)), None)
            return k, int(g[-1]), mon
    m = (re.search(r"annual report\s*(?:for\s*(?:the\s*)?(?:year\s*)?)?(20\d\d|19\d\d)(?:\s*[-/]\s*(\d{2,4}))?", t)
         or re.search(r"(20\d\d|19\d\d)(?:\s*[-/]\s*(\d{2,4}))?\s*annual report", t))
    if m:
        y = int(m.group(1))
        if m.group(2):
            y = int(m.group(2)) if len(m.group(2)) == 4 else 2000 + int(m.group(2))
        return "A", y, None
    if re.search(r"quarterly report|interim report|condensed interim", t):
        return "Q", None, None
    if re.search(r"corporate briefing|analyst briefing", t):
        return "CB", None, None
    return ("A", None, None) if "annual report" in t else (None, None, None)


# research material he adds beside the reports (8 Nov 2026: State Bank, PACRA, VIS, APCMA, PAMA, APTMA and more):
# filed by publisher and sector so the PSX brain can use it; nothing is read by a model here
SOURCES = [("SBP", r"state bank of pakistan|\bsbp\b"), ("PACRA", r"\bpacra\b|pakistan credit rating"),
           ("VIS", r"\bvis credit|\bvis\b.{0,20}rating|\bvis research"), ("APCMA", r"\bapcma\b|all pakistan cement"),
           ("PAMA", r"\bpama\b|pakistan automotive manufacturers"), ("APTMA", r"\baptma\b|all pakistan textile"),
           ("PBS", r"pakistan bureau of statistics|\bpbs\b"), ("OCAC", r"\bocac\b|oil companies advisory"),
           ("NEPRA", r"\bnepra\b"), ("OGRA", r"\bogra\b"), ("MoF", r"ministry of finance|economic survey"),
           ("IMF", r"international monetary fund|\bimf\b"), ("WB", r"world bank"), ("ADB", r"asian development bank"),
           ("JICA", r"\bjica\b"), ("PSX", r"pakistan stock exchange"), ("Broker", r"securities limited|research desk|target price")]
SECTORS = [("cement", r"cement|clinker"), ("auto", r"automo|vehicle|car sales|motorcycle|tractor"),
           ("textile", r"textile|cotton|yarn|garment|weaving|spinning|knitwear|towel|bed ?wear"),
           ("pharma", r"pharma"), ("fertilizer", r"fertili[sz]er|urea|\bdap\b"), ("steel", r"steel|\biron\b"),
           ("oil", r"\boil\b|petroleum|\bomc\b|refiner|\blng\b|\bgas\b"), ("power", r"power|electricity|\bipp"),
           ("banks", r"\bbank|deposit|advances|monetary|policy rate|kibor"), ("insurance", r"insurance|takaful"),
           ("sugar", r"sugar"), ("food", r"\bfood|dairy|edible"), ("chemicals", r"chemical"), ("tech", r"software|\bit exports"),
           ("macro", r"inflation|\bcpi\b|current account|remittance|reserves|exchange rate|\bgdp\b|balance of payments|trade deficit")]


def research(text, title):
    t = (title + " " + text[:6000]).lower()
    src = next((k for k, p in SOURCES if re.search(p, t)), None)
    sec = sorted({k for k, p in SECTORS if re.search(p, t)})
    yrs = [int(y) for y in re.findall(r"\b(20[0-3]\d)\b", t)]
    return {"src": src, "sectors": sec, "y": max(yrs) if yrs else None}


def company(text, wide=False):
    """the listed company whose name is printed first. 10 Nov 2026: letters printed apart (B.R.R Guardian) matched no
    name, and names printed after the first 4,000 letters (after a long contents page) were missed; the wide look
    reads the whole of the first four pages but only when the narrow one found nothing"""
    low = squash(norm(text[:16000] if wide else text[:4000])).split(" bankers ")[0]
    best = None
    for tk, core in SQ:
        m = re.search(r"\b" + re.escape(core) + r"\b", low)
        if m and (best is None or m.start() < best[0] or (m.start() == best[0] and len(core) > best[1])):
            best = (m.start(), len(core), tk)
    return best[2] if best else None


def name_to_ticker(name):
    """a printed company name (from the cover, or a model reading it) to a ticker: the same name list, exact first,
    then a very close spelling (one or two letters off)"""
    import difflib
    core = squash(re.sub(r"\s+", " ", re.sub(r"\b(limited|ltd|company|co|the|plc|pakistan)\b", " ", norm(name or ""))).strip())
    if len(core) < 4:
        return None
    strip = lambda c: re.sub(r"\s+", " ", re.sub(r"\bpakistan\b", " ", c)).strip()
    for tk, c in SQ:
        if strip(c) == core:
            return tk
    best = max(((difflib.SequenceMatcher(None, strip(c), core).ratio(), tk) for tk, c in SQ), default=(0, None))
    return best[1] if best[0] >= 0.93 else None


PANEL = []
PANEL_LOCK = threading.Lock()
ASK = """Below are the first pages of a PDF filed by a company listed on the Pakistan Stock Exchange.
Answer only with JSON: {"company": "the company's full name exactly as printed", "report": "annual" or "half year" or
"quarter" or "nine months" or "other", "period_end": "YYYY-MM-DD" or null}.
"report" is "annual" only for annual accounts or an annual report; "quarter" for three month accounts; "other" for
anything else (letters, notices, briefings, research). "period_end" is the date the accounts are made up to, as printed.
If something is not printed, use null. Do not guess.
FILE NAME: {title}
PAGES:
{text}"""


def ask_model(txt, title):
    """free models only (Gemini, Groq, Cloudflare and OpenRouter's free list) read the cover when the rules could not.
    10 Nov 2026: 1,874 of his files had no company and about 1,200 no year. Nothing is taken on trust: the company
    must be printed in the pages and match the listed names, and the year must be printed too."""
    with PANEL_LOCK:
        if not PANEL:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import reader as R
            PANEL.extend(R.panel() or [None])
    for rd in [r for r in PANEL if r is not None and not r.out]:
        j = rd.ask(ASK.replace("{title}", title or "").replace("{text}", txt[:5000]))
        if isinstance(j, dict) and (j.get("company") or j.get("period_end")):
            return j, rd.name
    return None, None


def by_model(r, txt, title):
    """fills company, kind and year from a model's reading of the cover, each only when proven by the printed text"""
    j, who = ask_model(txt, title)
    if not j:
        return False
    sq = squash(norm(txt))
    got = False
    co = str(j.get("company") or "")
    if not r.get("tk") and co and squash(norm(co))[:25] in sq:
        tk = name_to_ticker(co)
        if tk:
            r["tk"], got = tk, True
            r.pop("co", None)
        else:
            r["co"] = co[:80]
    m = re.match(r"(19|20)(\d\d)-(\d\d)-(\d\d)$", str(j.get("period_end") or ""))
    rep = str(j.get("report") or "").lower()
    kind = {"annual": "A", "half year": "Q2", "quarter": "Q1", "nine months": "Q3"}.get(rep)
    if m and kind and m.group(1) + m.group(2) in txt:
        y, mon = int(m.group(1) + m.group(2)), int(m.group(3))
        rk = r.get("kind")
        # the kind the rules read stays (Q1 and Q3 cannot be told apart by "three months" alone); a clash is left alone
        nk = kind if rk in (None, "Q", "other", "R") else (rk if (rk == "A") == (kind == "A") else None)
        if not r.get("y") and nk:
            r["kind"] = nk
            r["y"], r["mon"], got = y, mon, True
    elif rep == "other" and not r.get("kind"):
        r["kind"] = "other"
        got = True
    if got:
        r["ai"] = who
    return got


CONAME = re.compile(r"\b((?:[A-Z][A-Za-z&().,'-]*[ \t]+(?:(?:of|and|the|&)[ \t]+)?){1,8}(?:Limited|Ltd\.?|LIMITED|LTD\.?))")
NOT_CO = re.compile(r"chartered accountants|registrar|\(private\) limited|\(pvt\.?\) ltd|associates|consultants", re.I)


def coname(text):
    """the company's own name as printed on the first pages, for reports of companies no longer listed (8 Nov 2026):
    they match no listed name, so without this they could never be used"""
    for m in CONAME.finditer(text[:6000]):
        n = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
        if len(n) <= 70 and not NOT_CO.search(n):
            return n
    return None


API_N = [0, 0]  # downloads through the Drive API: got the file, refused


def ident(fid, title):
    w = tempfile.mkdtemp()
    pdf = os.path.join(w, "f.pdf")
    try:
        # the Drive API with his key first (9 Nov 2026): the public link was answering "Quota exceeded" for thousands
        # of his files, so they were never identified; the public link stays as the fallback
        key = os.environ.get("DRIVE_API_KEY", "")
        p = None
        import gtoken  # as the owner first (9 Nov 2026): Google refuses his shared files to everyone else
        if gtoken.fetch_pdf(fid, pdf, 400):
            API_N[0] += 1
            p = subprocess.CompletedProcess([], 0, "200", "")
        elif key:
            p = subprocess.run(["curl", "-sL", "-m", "400", "--max-filesize", "90000000", "-o", pdf, "-w", "%{http_code}",
                                f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&acknowledgeAbuse=true&key={key}"],
                               capture_output=True, text=True)
            if not (p.stdout == "200" and os.path.exists(pdf) and open(pdf, "rb").read(5) == b"%PDF-"):
                API_N[1] += 1
                p = None
            else:
                API_N[0] += 1
        if p is None:
            p = subprocess.run(["curl", "-sL", "-m", "400", "--max-filesize", "90000000", "-o", pdf, "-w", "%{http_code}",
                                DRIVE.format(fid)], capture_output=True, text=True)
        if p.stdout != "200" or not os.path.exists(pdf):
            return {"title": title, "err": f"download {p.stdout or p.returncode}"}
        size = os.path.getsize(pdf)
        head = open(pdf, "rb").read(4096)
        if head[:5] != b"%PDF-" and size < 50_000 and re.search(rb"(?i)<html|<!doctype", head):
            # Google answered with a page instead of the file: it is busy or limiting downloads (9 Nov 2026: 3,270
            # of his PDFs were filed as "not a PDF" from this 2 KB page and never read). A pause, never a verdict.
            return {"title": title, "err": "google busy", "busy": True}
        if head[:5] != b"%PDF-":
            r = {"title": title, "size": size, "kind": "notpdf", "v": 3}
            if re.search(r"\.(xlsx?|csv)$", title or "", re.I):
                r.update(kind="sheet", **research("", title))
            elif re.search(r"\.(jpe?g|png|webp)$", title or "", re.I):
                r["kind"] = "image"
            return r
        txt = subprocess.run(["pdftotext", "-l", "4", "-layout", pdf, "-"], capture_output=True, text=True,
                             errors="ignore", timeout=120).stdout
        scanned = len(txt.strip()) < 300
        if scanned:  # no text layer: OCR the first three pages
            txt = ""
            for i in (1, 2, 3):
                img = os.path.join(w, f"p{i}")
                # 8 Nov 2026: very large scanned pages made OCR run past its time limit and the whole file failed (575 of
                # his files); pages are now capped at a readable size, and one slow page no longer sinks the file
                try:
                    subprocess.run(["pdftoppm", "-scale-to", "1700", "-gray", "-f", str(i), "-l", str(i), "-png", "-singlefile", pdf, img],
                                   capture_output=True, timeout=90)
                    if os.path.exists(img + ".png"):
                        txt += subprocess.run(["tesseract", img + ".png", "-", "--psm", "6"], capture_output=True, text=True,
                                              errors="ignore", timeout=150).stdout + "\n"
                except subprocess.TimeoutExpired:
                    continue
        txt = fix(txt)
        k, y, mon = period(txt)
        tk = company(txt) or company(txt, wide=True)
        m = re.match(r"^([A-Z]{2,8})[ _-]", title or "")
        if not tk and m and any(m.group(1) == t for t, _ in NAMES):
            tk = m.group(1)  # files he named himself (TICKER FY2024 ...)
        r = {"title": title, "size": size, "tk": tk, "kind": k, "y": y, "mon": mon, "scanned": scanned, "v": 4}
        if not tk:
            r["co"] = coname(txt)
            if r["co"] and name_to_ticker(r["co"]):
                r["tk"] = name_to_ticker(r["co"])
                r.pop("co")
        # a free model reads the cover when the rules left the company or the year out (10 Nov 2026); research files
        # with a known publisher are left alone
        if (not r.get("tk") or not r.get("y") or not r.get("kind")) and len(txt.strip()) > 200:
            try:
                by_model(r, txt, title)
            except Exception:
                pass
        if not r.get("kind") or r.get("kind") == "other" or not r.get("tk"):  # research material, by publisher and sector
            rs = research(txt, title or "")
            if rs["src"] or rs["sectors"]:
                r.update(kind=r.get("kind") if r.get("kind") not in (None, "other") else "R", research=rs)
        return r
    except Exception as e:
        return {"title": title, "err": str(e)[:80]}
    finally:
        subprocess.run(["rm", "-rf", w])


def main():
    t0 = time.time()
    db = jl(OUT, {}) or {}
    # hand indexed files with a company and year are left alone; those missing either are looked at (9 Nov 2026)
    known = {r["id"] for r in jl(os.path.join(FILES, "library_index.json"), []) or [] if r.get("id") and r.get("tk") and r.get("y")}
    files = listing()
    # a failed download gets two more tries, in later runs
    # looked at again (8 Nov 2026): files that failed on the old OCR time limit, and files matched to no listed company,
    # which now get their printed company name (companies that left the market)
    again = lambda r: (r.get("err") and (r.get("busy") or r.get("tries", 1) < 3 or ("tesseract" in str(r["err"]) and r.get("v", 1) < 2))) \
        or (r.get("kind") == "notpdf" and r.get("size") == 2040) \
        or (not r.get("err") and not r.get("tk") and r.get("v", 1) < 2) \
        or (not r.get("err") and not r.get("kind") and r.get("v", 1) < 3) \
        or (not r.get("err") and r.get("v", 1) < 4 and r.get("kind") not in ("notpdf", "image", "sheet", "CB")
            and not (r.get("kind") == "R" and (r.get("research") or {}).get("src"))
            and (not r.get("tk") or not r.get("y") or not r.get("kind")))
    # 10 Nov 2026: every file still missing its company, kind or year is looked at once more with the better name
    # matching and a free model reading the cover (research files with a known publisher are left alone)
    todo = [(f, t) for f, t in files.items() if f not in known and (f not in db or again(db[f])) and mine(f)]
    todo.sort(key=lambda x: x[0] in db)  # files never seen first
    lock, done, busy = threading.Lock(), [0], [0]
    with ThreadPoolExecutor(6) as pool:   # 6 at a time; Google's busy answers now slow it down instead of losing files
        futs = {}
        it = iter(todo)
        for _ in range(6):
            x = next(it, None)
            if x:
                futs[pool.submit(ident, *x)] = x
        while futs:
            for f in as_completed(list(futs)):
                fid, title = futs.pop(f)
                r = f.result()
                with lock:
                    old = db.get(fid) or {}
                    if r.get("busy"):
                        busy[0] += 1
                        r["tries"] = old.get("tries", 0)   # Google being busy is not the file's fault
                    elif r.get("err"):
                        r["tries"] = old.get("tries", 0) + 1
                        r["v"] = 2
                        busy[0] = 0
                    else:
                        busy[0] = 0
                    r["at"] = time.strftime("%Y-%m-%d")
                    # a failed look never replaces a file already identified (9 Nov 2026)
                    if not (r.get("err") and old.get("kind") and old.get("kind") != "notpdf" and not old.get("err")):
                        db[fid] = r
                    done[0] += 1
                    if done[0] % 10 == 0:
                        json.dump(db, open(OUT, "w"), indent=0)
                if busy[0] >= 5:   # Google is limiting: slow down, and stop this run if it goes on
                    # never sleep past the run's own time limit (9 Nov 2026: sleeps ran the step out of time)
                    time.sleep(max(0, min(300, 30 * busy[0], MINUTES * 60 - (time.time() - t0))))
                    if busy[0] >= 25:
                        print("google busy: stopping this run early; the next run carries on", flush=True)
                        t0 = 0
                if time.time() - t0 < MINUTES * 60:
                    x = next(it, None)
                    if x:
                        futs[pool.submit(ident, *x)] = x
                break
    json.dump(db, open(OUT, "w"), indent=0)
    ann = [r for r in db.values() if r.get("kind") == "A" and r.get("tk") and r.get("y")]
    left = sum(1 for f, _ in todo) - done[0]
    print(f"done: drive folder lists {len(files)} files; {len(known)} were indexed by hand, {len(db)} by the robot "
          f"({done[0]} this run, {max(0, left)} still to look at); {len(ann)} annual reports with company and year, "
          f"{sum(1 for r in db.values() if r.get('err'))} downloads failed; Drive API gave {API_N[0]} files, refused {API_N[1]}")


if __name__ == "__main__":
    main()
