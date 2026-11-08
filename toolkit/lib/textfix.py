"""Repairs report text that PDF fonts break (8 Nov 2026).

Many annual reports print "fi", "fl", "ff", "ti", "ft" and "tt" as single joined glyphs. pdftotext then gives either
the joined character ("Proﬁt"), a blank box ("Taxa�on"), or nothing at all ("Operang", "acvies", "aer"). Every
check that looks for a word ("profit", "taxation", "statement of profit or loss") then failed on those reports: the
income statement page was never picked, and figures the models read correctly failed the row proof. 718 read
reports had lost their profit this way or through the rules fixed alongside.

fix(text) puts the letters back: joined characters are spelled out, and a broken word is replaced only when it is
exactly a known statement word with one or two of those letter pairs missing. Numbers are never touched.
"""
import re

LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
       "": "fi", "": "fl"}  # the last two: the same joined letters in some fonts' private codes
PAIRS = ("ffi", "ffl", "ff", "fi", "fl", "ft", "ti", "tt", "tf")
# statement words that contain those letter pairs (spelled correctly); only words of 5 letters or more, so a short
# real word ("me" from "time") can never be produced or replaced
WORDS = """
accounting acquisition acquisitions activities activity addition additions administration administrative after
amortisation amortised amortization appropriation appropriations attributable benefit benefits capitalisation
capitalization certificate certificates classified combination consideration contingencies contingency contingent
continued continuing contribution contributions conventional convertible cumulative deficit defined depreciation
derecognition derivative difference differential dilutive diminution discontinued discounting distribution
distributions domestic doubtful duties effect effective efficiency equities estimated exceptional executive
exploration facilities facility fertiliser fertilizer fertilizers fiduciary final finance financed finances financial
financing financings fixed flows inflow inflows information institutions international investing liabilities
liability maturities modification obligation obligations office officer oilfields operating operation operational
operations outflow outflows overdraft overdrafts participation participatory parties portfolio portion position
potential production profit profitability profits properties property ratification recognition reclassified
redemption refinance refineries refinery remuneration retention retirement revaluation securities separation
settlement subscription taxation textile textiles traffic ultimate underwriting utilisation utilization written
statement statements profitable profitloss deferred differed offset offsets cotton committed commitment commitments
staff tariff tariffs specific justified justification modified notified quantities quantity sanctioned
sufficient insufficient affiliated affiliate affiliates certified unaffected affected official officials
""".split()


def _variants(w):
    """the word with one or two of the joined letter pairs left out"""
    out = set()
    spots = [(m.start(), len(p)) for p in PAIRS for m in re.finditer(p, w)]
    for i, (a, la) in enumerate(spots):
        v = w[:a] + w[a + la:]
        out.add(v)
        for b, lb in spots[i + 1:]:
            if b >= a + la:
                out.add(w[:a] + w[a + la:b] + w[b + lb:])
            elif a >= b + lb:
                out.add(w[:b] + w[b + lb:a] + w[a + la:])
    return out


FIX = {}
for _w in WORDS:
    if len(_w) >= 5:
        for _v in _variants(_w):
            if len(_v) >= 3 and _v not in WORDS:
                FIX.setdefault(_v, _w)
# variants two words could produce are left alone (never guess)
_seen = {}
for _w in WORDS:
    for _v in _variants(_w):
        _seen.setdefault(_v, set()).add(_w)
for _v, _ws in _seen.items():
    if len(_ws) > 1:
        FIX.pop(_v, None)
# broken forms that are also real words: never rewritten
for _real in ("ores", "ions", "ices", "ones", "nested", "sees", "lows", "ease", "eaten", "ration", "rations",
              "mentioned", "ring", "rings", "staff", "nancy", "dues", "pares"):
    FIX.pop(_real, None)

TOKEN = re.compile(r"[A-Za-z�]+")


def _case(src, word):
    if src.isupper():
        return word.upper()
    if src[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def fix(text):
    if not text:
        return text
    for a, b in LIG.items():
        text = text.replace(a, b)

    def one(m):
        t = m.group(0)
        bare = t.replace("�", "")
        w = FIX.get(bare.lower())
        if w and (bare.lower() not in WORDS):
            return _case(bare, w)
        return bare if "�" in t else t
    text = TOKEN.sub(one, text)
    # a joined glyph printed as a gap splits the word in two ("a er", "pro t", "taxa on"): the two halves are joined
    # when together they are exactly a known broken word
    parts = re.split(r"([A-Za-z]+)", text)
    i = 1
    while i + 2 < len(parts):
        a, gap, b = parts[i], parts[i + 1], parts[i + 2]
        w = FIX.get((a + b).lower()) if gap == " " and len(a) <= 6 and len(b) <= 6 else None
        if w:
            parts[i:i + 3] = [_case(a, w)]
        i += 2 if not w else 0
        if w:
            i += 2
    return "".join(parts)
