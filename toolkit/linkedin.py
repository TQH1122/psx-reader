#!/usr/bin/env python3
"""LinkedIn feed for the PSX Tracker (12 Nov 2026). Free only: Apify's free plan ($5 credit renewed every month).

He wants the broker and company material posted on LinkedIn collected for the tracker, without ever paying.
LinkedIn has no free official route, so posts are fetched by an Apify posts reader that needs no LinkedIn login
(his own account is never touched). Everything read is saved only in the private psx-tracker repository; this
public repository gets the code and a counts only status file.

Each run (daily, kept alive by the heartbeat):
1. Finds pages for free: the LinkedIn company link in the footer of each broker's and each listed company's own
   website (company sites come from the website report finder's catalogue). A few broker page names are also
   tried directly; such a guessed page is kept only if its posts are plainly about Pakistan's market.
2. Checks the month's free credit first and never goes past $4.60 of the $5 (a hard cap is also passed to every
   Apify run, so a run cannot overspend even if a count is off).
3. Fetches only posts newer than the last one already saved for each page: brokers and data sources daily,
   listed companies weekly (largest first). Leftover credit late in the month goes to one broker's last 12 months.
4. Keeps one copy of every post: same post id, or the same long text posted by several pages, is stored once with
   a note of every page it appeared on. Attached PDF carousels are downloaded and their text kept.
5. Tags each post with the tickers it names and saves it under data/files/linkedin/ in the private tracker.

  python3 toolkit/linkedin.py --tracker TRACKER_DIR [--status PUBLIC_STATUS_FILE]
Needs APIFY_TOKEN. Ends with one line: "done: ..." or "stopped: <plain reason>".
"""
import hashlib, json, os, re, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.parse, urllib.request, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_CAT = os.path.join(ROOT, "data", "files", "annual", "web_catalogue.json")
API = "https://api.apify.com/v2"
ACTORS = ["harvestapi~linkedin-company-posts", "sauliusautomatesit~linkedin-posts-scraper-api"]
UA = "psx-tracker-linkedin-finder/1.0 (personal investment research; reads the LinkedIn link on a home page)"
FREE_CAP = 4.60            # of the $5 monthly free credit; the rest is a safety margin
PRICE = 0.0025             # dollars per post, a little above the dearer actor, used only for planning
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

# Brokers and data sources: home pages whose footer carries the LinkedIn link. A dead address just finds nothing.
BROKER_SITES = {
    "Arif Habib": ["arifhabibltd.com"], "Topline": ["topline.com.pk", "toplinesecurities.com"],
    "AKD Securities": ["akdsecurities.net", "akdsl.com"], "JS Global": ["jsgcl.com"],
    "IGI Securities": ["igisecurities.com.pk"], "Insight Securities": ["insightsec.com.pk"],
    "Intermarket": ["intermarketsecurities.com", "imsecurities.com.pk"], "BMA Capital": ["bmacapital.com"],
    "Akseer Research": ["akseerresearch.com"], "Alpha Capital": ["alphacapital.com.pk", "alphapk.com"],
    "Sherman Securities": ["shermansecurities.com", "sherman.com.pk"], "Chase Securities": ["chasesecurities.pk", "chasesecurities.com.pk"],
    "Foundation Securities": ["fs.com.pk", "foundationsecurities.com.pk"], "Optimus Capital": ["optimus.com.pk"],
    "Taurus Securities": ["taurus.com.pk"], "Next Capital": ["nextcapital.com.pk"], "Al Habib Capital": ["ahcml.com"],
    "Pearl Securities": ["pearlsecurities.com"], "Ismail Iqbal": ["iis.com.pk", "ismailiqbal.com"],
    "Darson Securities": ["darsonsecurities.com"], "Standard Capital": ["standardcapital.pk"],
    "Elixir Securities": ["elixirsecurities.com"], "MRA Securities": ["mrasecurities.com"],
    "Spectrum Securities": ["spectrumsecurities.com.pk"], "Abbasi Securities": ["abbasisecurities.com"],
}
SOURCE_SITES = {
    "PSX": ["psx.com.pk"], "SECP": ["secp.gov.pk"], "State Bank": ["sbp.org.pk"], "Mettis Global": ["mettisglobal.news"],
    "Business Recorder": ["brecorder.com"], "PACRA": ["pacra.com"], "VIS": ["vis.com.pk"], "MUFAP": ["mufap.com.pk"],
    "CDC": ["cdcpakistan.com"], "NCCPL": ["nccpl.com.pk"], "APCMA": ["apcma.com"], "APTMA": ["aptma.org.pk"],
    "PBC": ["pbc.org.pk"], "OICCI": ["oicci.org"], "Profit": ["profit.pakistantoday.com.pk"],
}
# guessed page names, kept only if their posts are about Pakistan's market
BROKER_GUESSES = {
    "Topline": ["topline-securities", "topline-securities-limited"], "Arif Habib": ["arif-habib-limited"],
    "AKD Securities": ["akd-securities-limited", "akd-securities"], "JS Global": ["js-global-capital-limited"],
    "IGI Securities": ["igi-securities"], "Insight Securities": ["insight-securities"],
    "Intermarket": ["intermarket-securities-limited"], "BMA Capital": ["bma-capital-management-limited"],
    "Akseer Research": ["akseer-research"], "Sherman Securities": ["sherman-securities"],
    "Chase Securities": ["chase-securities-pakistan"], "Optimus Capital": ["optimus-capital-management"],
    "Al Habib Capital": ["al-habib-capital-markets"], "Next Capital": ["next-capital-limited"],
}
PK_WORDS = re.compile(r"\b(psx|kse|kmi|pakistan|karachi|lahore|islamabad|sbp|secp|pkr|rs\.?\s?\d|imf|policy rate|"
                      r"equit(y|ies)|stock market|bourse)\b", re.I)
LI_LINK = re.compile(r"linkedin\.com/(company|showcase)/([A-Za-z0-9\-_.%]+)", re.I)
NOT_TICKER = set("""THE AND FOR WITH FROM THIS THAT YOU OUR ARE HAS HAVE WAS NOT BUT ALL NEW CEO CFO COO PSX KSE KMI SBP SECP IMF
GDP CPI EPS DPS YOY QOQ MOM ROE ROA ROI PKR USD BPS LNG RLNG OMC IPP AGM EGM CBS EBITDA PAT PBT NII FY CY ADR GDR ETF
NAV AMC MTS IPO CSR ESG PDF LIVE NEWS NOTE DAY DAYS TOP BUY SELL HOLD CALL TIME WEEK YEAR TODAY JOIN TEAM MORE ONE TWO
OIL GAS CEMENT STEEL BANK BANKS POWER AUTO TECH TARGET PRICE RATE RATES REPORT DATA FOOD FOODS PAK SAY WHY HOW WHAT
WHO KEY LOW HIGH CAN WILL ITS GET SEE OUT END PER NET TAX BIG BEST GOOD JUST NOW""".split())


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- page finding (free)
def fetch_home(domain):
    """Return the html of a site's home page, obeying robots.txt; '' if unreachable or not allowed."""
    domain = re.sub(r"^https?://", "", domain).strip("/").split("/")[0]
    if not domain or "." not in domain or domain in ("www.pk", "www"):
        return ""
    for base in (f"https://{domain}", f"https://www.{domain}" if not domain.startswith("www.") else None, f"http://{domain}"):
        if not base:
            continue
        try:
            rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
            try:
                rp.read()
                if not rp.can_fetch(UA, base + "/"):
                    return ""
            except Exception:
                pass
            req = urllib.request.Request(base + "/", headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read(1_500_000).decode("utf-8", "ignore")
        except Exception:
            continue
    return ""


def slug_from_html(html):
    for kind, slug in LI_LINK.findall(html or ""):
        slug = urllib.parse.unquote(slug).strip(".").split("?")[0]
        if slug and slug.lower() not in ("linkedin", "company", "showcase", "share", "sharearticle"):
            return f"{kind.lower()}/{slug}"
    return None


def find_pages(state, tickers_alive, minutes):
    """Footer links from broker, source and company home pages. Each site is visited again after 45 days."""
    sites = state.setdefault("sites", {})
    pages = state.setdefault("pages", {})
    jobs = []
    for group, kind in ((BROKER_SITES, "broker"), (SOURCE_SITES, "source")):
        for owner, domains in group.items():
            jobs.append((f"{kind}:{owner}", kind, owner, domains))
    cat = jload(WEB_CAT, {}).get("co", {})
    for t, v in cat.items():
        site = (v or {}).get("site") or ""
        if t in tickers_alive and re.search(r"[a-z0-9-]+\.[a-z]{2,}", site):
            jobs.append((f"company:{t}", "company", t, [site]))
    due = []
    for key, kind, owner, domains in jobs:
        s = sites.get(key)
        wait = 45 if (s and s.get("slug")) else 10
        if not s or (NOW - datetime.fromisoformat(s["at"])).days >= wait:
            due.append((key, kind, owner, domains))
    stop = time.time() + minutes * 60
    lock = threading.Lock()
    found = [0]

    def work(job):
        key, kind, owner, domains = job
        if time.time() > stop:
            return
        slug = None
        for d in domains:
            slug = slug_from_html(fetch_home(d))
            if slug:
                break
        with lock:
            sites[key] = {"at": NOW.isoformat(timespec="seconds"), "slug": slug}
            if slug:
                p = pages.setdefault(slug, {"kind": kind, "owner": owner, "from": "site", "found": TODAY})
                if p.get("from") == "guess":       # a site link proves a guessed page
                    p.update(kind=kind, owner=owner, **{"from": "site"})
                found[0] += 1

    with ThreadPoolExecutor(12) as ex:
        list(ex.map(work, due))
    for owner, slugs in BROKER_GUESSES.items():
        for s in slugs:
            pages.setdefault(f"company/{s}", {"kind": "broker", "owner": owner, "from": "guess", "found": TODAY})
    return len(due), found[0]


# ---------------------------------------------------------------- Apify (free credit only)
def apify(method, path, token, body=None, timeout=60):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def credit_left(token):
    """Dollars still free this month, under our own cap. Raises if the key is wrong."""
    d = (apify("GET", "/users/me/limits", token) or {}).get("data") or {}
    used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
    plan = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 5)
    return round(min(plan, 5.0) - (5.0 - FREE_CAP) - used, 4), used


def actor_input(actor, urls, since, max_posts):
    if actor.startswith("harvestapi"):
        return {"targetUrls": urls, "maxPosts": max_posts, "postedLimitDate": since,
                "includeReposts": False, "includeQuotePosts": True, "scrapeReactions": False, "scrapeComments": False}
    return {"urls": urls, "maxPostsPerCompany": max_posts or 10, "postedAfter": since, "includeReposts": False}


def run_actor(token, urls, since, max_posts, dollars, state):
    """One Apify run with a hard spending cap. Returns (items, actor) or raises with a plain reason."""
    errors = []
    order = ACTORS if state.get("actor") != ACTORS[1] else ACTORS[::-1]
    for actor in order:
        try:
            q = urllib.parse.urlencode({"maxTotalChargeUsd": f"{max(dollars, 0.01):.2f}", "timeout": 1500})
            run = (apify("POST", f"/acts/{actor}/runs?{q}", token, actor_input(actor, urls, since, max_posts)) or {})["data"]
            rid, t0 = run["id"], time.time()
            while run.get("status") in ("READY", "RUNNING") and time.time() - t0 < 1600:
                time.sleep(15)
                run = apify("GET", f"/actor-runs/{rid}", token)["data"]
            items = apify("GET", f"/datasets/{run['defaultDatasetId']}/items?clean=true&format=json", token, timeout=180) or []
            if run.get("status") != "SUCCEEDED" and not items:
                raise RuntimeError(f"run ended {run.get('status')}")
            state["actor"] = actor
            return items, actor
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:200]
            if e.code == 401:
                raise RuntimeError("the Apify key was refused (APIFY_TOKEN may be wrong)")
            errors.append(f"{actor.split('~')[0]}: {e.code} {msg}")
        except Exception as e:
            errors.append(f"{actor.split('~')[0]}: {e}")
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------- posts
def norm_text(t):
    t = re.sub(r"https?://\S+", " ", (t or "").lower())
    return re.sub(r"[^a-z0-9%.]+", " ", t).strip()


def page_of(item, slugs):
    """Which watched page a post came from."""
    a = item.get("author") or {}
    cands = [a.get("publicIdentifier"), a.get("linkedinUrl"), a.get("url"), item.get("companyUrl"),
             item.get("input"), item.get("query") if isinstance(item.get("query"), str) else None]
    for c in cands:
        if not c:
            continue
        c = str(c).lower().rstrip("/")
        for s in slugs:
            if c == s.split("/", 1)[1].lower() or c.endswith("/" + s.lower()):
                return s
    return None


def to_ms(v):
    if isinstance(v, dict):
        v = v.get("timestamp") or v.get("date")
    if isinstance(v, (int, float)):
        return int(v if v > 1e11 else v * 1000)
    if isinstance(v, str) and v:
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            return None
    return None


def normalise(item, slug):
    eng = item.get("engagement") or {}
    doc = item.get("document") or {}
    imgs = item.get("postImages") or item.get("images") or []
    imgs = [i.get("url") if isinstance(i, dict) else i for i in imgs][:10]
    a = item.get("author") or {}
    ms = to_ms(item.get("postedAt"))
    return {
        "id": str(item.get("id") or item.get("urn") or item.get("linkedinUrl") or item.get("url")),
        "url": item.get("linkedinUrl") or item.get("url") or "",
        "date": datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="minutes") if ms else None,
        "page": slug, "by": a.get("name") or item.get("companyName") or "",
        "text": (item.get("content") or item.get("text") or "")[:20000],
        "doc": ({"title": doc.get("title"), "url": doc.get("transcribedDocumentUrl") or doc.get("url"),
                 "pages": doc.get("totalPageCount")} if doc else None),
        "images": [i for i in imgs if i],
        "likes": eng.get("likes") if eng else item.get("reactions") if isinstance(item.get("reactions"), int) else None,
        "comments": eng.get("comments") if eng else item.get("comments") if isinstance(item.get("comments"), int) else None,
    }


def doc_text(url):
    """Text of an attached PDF carousel (broker slides), at most 25 MB."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(25_000_000)
        if not data.startswith(b"%PDF"):
            return ""
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", "ignore")[:150000]
    except Exception:
        return ""


def ticker_tagger(rows):
    names, tick = [], set()
    for r in rows:
        t = (r.get("s") or "").upper()
        if len(t) >= 3 and t not in NOT_TICKER and not t.isdigit():
            tick.add(t)
        n = re.sub(r"\b(limited|ltd|company|co|corporation|pakistan|\(pvt\)|private|plc)\b\.?", " ", (r.get("n") or "").lower())
        n = re.sub(r"[^a-z0-9& ]+", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if len(n) >= 6 and len(n.split()) >= 2:
            names.append((n, t))
    name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n, _ in sorted(names, key=lambda x: -len(x[0]))) + r")\b") if names else None
    by_name = dict(names)

    def tag(text):
        found = set()
        for m in re.finditer(r"(?:[#$]|\bPSX:\s?)?\b([A-Z]{3,9})\b", text or ""):
            if m.group(1) in tick:
                found.add(m.group(1))
        if name_re:
            low = re.sub(r"[^a-z0-9& ]+", " ", (text or "").lower())
            for m in name_re.finditer(re.sub(r"\s+", " ", low)):
                found.add(by_name[m.group(1)])
        return sorted(found)
    return tag


class Store:
    """data/files/linkedin/posts/YYYY-MM.json in the private tracker, one copy of each post."""

    def __init__(self, base):
        self.base = os.path.join(base, "posts")
        self.months, self.dirty = {}, set()
        self.ids, self.fps = {}, {}
        os.makedirs(self.base, exist_ok=True)
        for fn in sorted(os.listdir(self.base)):
            if fn.endswith(".json"):
                m = fn[:-5]
                self.months[m] = jload(os.path.join(self.base, fn), [])
                for i, p in enumerate(self.months[m]):
                    self.ids[p["id"]] = (m, i)
                    if p.get("fp"):
                        self.fps[p["fp"]] = (m, i)

    def add(self, p):
        if p["id"] in self.ids:
            return False
        nt = norm_text(p["text"])
        fp = hashlib.sha1(nt.encode()).hexdigest()[:16] if len(nt) >= 80 else None
        if fp and fp in self.fps:                 # same long text from another page: note it, keep one copy
            m, i = self.fps[fp]
            old = self.months[m][i]
            also = old.setdefault("also", [])
            if p["page"] != old["page"] and p["page"] not in also:
                also.append(p["page"])
                self.dirty.add(m)
            self.ids[p["id"]] = (m, i)
            return False
        p["fp"] = fp
        m = (p["date"] or NOW.isoformat())[:7]
        lst = self.months.setdefault(m, [])
        lst.append(p)
        self.ids[p["id"]] = (m, len(lst) - 1)
        if fp:
            self.fps[fp] = (m, len(lst) - 1)
        self.dirty.add(m)
        return True

    def drop_page(self, slug):
        """A guessed page that turned out not to be about Pakistan's market: remove what it brought."""
        for m, lst in self.months.items():
            keep = [p for p in lst if p["page"] != slug]
            if len(keep) != len(lst):
                self.months[m] = keep
                self.dirty.add(m)
        self.ids = {}
        self.fps = {}
        for m, lst in self.months.items():
            for i, p in enumerate(lst):
                self.ids[p["id"]] = (m, i)
                if p.get("fp"):
                    self.fps[p["fp"]] = (m, i)

    def save(self):
        for m in self.dirty:
            self.months[m].sort(key=lambda p: p.get("date") or "")
            jsave(os.path.join(self.base, m + ".json"), self.months[m])
        self.dirty.clear()

    def index(self):
        """by_ticker.json: ticker -> newest first [date, month, id] for the tracker's pages and Ask PSX."""
        idx = {}
        for m, lst in self.months.items():
            for p in lst:
                for t in p.get("tickers", []):
                    idx.setdefault(t, []).append([p.get("date"), m, p["id"]])
        for t in idx:
            idx[t].sort(reverse=True)
        return idx


# ---------------------------------------------------------------- one run
def main():
    args = sys.argv[1:]
    tracker = args[args.index("--tracker") + 1] if "--tracker" in args else os.path.join(ROOT, "tracker")
    status_file = args[args.index("--status") + 1] if "--status" in args else None
    base = os.path.join(tracker, "data", "files", "linkedin")
    state_path = os.path.join(base, "state.json")
    state = jload(state_path, {})
    token = os.environ.get("APIFY_TOKEN", "").strip()
    rows = jload(os.path.join(tracker, "data", "db", "market", "screener.json"), {}).get("rows") \
        or jload(os.path.join(ROOT, "data", "db", "market", "screener.json"), {}).get("rows", [])
    alive = set(jload(os.path.join(tracker, "data", "db", "market", "active.json"), {}).get("s")
                or jload(os.path.join(ROOT, "data", "db", "market", "active.json"), {}).get("s", []))
    mcap = {r.get("s"): r.get("mc") or 0 for r in rows}
    summary = {"at": NOW.isoformat(timespec="minutes"), "new": 0, "spent": 0.0}

    def finish(line, ok=True):
        pages = state.get("pages", {})
        if store:
            summary["new"] = sum(len(v) for v in store.months.values()) - before
        live = [s for s, p in pages.items() if p.get("from") != "guess" or p.get("relevant")]
        summary.update(line=line, ok=ok, pages=len(live),
                       brokers=len({p["owner"] for s, p in pages.items() if p["kind"] == "broker" and s in live}),
                       companies=len({p["owner"] for s, p in pages.items() if p["kind"] == "company" and s in live}))
        state.setdefault("runs", []).append(summary)
        state["runs"] = state["runs"][-40:]
        jsave(state_path, state)
        if status_file:
            total = sum(len(v) for v in store.months.values()) if store else 0
            jsave(status_file, {k: summary.get(k) for k in ("at", "ok", "line", "new", "spent", "creditLeft", "unmatched",
                                                             "pages", "brokers", "companies")}
                  | {"posts": total, "months": len(store.months) if store else 0})
        print(line, flush=True)
        sys.exit(0 if ok else 1)

    store, before = None, 0
    if not token:
        finish("stopped: the Apify key (APIFY_TOKEN) is missing", ok=False)
    looked, found = find_pages(state, alive, minutes=12)
    log(f"page finder: visited {looked} home pages, found {found} LinkedIn pages")
    store = Store(base)
    before = sum(len(v) for v in store.months.values())
    try:
        left, used = credit_left(token)
    except urllib.error.HTTPError as e:
        finish("stopped: the Apify key was refused (APIFY_TOKEN may be wrong)" if e.code in (401, 403)
               else f"stopped: Apify did not answer ({e.code})", ok=False)
    except Exception as e:
        finish(f"stopped: Apify could not be reached ({e})", ok=False)
    summary["usedBefore"] = used
    tag = ticker_tagger(rows)
    pages = state["pages"]
    slugs = list(pages)

    def due(slug):
        p = pages[slug]
        if p.get("from") == "guess" and p.get("relevant") is False:
            return False
        if p.get("dead", 0) >= 3:                # three empty fetches in a row: try again after 60 days
            return (NOW - datetime.fromisoformat(p["checked"])).days >= 60
        last = p.get("checked")
        gap = timedelta(days=7) if p["kind"] == "company" else timedelta(hours=20)
        return not last or NOW - datetime.fromisoformat(last) >= gap

    # brokers and sources first, then companies by size
    todo = sorted([s for s in slugs if due(s)],
                  key=lambda s: (pages[s]["kind"] == "company", -(mcap.get(pages[s]["owner"]) or 0)))
    groups = {}
    for s in todo:
        last = pages[s].get("last")
        first = 14 if pages[s]["kind"] == "company" else 30          # how far back a newly found page starts
        since = (datetime.fromisoformat(last).date().isoformat() if last
                 else (NOW - timedelta(days=first)).date().isoformat())
        groups.setdefault((pages[s]["kind"] == "company", since), []).append(s)
    # brokers and data sources must last the whole month, so companies only use what is above their reserve
    nxt = (NOW.replace(day=28) + timedelta(days=4)).replace(day=1)
    reserve = round(0.06 * max((nxt - NOW).days, 1), 2)

    def take(batch, since, max_posts, dollars, why):
        items, actor = run_actor(token, ["https://www.linkedin.com/" + s for s in batch], since, max_posts, dollars, state)
        got = {s: 0 for s in batch}
        newest = {}
        for it in items:
            s = page_of(it, batch) or (batch[0] if len(batch) == 1 else None)
            if not s:                       # paid for, so kept even when its page can't be told; counted for a fix
                p = normalise(it, None)
                if p["id"] and p["id"] != "None":
                    p.update(tickers=tag(p["text"]), kind="unknown", owner=p["by"])
                    store.add(p)
                summary["unmatched"] = summary.get("unmatched", 0) + 1
                continue
            p = normalise(it, s)
            if not p["id"] or p["id"] == "None":
                continue
            got[s] += 1
            if p["date"] and p["date"] > newest.get(s, ""):
                newest[s] = p["date"]
            if p.get("doc") and p["doc"].get("url"):
                p["doc"]["text"] = doc_text(p["doc"]["url"])
            p["tickers"] = sorted(set(tag(p["text"] + " " + ((p.get("doc") or {}).get("text") or "")[:20000]))
                                  | ({pages[s]["owner"]} if pages[s]["kind"] == "company" else set()))
            p["kind"], p["owner"] = pages[s]["kind"], pages[s]["owner"]
            if pages[s].get("from") == "guess" and pages[s].get("relevant") is None:
                pages[s].setdefault("_pk", [0, 0])
                pages[s]["_pk"][0] += bool(PK_WORDS.search(p["text"]))
                pages[s]["_pk"][1] += 1
            store.add(p)
        for s in batch:
            pg = pages[s]
            pg["checked"] = NOW.isoformat(timespec="seconds")
            pg["posts"] = pg.get("posts", 0) + got[s]
            pg["dead"] = 0 if got[s] else pg.get("dead", 0) + (1 if not pg.get("last") else 0)
            if newest.get(s) and newest[s] > (pg.get("last") or ""):
                pg["last"] = newest[s]
            if "_pk" in pg:
                hit, n = pg.pop("_pk")
                pg["relevant"] = (hit / n >= 0.3) if n >= 3 else None
                if pg["relevant"] is False:
                    store.drop_page(s)
        store.save()
        log(f"{why}: {len(batch)} pages, {len(items)} posts read with {actor.split('~')[0]}")
        return len(items)

    problems = []
    spend_start = left
    # brokers and sources first, then companies; within each, the most recent "since" first (cheapest, freshest)
    for comp, since in sorted(groups, key=lambda k: (k[0], [-ord(c) for c in k[1]])):
        batch_all = groups[(comp, since)]
        room = left - (reserve if comp else 0)
        for i in range(0, len(batch_all), 40):
            room = left - (reserve if comp else 0)
            if room < 0.05:
                break
            batch = batch_all[i:i + 40]
            try:
                n = take(batch, since, 0, min(room, 1.5), f"{'company' if comp else 'broker and source'} posts since {since}")
                left = round(left - n * PRICE, 4)
            except Exception as e:
                problems.append(str(e)[:160])
                break
        if problems:
            break
    # leftover credit in the last ten days of the month: one broker's last 12 months
    if not problems and left > 1.0 and NOW.day >= 21:
        back = [s for s in slugs if pages[s]["kind"] == "broker" and not pages[s].get("backfill")
                and pages[s].get("posts", 0) > 0 and (pages[s].get("from") != "guess" or pages[s].get("relevant"))]
        if back:
            s = back[0]
            try:
                n = take([s], (NOW - timedelta(days=365)).date().isoformat(), min(int((left - 0.3) / PRICE), 400),
                         left - 0.3, "older posts")
                pages[s]["backfill"] = TODAY
                left = round(left - n * PRICE, 4)
            except Exception as e:
                problems.append(str(e)[:160])
    try:
        left_now, used_now = credit_left(token)
        summary["spent"] = round(used_now - used, 4)
        summary["creditLeft"] = round(left_now, 2)
    except Exception:
        summary["spent"] = round(spend_start - left, 4)
    jsave(os.path.join(base, "by_ticker.json"), store.index())
    summary["new"] = sum(len(v) for v in store.months.values()) - before
    if problems:
        finish(f"stopped: {summary['new']} new posts saved, then Apify failed: {problems[0]}", ok=False)
    if left < 0.05 and any(due(s) for s in slugs):
        finish(f"done: {summary['new']} new posts; this month's free credit is used up, the rest waits for next month")
    finish(f"done: {summary['new']} new posts saved, ${summary['spent']:.2f} of free credit used this run")


if __name__ == "__main__":
    main()
