#!/usr/bin/env python3
"""Free AI reading panel for the PSX Tracker (website batch 5, 1 Nov 2026).

Reads the broker and rating agency reports that research.py collects (text in data/files/research/<id>.txt)
with free AI models, and keeps only figures that pass every check:
  1. two different models read the same number for the same company, period and line, and
  2. that number is printed in the report text itself (no model may calculate or invent a figure), and
  3. the ticker is a live PSX listing in the screener.
A third model settles a report when the first two disagree. Figures that fail stay in the raw reads for
audit but never reach the website. Qualitative points (drivers, outlook, risks) come from the first model
and are shown as an AI summary with a link to the broker's PDF.

Free models used, whichever have a key in the environment (GitHub secrets):
  GEMINI_API_KEY      Google AI Studio (required): the best free Flash model reads, a second Flash model
                      (lite, separate daily quota) checks, so one key already gives two readers.
  GROQ_API_KEY        optional: gpt-oss, Llama, Qwen, Kimi on Groq, each its own daily quota.
  OPENROUTER_API_KEY  optional: free DeepSeek, Qwen and similar models.
  MISTRAL_API_KEY     optional: Mistral (its free plan no longer allows API keys, Nov 2026).
  NVIDIA_API_KEY      optional: NVIDIA's free API (DeepSeek, Kimi, MiniMax, GLM).
  ZAI_API_KEY         optional: Z.ai's free GLM Flash models (China's Zhipu).
  CF_ACCOUNT_ID + CF_AI_TOKEN  optional: Cloudflare Workers AI free allowance.
Models are found by asking each service which models it offers, so renamed or newer models are picked up.

Usage: python3 toolkit/reader.py [--minutes N] [--max N] [--db DIR] [--rebuild]
  --minutes  stop starting new reports after N minutes (default 30)
  --max      read at most N reports this run
  --rebuild  no AI calls: rebuild market/readings.json from the saved reads
Writes data/files/readings/<id>.json (every model's read of that report) and data/db/market/readings.json
(checked figures for the website). Prints one result line at the end.
"""
import bisect, json, os, re, sys, threading, time, urllib.error, urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = sys.argv[1:]
opt = lambda k, d: A[A.index(k) + 1] if k in A else d
DB = opt("--db", os.path.join(ROOT, "data", "db"))
FILES = os.path.join(ROOT, "data", "files")
TEXT, RAW = os.path.join(FILES, "research"), os.path.join(FILES, "readings")
MINUTES, MAXN, REBUILD = float(opt("--minutes", 30)), int(opt("--max", 100000)), "--rebuild" in A
SKIP_KINDS = {"technical"}           # chart outlooks: no fundamentals in them
AMT = ("rev", "gp", "op", "pat")     # amounts, converted to Rs mn
PER = ("eps", "dps")                 # per share, Rs
LABEL = {"rev": "sales", "gp": "gross profit", "op": "operating profit", "pat": "profit", "eps": "EPS", "dps": "DPS"}

PROMPT = """You read one research report about the Pakistan Stock Exchange (a broker report or a rating agency study).
Return only JSON in exactly this shape:
{"type":"result review|result preview|company|sector|economy|strategy|other",
 "summary":"one plain sentence on what the report says",
 "companies":[{"ticker":"PSX ticker","rating":"buy|hold|sell|none","tp":null,
   "results":[{"period":"like 1QFY27, 2QFY27, 1HFY27, 9MFY26, FY26","actual":true,"basis":"consolidated|unconsolidated|not stated","unit":"unit of the amounts as printed, like PKR mn or PKR bn","rev":null,"gp":null,"op":null,"pat":null,"eps":null,"dps":null}],
   "forecasts":[{"year":"like FY27","eps":null,"dps":null}],
   "points":["up to 4 short plain bullets: what drove the results, the outlook, the main risks"]}],
 "macro":[{"name":"like CPI YoY %, policy rate %, current account USD mn, remittances USD mn","period":"like Oct-26","value":null}]}
Rules:
- Copy every number exactly as printed in the report. Never calculate, convert, round or guess. Use null when a figure is not printed.
- A loss is a negative number. tp is the target price or fair value per share.
- rev is sales or revenue, gp gross profit, op operating profit, pat profit after tax for the period (attributable to owners when shown), eps earnings per share, dps dividend per share announced for the period.
- results holds reported actual figures only; a preview's expected figures have "actual":false.
- List only companies the report covers in detail, by their PSX ticker. List macro figures only for economy reports.
REPORT:
"""


# ---------------------------------------------------------------- web calls
def http(url, body=None, headers=None, timeout=100):
    data = json.dumps(body).encode() if body is not None else None
    # a plain browser style name: Groq's gate refuses Python's default one (error 1010)
    req = urllib.request.Request(url, data=data, headers=dict({"content-type": "application/json",
                                 "user-agent": "Mozilla/5.0 (compatible; psx-tracker/1.0)"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)


def jload(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def jfrom(text):
    """the JSON object inside a model's answer"""
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    a, z = t.find("{"), t.rfind("}")
    return jload(t[a:z + 1]) if a >= 0 and z > a else None


CALLS = []  # every model call this run: model, answer code, seconds (saved for checking)


class Reader:
    """One free model. Keeps its own pace and stops for the day when its quota is spent."""
    def __init__(self, svc, model, gap, call):
        self.svc, self.model, self.gap, self.call = svc, model, gap, call
        self.name = f"{svc}:{model}"
        self.next, self.out, self.n, self.err, self.lock = 0.0, False, 0, "", threading.Lock()

    def family(self):
        return re.sub(r"[-_.:/]|lite|latest|preview|\d", "", self.model.lower())[:12]

    def ask(self, prompt):
        bad_json = 0
        for attempt in range(4):
            if self.out:
                return None
            with self.lock:
                wait = self.next - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.next = time.time() + self.gap
            t1 = time.time()
            code, body = self.call(self.model, prompt)
            CALLS.append([self.model, code, round(time.time() - t1, 1), len(body or ""), (body or "")[:600] if code != 200 else ""])
            if code == 200:
                j = jfrom(body)
                self.n += 1
                if j is not None:
                    return j
                self.err, bad_json = "answer was not JSON", bad_json + 1
                if bad_json >= 2:
                    return None
                continue
            low = body.lower()
            if code == 429:
                # Google lists the limit that was hit as a quotaId; only a per day limit (or a model outside the free
                # plan, "limit: 0") rests the model until tomorrow. A per minute limit just means wait and try again.
                ids = " ".join(re.findall(r'"quotaId"\s*:\s*"([^"]+)"', body)).lower()
                daily = "perday" in ids or "limit: 0" in low or (not ids and ("per day" in low or "tokens per day" in low))
                if self.n == 0 and attempt >= 1:  # refuses from the start (e.g. Mistral's free plan, no API): skip it this run
                    self.out, self.err = True, "refused from the start (429)"
                    return None
                if daily and (ids or attempt >= 1 or "limit: 0" in low):
                    self.out, self.err = True, "daily free quota used"
                    return None
                m = re.search(r'"retryDelay"\s*:\s*"(\d+)', body) or re.search(r"try again in (\d+(?:\.\d+)?)s", low)
                time.sleep(min(90, float(m.group(1)) + 1 if m else 20 * (attempt + 1)))
                continue
            if code == 404:  # retired model
                self.out, self.err = True, "retired"
                return None
            if code in (500, 502, 503, 504, 0):
                self.slow = getattr(self, "slow", 0) + 1
                if self.slow >= 3:  # keeps hanging: leave it out for this run
                    self.out, self.err = True, f"no answer ({code}) three times"
                    return None
                time.sleep(8)
                continue
            self.out, self.err = True, f"answered {code}: {re.sub(r'[^A-Za-z0-9 .,:]', ' ', body)[:120]}"
            return None
        return None


def gemini_readers(key):
    base = "https://generativelanguage.googleapis.com/v1beta/models"
    code, body = http(f"{base}?pageSize=1000", headers={"x-goog-api-key": key}, timeout=40)
    names = []
    for m in (jload(body) or {}).get("models", []) if code == 200 else []:
        n = m.get("name", "").split("/")[-1]
        if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in n and "gemini" in n \
                and not re.search(r"image|tts|audio|live|embed|latest|thinking|exp|computer|robotics", n):
            names.append(n)
        # Gemma (Google's open models) on the same free key: a separate and much larger daily allowance
        elif "generateContent" in m.get("supportedGenerationMethods", []) and re.match(r"gemma-\d", n) \
                and re.search(r"\d{2,3}b", n) and not re.search(r"embed|e\d+b|nano", n):
            names.append(n)
    if not names:  # free on his key when checked on 1 Nov 2026
        names = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    ver = lambda n: float((re.search(r"gemini-(\d+(?:\.\d+)?)", n) or [0, 0])[1])
    rank = lambda n: (-ver(n), "preview" in n, len(n))
    gemma = sorted([n for n in names if n.startswith("gemma")],
                   key=lambda n: (-float((re.search(r"gemma-(\d+)", n) or [0, 0])[1]), -int((re.search(r"(\d+)b", n) or [0, 0])[1])))
    names = [n for n in names if not n.startswith("gemma")]
    full = sorted([n for n in names if "lite" not in n], key=rank)
    lite = sorted([n for n in names if "lite" in n], key=rank)

    def call(model, prompt):
        gc = {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 8192}
        if model.startswith("gemma"):
            gc.pop("responseMimeType")  # Gemma has no JSON mode; the answer's JSON is cut out of its text
        elif re.match(r"gemini-[3-9]", model):
            gc["thinkingConfig"] = {"thinkingLevel": "low"}
        elif model.startswith("gemini-2.5"):
            gc["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gc}
        code, out = http(f"{base}/{model}:generateContent", body, {"x-goog-api-key": key})
        if code == 400 and "thinking" in out.lower():
            gc.pop("thinkingConfig", None)
            code, out = http(f"{base}/{model}:generateContent", body, {"x-goog-api-key": key})
        if code == 200:
            j = jload(out) or {}
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return 200, "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return code, out
    # the best full Flash reads first; lite models and older Flash models check (each has its own daily quota)
    order = full + lite  # models outside the free plan answer "limit 0" at once and are left out for the day
    return [Reader("gemini", n, 6.5 if "lite" not in n else 4.5, call) for n in order] + \
        [Reader("gemma", n, 4, call) for n in gemma]


def openai_readers(svc, base, key, prefer, gap, listing=True, extra=None):
    hdr = {"authorization": f"Bearer {key}"}
    have = None
    if listing:
        code, body = http(f"{base}/models", headers=hdr, timeout=40)
        have = {m.get("id") for m in (jload(body) or {}).get("data", [])} if code == 200 else None
    models = [m for m in prefer if have is None or m in have]

    def call(model, prompt):
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}}
        body.update(extra or {})
        code, out = http(f"{base}/chat/completions", body, hdr)
        if code == 400 and "response_format" in out:
            body.pop("response_format")
            code, out = http(f"{base}/chat/completions", body, hdr)
        if code == 200:
            j = jload(out) or {}
            return 200, ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return code, out
    return [Reader(svc, m, gap, call) for m in models]


def openrouter_free():
    """OpenRouter's free models change often, so the list is read fresh each run: the strongest known
    general models first, then any other large free model; small, safety, code and audio models are left out."""
    prefer = ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free",
              "deepseek/deepseek-chat-v3-0324:free", "qwen/qwen3-235b-a22b:free", "moonshotai/kimi-k2:free"]
    code, body = http("https://openrouter.ai/api/v1/models", timeout=40)
    free = [m.get("id", "") for m in (jload(body) or {}).get("data", []) if code == 200]
    free = [m for m in free if m.endswith(":free")]
    if not free:
        return prefer[:4]
    skip = re.compile(r"inkling|safety|guard|code|coder|nano|mini|small|lite|lightning|tiny|\b\d(\.\d)?b\b|-[1-9]b|2\.6b|4b-|audio|tts")
    rest = [m for m in free if m not in prefer and not skip.search(m)]
    return ([m for m in prefer if m in free] + rest)[:5]


def panel():
    E = os.environ.get
    rs = []
    if E("GEMINI_API_KEY"):
        rs += gemini_readers(E("GEMINI_API_KEY"))
    if E("GROQ_API_KEY"):
        rs += openai_readers("groq", "https://api.groq.com/openai/v1", E("GROQ_API_KEY"),
                             ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct",
                              "qwen/qwen3-32b", "meta-llama/llama-4-maverick-17b-128e-instruct"], 8)
    if E("MISTRAL_API_KEY"):
        rs += openai_readers("mistral", "https://api.mistral.ai/v1", E("MISTRAL_API_KEY"),
                             ["mistral-medium-latest", "mistral-small-2603"], 2, listing=False)
    if E("NVIDIA_API_KEY"):  # NVIDIA's free developer API: Chinese models, 40 questions a minute shared, no daily cap
        rs += openai_readers("nvidia", "https://integrate.api.nvidia.com/v1", E("NVIDIA_API_KEY"),
                             ["deepseek-ai/deepseek-v4.1-flash", "moonshotai/kimi-k2.6",
                              "minimaxai/minimax-m3.1-flash", "z-ai/glm-5.3-flash"], 6)
    if E("ZAI_API_KEY"):  # Z.ai (Zhipu, China): GLM Flash models are free for good, one question at a time
        rs += openai_readers("zai", "https://api.z.ai/api/paas/v4", E("ZAI_API_KEY"),
                             ["glm-4.7-flash", "glm-4.5-flash"], 3, listing=False,
                             extra={"thinking": {"type": "disabled"}})
    if E("OPENROUTER_API_KEY"):
        rs += openai_readers("openrouter", "https://openrouter.ai/api/v1", E("OPENROUTER_API_KEY"),
                             openrouter_free(), 4)
    if E("CEREBRAS_API_KEY"):  # free plan with a large daily token allowance; models found from its list
        rs += openai_readers("cerebras", "https://api.cerebras.ai/v1", E("CEREBRAS_API_KEY"),
                             ["gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507", "zai-glm-4.6", "llama-3.3-70b"], 3)
    if E("CF_ACCOUNT_ID") and E("CF_AI_TOKEN"):
        rs += openai_readers("cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{E('CF_ACCOUNT_ID')}/ai/v1",
                             E("CF_AI_TOKEN"), ["@cf/openai/gpt-oss-120b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"],
                             3, listing=False)
    return rs


# ---------------------------------------------------------------- checks
NUM = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")


def numbers(text):
    out = []
    for m in NUM.finditer(text):
        s = m.group(0).strip("()").replace(",", "").lstrip("-")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return sorted(set(out))


def printed(v, nums):
    """the number appears in the report text (sign aside)"""
    v = abs(v)
    tol = max(0.005, abs(v) * 0.0005)
    i = bisect.bisect_left(nums, v - tol)
    return i < len(nums) and nums[i] <= v + tol


def fnum(x):
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def mn_factor(unit):
    u = (unit or "").lower().replace(" ", "")
    if re.search(r"bn|billion|b$", u):
        return 1000.0
    if re.search(r"mn|million|m$|mln", u):
        return 1.0
    if re.search(r"'?000|thousand|k$", u):
        return 0.001
    return None


def period(p):
    p = re.sub(r"[\s'’-]", "", str(p or "").upper())
    p = re.sub(r"^Q([1-4])", r"\1Q", p)
    p = re.sub(r"^([1-4])Q(\d{2})$", r"\1QFY\2", p)
    p = re.sub(r"^(1H|9M|H1)", lambda m: {"H1": "1H"}.get(m.group(1), m.group(1)), p)
    p = re.sub(r"FY20(\d{2})", r"FY\1", p)
    return p if re.fullmatch(r"(?:[1-4]Q|1H|9M)?FY\d{2}|CY\d{2}", p) else ""


def facts(j, nums, tickers, kind):
    """one read -> {key: value}; only figures printed in the text are kept"""
    f = {}
    for c in (j or {}).get("companies") or []:
        t = re.sub(r"[^A-Z0-9]", "", str(c.get("ticker") or "").upper().replace("PSX", ""))
        if t not in tickers:
            continue
        r = str(c.get("rating") or "").lower()
        if r in ("buy", "hold", "sell"):
            f[f"{t}|rating"] = r
        tp = fnum(c.get("tp"))
        if tp and tp > 0 and printed(tp, nums):
            f[f"{t}|tp"] = tp
        for x in c.get("results") or []:
            p = period(x.get("period"))
            if not p or x.get("actual") is False or kind == "result preview":
                continue
            fac = mn_factor(x.get("unit"))
            b = str(x.get("basis") or "").lower()
            f[f"{t}|res|{p}|basis"] = "consolidated" if b.startswith("consol") else "unconsolidated" if b.startswith("uncon") else ""
            for k in AMT + PER:
                v = fnum(x.get(k))
                if v is None or not printed(v, nums):
                    continue
                if k in AMT:
                    if fac is None:
                        continue
                    v = round(v * fac, 3)
                f[f"{t}|res|{p}|{k}"] = v
        for x in c.get("forecasts") or []:
            y = period(x.get("year"))
            if not re.fullmatch(r"FY\d{2}", y):
                continue
            for k in PER:
                v = fnum(x.get(k))
                if v is not None and printed(v, nums):
                    f[f"{t}|fc|{y}|{k}"] = v
    for m in (j or {}).get("macro") or []:
        v = fnum(m.get("value"))
        n = re.sub(r"\s+", " ", str(m.get("name") or "")).strip()[:40]
        p = re.sub(r"\s+", "", str(m.get("period") or ""))[:12]
        if v is not None and n and p and printed(v, nums):
            f[f"macro|{re.sub('[^a-z]', '', n.lower())[:14]}|{re.sub('[^a-z0-9]', '', p.lower())}"] = [n, p, v]
    return f


def same(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return same(a[2], b[2])
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(0.006, abs(a) * 0.005)
    return a == b


def agree(fs):
    """keys where at least two reads give the same value -> (agreed, disputed keys)"""
    ok, bad = {}, set()
    keys = set().union(*[set(f) for f in fs]) if fs else set()
    for k in keys:
        vals = [f[k] for f in fs if k in f]
        hit = next((v for i, v in enumerate(vals) if any(same(v, w) for w in vals[i + 1:])), None)
        if hit is not None:
            ok[k] = hit
        elif len(vals) >= 2 and not k.endswith(("|basis", "|rating")):
            bad.add(k)
    return ok, bad


# ---------------------------------------------------------------- data
def jread(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d


def trim(text):
    """drop the disclaimer pages at the back; cap the length"""
    cut = re.search(r"(?im)^\s*(disclaimer|analyst certification|research dissemination|rating definitions?|"
                    r"valuation methodology|disclosure of interest)\b", text[len(text) // 3:])
    if cut:
        text = text[:len(text) // 3 + cut.start()]
    text = re.sub(r"[ \t]{3,}", "   ", text)
    return text[:40000]


def queue(items, have, n_readers, important):
    """reports still needing a read, most useful first"""
    pri = {"result review": 0, "company": 1, "note": 1, "result preview": 2, "sector": 2, "economy": 3, "strategy": 3}
    out = []
    for it in items:
        if it.get("kind") in SKIP_KINDS or not os.path.exists(os.path.join(TEXT, it["id"] + ".txt")):
            continue
        rec = have.get(it["id"]) or {}
        reads = rec.get("reads") or {}
        need = len(reads) < 2 or (rec.get("disputed") and len(reads) < 3)
        if not need or len(reads) >= n_readers:
            continue
        mine = any(t in important for t in it.get("t") or [])
        out.append((not mine, pri.get(it.get("kind"), 2), -int((it.get("d") or "0000-00-00").replace("-", "")), it))
    return [x[-1] for x in sorted(out, key=lambda x: x[:3])]


def read_one(it, readers, tickers, rec):
    text = trim(open(os.path.join(TEXT, it["id"] + ".txt"), errors="ignore").read())
    nums = numbers(text)
    reads = rec.setdefault("reads", {})
    prompt = PROMPT + f"Title: {it.get('title')}\nDate: {it.get('d')}\n\n{text}"

    fam = {r.name: r.family() for r in readers}

    def go(r):
        j = r.ask(prompt)
        if j is not None:
            reads[r.name] = {"at": date.today().isoformat(), "j": j}
    for _ in range(3):
        want = 2 - len(reads) if len(reads) < 2 else (1 if rec.get("disputed") and len(reads) < 3 else 0)
        free = [r for r in readers if not r.out and r.name not in reads]
        if want <= 0 or not free:
            break
        used = {fam.get(n) for n in reads}
        batch = []
        for _ in range(want):  # a different kind of model first; another model of the same kind only if none is left
            fresh = [r for r in free if r not in batch and r.family() not in used | {b.family() for b in batch}]
            r = (fresh or [r for r in free if r not in batch] or [None])[0]
            if r:
                batch.append(r)
        ts = [threading.Thread(target=go, args=(r,)) for r in batch]
        [t.start() for t in ts]
        [t.join() for t in ts]
        fs = [facts(v["j"], nums, tickers, it.get("kind")) for v in reads.values()]
        rec["disputed"] = sorted(agree(fs)[1])
        if len(reads) >= 2 and not rec["disputed"]:
            break
    return rec


def build(items, tickers, shares):
    """all saved reads -> the checked figures the website shows"""
    co, macro, rep, models = {}, {}, {}, set()
    n_facts = n_checked = 0
    by_id = {it["id"]: it for it in items}
    for name in sorted(os.listdir(RAW)) if os.path.isdir(RAW) else []:
        rec = jread(os.path.join(RAW, name), {})
        it = by_id.get(name[:-5])
        if not it or not rec.get("reads"):
            continue
        text = trim(open(os.path.join(TEXT, it["id"] + ".txt"), errors="ignore").read())
        nums = numbers(text)
        reads = list(rec["reads"].items())
        models.update(k for k, _ in reads)
        fs = [facts(v["j"], nums, tickers, it.get("kind")) for _, v in reads]
        ok, bad = agree(fs) if len(fs) >= 2 else ({}, set())
        n_facts += len(ok)
        first = reads[0][1]["j"] or {}
        tks = sorted({k.split("|")[0] for k in ok if not k.startswith("macro")} |
                     {re.sub(r"[^A-Z0-9]", "", str(c.get("ticker") or "").upper()) for c in first.get("companies") or []} & tickers)
        rep[it["id"]] = {"s": str(first.get("summary") or "")[:300], "r": len(reads), "c": len(ok), "t": tks}
        if ok:
            n_checked += 1
        base = {"d": it.get("d"), "id": it["id"], "src": it.get("src")}
        for t in tks:
            C = co.setdefault(t, {"tp": [], "res": {}, "fc": {}, "pts": []})
            if f"{t}|tp" in ok:
                C["tp"].append(dict(base, v=ok[f"{t}|tp"], r=ok.get(f"{t}|rating", "")))
            pers = {k.split("|")[2] for k in ok if k.startswith(f"{t}|res|")}
            for p in pers:
                row = {k: ok[f"{t}|res|{p}|{k}"] for k in AMT + PER if f"{t}|res|{p}|{k}" in ok}
                if not row:
                    continue
                row.update(base, p=p, b=ok.get(f"{t}|res|{p}|basis", ""))
                sh = shares.get(t)
                if sh and row.get("pat") and row.get("eps"):
                    implied = row["pat"] * 1e6 / row["eps"]
                    if not 0.85 <= implied / sh <= 1.15:
                        row["x"] = "EPS and profit imply a different share count: check basis or a share issue"
                old = C["res"].get(p)
                if not old or (row["d"] or "") > (old["d"] or ""):
                    C["res"][p] = row
            for y in {k.split("|")[2] for k in ok if k.startswith(f"{t}|fc|")}:
                row = {k: ok[f"{t}|fc|{y}|{k}"] for k in PER if f"{t}|fc|{y}|{k}" in ok}
                old = C["fc"].get(y)
                if row and (not old or (base["d"] or "") > (old["d"] or "")):
                    C["fc"][y] = dict(base, y=y, **row)
            pts = next((c.get("points") for c in first.get("companies") or []
                        if re.sub(r"[^A-Z0-9]", "", str(c.get("ticker") or "").upper()) == t), None)
            pts = [re.sub(r"\s+", " ", str(x)).strip()[:240] for x in (pts or []) if str(x).strip()][:4]
            if pts:
                C["pts"].append(dict(base, t=pts, title=it.get("title")))
        for k, v in ok.items():
            if k.startswith("macro|"):
                macro[k] = {"n": v[0], "p": v[1], "v": v[2], "d": it.get("d"), "id": it["id"]}
    for t, C in co.items():
        C["tp"] = sorted(C["tp"], key=lambda x: x["d"] or "", reverse=True)[:12]
        C["res"] = sorted(C["res"].values(), key=lambda x: x["d"] or "", reverse=True)[:16]
        C["fc"] = sorted(C["fc"].values(), key=lambda x: x["y"], reverse=True)[:6]
        C["pts"] = sorted(C["pts"], key=lambda x: x["d"] or "", reverse=True)[:6]
    macro = sorted(macro.values(), key=lambda x: x["d"] or "", reverse=True)[:120]
    return {"asOf": date.today().isoformat(),
            "src": "Broker and PACRA reports read by free AI models; a figure is kept only when two models read the same "
                   "number and it is printed in the report",
            "panel": sorted(models), "n": {"read": len(rep), "checked": n_checked, "facts": n_facts},
            "co": co, "macro": macro, "rep": rep}


def main():
    t0 = time.time()
    items = (jread(os.path.join(DB, "market", "research.json"), {}) or {}).get("items", [])
    scr = (jread(os.path.join(DB, "market", "screener.json"), {}) or {}).get("rows", [])
    tickers = {r["s"] for r in scr}
    shares = {r["s"]: r["mc"] / r["p"] for r in scr if r.get("mc") and r.get("p")}
    important = set(os.path.splitext(f)[0] for d in ("holdings", "reviews") if os.path.isdir(os.path.join(DB, d))
                    for f in os.listdir(os.path.join(DB, d)) if "_" not in f)
    os.makedirs(RAW, exist_ok=True)
    msg = ""
    if not REBUILD:
        readers = panel()
        if not readers:
            print("reader: no free AI key is set yet (GEMINI_API_KEY); nothing read")
            return 0
        have = {f[:-5]: jread(os.path.join(RAW, f), {}) for f in os.listdir(RAW)}
        todo = queue(items, have, len(readers), important)
        done = 0
        for it in todo[:MAXN]:
            if time.time() - t0 > MINUTES * 60 or all(r.out for r in readers):
                break
            rec = read_one(it, readers, tickers, have.get(it["id"]) or {"id": it["id"]})
            if rec.get("reads"):
                json.dump(rec, open(os.path.join(RAW, it["id"] + ".json"), "w"), separators=(",", ":"), ensure_ascii=False)
                done += 1
        spent = [r for r in readers if r.out]
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "minutes": round((time.time() - t0) / 60, 1),
                   "panel": [r.name for r in readers], "calls": CALLS[-400:]},
                  open(os.path.join(FILES, "reader_calls.json"), "w"), indent=0)
        msg = (f"read {done} reports, {max(0, len(todo) - done)} waiting; models used: "
               + ", ".join(f"{r.model} {r.n}" for r in readers if r.n)
               + (f"; resting until tomorrow: {', '.join(r.model + ' (' + r.err + ')' for r in spent)}" if spent else ""))
    out = build(items, tickers, shares)
    old = jread(os.path.join(DB, "market", "readings.json"))
    if old and out["n"]["facts"] < old.get("n", {}).get("facts", 0) * 0.8:
        print(f"reader stopped: checked figures fell from {old['n']['facts']} to {out['n']['facts']}; nothing saved. {msg}")
        return 2
    json.dump(out, open(os.path.join(DB, "market", "readings.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"done: {msg + '; ' if msg else ''}{out['n']['read']} reports read in all, {out['n']['facts']} checked figures "
          f"for {len(out['co'])} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
