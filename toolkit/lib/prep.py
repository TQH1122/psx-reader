#!/usr/bin/env python3
"""prep.py PDF OUT.txt : find the pages that matter in a PSX annual report and write a compact bundle.

Pages picked (by keyword and number density):
  SUMMARY  multi year summary (six or ten years at a glance, key financial data, horizontal analysis)
  PL, BS, CF  income statement, balance sheet, cash flow; consolidated (C) and company only (S) when both exist
Scanned pages with no text layer are OCRed (only the candidate pages, found by OCR of every 4th page if needed).
Prints one json line: pages, text pages, has consolidated, picked pages.
"""
import json, re, subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textfix import fix  # joined letters in PDF fonts (8 Nov 2026)

pdf, out = sys.argv[1], sys.argv[2]

def run(a):
    return subprocess.run(a, capture_output=True, text=True, errors="ignore").stdout

def pages_text(mode="-layout"):
    t = run(["pdftotext", mode, pdf, "-"])
    return fix(t).split("\f")

TXT = pdf.lower().endswith(".txt")  # an old html report already turned into text pages (split by form feeds)
P = fix(open(pdf, errors="ignore").read()).split("\f") if TXT else pages_text()
n = len(P)
textp = sum(1 for p in P if len(p.strip()) > 200)
ocr = False
if not TXT and textp < max(3, n * 0.3):
    # scanned: OCR every page at low cost (150 dpi, page by page)
    ocr = True
    P = []
    tot = int(re.search(r"Pages:\s+(\d+)", run(["pdfinfo", pdf])).group(1))
    for i in range(1, tot + 1):
        img = f"/tmp/ocr_{os.getpid()}"
        subprocess.run(["pdftoppm", "-r", "150", "-gray", "-f", str(i), "-l", str(i), "-png", pdf, img], capture_output=True)
        f = [x for x in os.listdir("/tmp") if x.startswith(f"ocr_{os.getpid()}")]
        txt = ""
        for x in f:
            txt = run(["tesseract", "/tmp/" + x, "-", "--psm", "6"])
            os.remove("/tmp/" + x)
        P.append(fix(txt))
    n = len(P)

YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
NUM = re.compile(r"\(?-?\d[\d,]{2,}(?:\.\d+)?\)?")

def numdens(p):
    return len(NUM.findall(p))

def years(p):
    return len(set(YEAR.findall(p)))

def score(p, kind):
    l = re.sub(r"\s+", " ", p.lower())
    nd = numdens(p)
    if nd < 25:
        return 0
    if "contents" in l[:400] and nd < 60:
        return 0
    s = 0
    if kind == "SUM":
        if "notes to the" in l[:700]:
            return 0
        kw = ["six year", "ten year", "years at a glance", "year at a glance", "key financial", "financial highlights", "horizontal analysis",
              "key operating and financial", "financial summary", "performance at a glance", "years summary", "year summary", "financial data",
              "six years", "ten years", "historical"]
        if not any(k in l for k in kw):
            return 0
        s = years(p) * 3 + sum(k in l for k in ["sales", "revenue", "turnover", "profit after", "net profit", "earnings per share", "dividend",
                                                  "total assets", "shareholders", "equity"]) * 4
        if years(p) < 4:
            return 0
        if "vertical analysis" in l and "horizontal" not in l:
            s -= 5
    elif kind == "PL":
        if not re.search(r"statement of profit or loss|profit and loss account|income statement|statement of income", l):
            return 0
        # "earnings per ordinary share", "basic and diluted earnings per share", "loss per share" (8 Nov 2026)
        if not re.search(r"per (ordinary |each |one )?(share|certificate|unit)", l):
            return 0
        s = 10 + sum(k in l for k in ["cost of sales", "gross profit", "taxation", "finance cost", "profit before", "net revenue", "sales", "attributable"]) * 3
        if "comprehensive income" in l and "cost of sales" not in l and "gross" not in l and "markup" not in l and "interest earned" not in l:
            s -= 6
    elif kind == "BS":
        if not re.search(r"statement of financial position|balance sheet", l):
            return 0
        # a running heading can name the balance sheet on every statement page: another statement's page is not it
        if re.search(r"changes in equity|comprehensive income|cash flow statement|statement of cash flows?|profit and loss account|statement of profit or loss", l[:700]) \
                and not re.search(r"total assets|net assets|total equity and liabilities|total liabilities", l):
            return 0
        s = 10 + sum(k in l for k in ["share capital", "total assets", "non-current assets", "current assets", "current liabilities", "equity", "reserves",
                                         "trade debts", "property, plant", "advances", "deposits"]) * 3
    elif kind == "CF":
        if "cash flow" not in l or "operating activities" not in l:
            return 0
        s = 10 + sum(k in l for k in ["investing activities", "financing activities", "cash and cash equivalents", "dividend", "fixed capital",
                                         "property, plant"]) * 3
    if "notes to" in l[:600] or "summary of significant" in l:
        s -= 8
    if kind in ("PL", "BS", "CF"):
        head = l[:1500]
        if re.search(r"horizontal analysis|vertical analysis|at a glance|six years|ten years|years summary|summary of|key financial|financial highlights|value added|wealth", l):
            return 0
        if years(p) >= 4:
            s -= 15
        if re.search(r"\bnotes?\b", head):
            s += 8
        if re.search(r"takaful fund|participants|window takaful|operator.s fund|statutory fund|provident fund|gratuity fund", head):
            s -= 20
        title = re.search(r"statement of profit or loss|profit and loss account|income statement|statement of income|statement of financial position|balance sheet|cash flow", l[:900])
        if title:
            s += 6
    return s

def ocr_page(i):
    img = f"/tmp/ocrp_{os.getpid()}_{i}"
    subprocess.run(["pdftoppm", "-r", "200", "-gray", "-f", str(i + 1), "-l", str(i + 1), "-png", pdf, img], capture_output=True)
    txt = ""
    for x in [x for x in os.listdir("/tmp") if x.startswith(os.path.basename(img))]:
        txt = run(["tesseract", "/tmp/" + x, "-", "--psm", "6"])
        os.remove("/tmp/" + x)
    return txt

# statement pages whose numbers are not in the text layer (drawn as curves): OCR them
TITLE = re.compile(r"statement of profit or loss|profit and loss account|statement of financial position|balance sheet|statement of cash flows?|cash flow statement")
ocrd = []
if not ocr and not TXT:
    for i, p in enumerate(P):
        head = re.sub(r"\s+", " ", p.lower())[:300]
        if TITLE.search(head) and numdens(p) < 15 and "contents" not in head and "notes to" not in head and len(ocrd) < 12:
            t = fix(ocr_page(i))
            if numdens(t) >= 25:
                P[i] = t
                ocrd.append(i + 1)

def is_cons(p):
    l = re.sub(r"\s+", " ", p.lower())
    h = l[:250]
    # some PDFs print the company only running header over the consolidated pages: count the titles in the heading
    hh = l[:400]
    c = len(re.findall(r"(?<!un)(?<!un-)consolidated (statement|profit|balance|cash)", hh))
    u = len(re.findall(r"(unconsolidated|un-consolidated|separate|standalone|stand alone) (statement|profit|balance|cash)", hh))
    if c != u and (c or u):
        return c > u
    if c and c == u:  # still tied: the whole page decides
        c2 = len(re.findall(r"(?<!un)(?<!un-)consolidated", l))
        u2 = len(re.findall(r"unconsolidated|un-consolidated", l))
        if c2 != u2:
            return c2 > u2
    if re.search(r"unconsolidated|un-consolidated|separate|standalone|stand alone", h):
        return False
    if "consolidated" in h:
        return True
    return "consolidated" in l and "unconsolidated" not in l.replace("un-consolidated", "unconsolidated")

has_cons = sum(1 for p in P if is_cons(p) and re.search(r"statement of financial position|balance sheet", p.lower())) > 0

picked = {}
for kind in ["SUM", "PL", "BS", "CF"]:
    sc = [(score(p, kind), i) for i, p in enumerate(P)]
    if kind == "SUM":
        best = sorted([x for x in sc if x[0] > 0], reverse=True)[:2]
        if best:
            picked["SUM"] = sorted(i for _, i in best)
        continue
    for basis in (["C", "S"] if has_cons else ["S"]):
        cand = [x for x in sc if x[0] > 0 and (is_cons(P[x[1]]) == (basis == "C"))]
        if cand:
            picked[kind + basis] = [max(cand)[1]]
            # statements often run over two pages (bank PL, BS with equity and liabilities on the next page)
            i = picked[kind + basis][0]
            li = P[i].lower()
            if kind == "BS":
                has_assets = bool(re.search(r"total assets|non-current assets|non current assets", li))
                has_eq = bool(re.search(r"share capital|current liabilities", li))
                if not (has_assets and has_eq):
                    need = r"share capital|current liabilities" if has_assets else r"total assets|non-current assets|non current assets"
                    for j in (i - 1, i + 1):
                        if 0 <= j < n and numdens(P[j]) > 15 and re.search(need, P[j].lower()) and "notes to" not in P[j].lower()[:500] \
                                and not re.search(r"statement of profit|profit and loss|cash flow", P[j].lower()[:600]):
                            picked[kind + basis].append(j)
                            break
            if kind == "CF" and "financing" not in li and i + 1 < n and "financing" in P[i + 1].lower():
                picked[kind + basis].append(i + 1)
            if kind == "PL" and not re.search(r"per share", li) and i + 1 < n and re.search(r"per share", P[i + 1].lower()):
                picked[kind + basis].append(i + 1)
            picked[kind + basis] = sorted(set(picked[kind + basis]))

def compact(p):
    lines = []
    for ln in p.splitlines():
        if not ln.strip():
            continue
        ln = re.sub(r" {3,}", "   ", ln.rstrip())
        lines.append(ln)
    return "\n".join(lines)

head = " ".join(compact(P[i])[:300] for i in range(min(2, n)))
with open(out, "w") as f:
    f.write(f"FILE {os.path.basename(pdf)} pages={n} ocr={ocr} consolidated_statements={has_cons}" + (f" ocr_pages={ocrd} (numbers read by OCR: double check digits)" if ocrd else "") + "\n")
    for k, idx in picked.items():
        for i in idx:
            f.write(f"\n===== {k} page {i + 1} =====\n")
            f.write(compact(P[i]) + "\n")
print(json.dumps({"ocr_pages": ocrd, "pages": n, "textpages": textp, "ocr": ocr, "cons": has_cons, "picked": {k: [i + 1 for i in v] for k, v in picked.items()},
                  "chars": os.path.getsize(out)}))
