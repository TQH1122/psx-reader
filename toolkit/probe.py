#!/usr/bin/env python3
"""One off probe (11 Nov 2026): can the robots reach what the corporate briefing session collector needs?
PSX notice search, YouTube listing, captions, audio, and free Whisper on Groq. Prints only short status lines."""
import glob, json, os, re, subprocess, sys, tempfile

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36"


def sh(cmd, t=180):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def strip(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


print("== PSX notice search")
code, page, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "https://dps.psx.com.pk/announcements/companies"])
print("page bytes", len(page))
for m in re.finditer(r"<script[^>]*src=\"([^\"]+)\"", page):
    print("script", m.group(1))
for m in re.finditer(r"(?:\$\.(?:post|ajax|get)|fetch|axios)\s*\([^;]{0,300}", page):
    print("call", m.group(0)[:300])
for m in re.finditer(r"<(?:input|select)[^>]*name=\"([^\"]+)\"", page):
    print("field", m.group(1))
for data in ["type=C&symbol=&query=briefing&count=20&offset=0&date_from=&date_to=&page=annc",
             "type=C&symbol=&query=corporate+briefing&count=20&offset=0&date_from=2018-01-01&date_to=2026-11-11&page=annc"]:
    code, out, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "-X", "POST", "-H", "X-Requested-With: XMLHttpRequest",
                       "-d", data, "https://dps.psx.com.pk/announcements"])
    rows = re.findall(r"<tr[\s\S]*?</tr>", out)
    print("post", data[:60], "bytes", len(out), "rows", len(rows))
    for r in rows[:4]:
        print("  ", strip(r)[:200], re.findall(r"href=\"([^\"]+)\"", r)[:2])
    if not rows:
        print("  head:", strip(out)[:300])

print("== YouTube")
sh([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"], 300)
first = None
for q in ["Alpha Capital corporate briefing session", "Akseer Research corporate briefing session",
          "corporate briefing session PSX"]:
    code, out, err = sh(["yt-dlp", "--flat-playlist", "-J", f"ytsearch8:{q}"], 120)
    try:
        ents = json.loads(out).get("entries") or []
    except Exception:
        ents = []
        print("search failed:", err.strip().splitlines()[-1:] if err else "")
    print("search", q, len(ents))
    for e in ents[:8]:
        print("  ", e.get("id"), e.get("channel"), e.get("channel_id"), e.get("duration"), (e.get("title") or "")[:70])
        first = first or (e.get("id") if (e.get("duration") or 0) > 600 else None)
for ch in ["https://www.youtube.com/@AlphaCapitalResearch/videos", "https://www.youtube.com/@AkseerResearch/videos",
           "https://www.youtube.com/@alphacapital/videos", "https://www.youtube.com/@akseerresearch2486/videos"]:
    code, out, err = sh(["yt-dlp", "--flat-playlist", "-J", "--playlist-end", "5", ch], 120)
    try:
        j = json.loads(out)
        print("channel", ch, j.get("channel"), j.get("channel_id"), len(j.get("entries") or []))
    except Exception:
        print("channel", ch, "failed", (err.strip().splitlines() or [""])[-1][:150])

if first:
    d = tempfile.mkdtemp()
    print("== captions for", first)
    code, out, err = sh(["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,ur.*,en,ur",
                         "--sub-format", "vtt", "-o", f"{d}/%(id)s.%(ext)s", f"https://www.youtube.com/watch?v={first}"], 120)
    print("code", code, "files", [os.path.basename(p) for p in glob.glob(d + "/*")], (err.strip().splitlines() or [""])[-1][:200])
    print("== audio")
    code, out, err = sh(["yt-dlp", "-f", "bestaudio", "--download-sections", "*300-420", "-x", "--audio-format", "mp3",
                         "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 32k", "-o", f"{d}/a.%(ext)s",
                         f"https://www.youtube.com/watch?v={first}"], 300)
    aud = glob.glob(d + "/a.*")
    print("code", code, "files", [(os.path.basename(p), os.path.getsize(p)) for p in aud], (err.strip().splitlines() or [""])[-1][:200])
    key = os.environ.get("GROQ_API_KEY")
    if aud and key:
        print("== Groq Whisper")
        for ep in ["transcriptions", "translations"]:
            code, out, err = sh(["curl", "-s", "-m", "120", "https://api.groq.com/openai/v1/audio/" + ep,
                                 "-H", "Authorization: Bearer " + key, "-F", "model=whisper-large-v3",
                                 "-F", "response_format=verbose_json", "-F", "file=@" + aud[0]], 150)
            try:
                j = json.loads(out)
                print(ep, "language", j.get("language"), "text", (j.get("text") or str(j.get("error")))[:300])
            except Exception:
                print(ep, "failed", out[:200])

print("== Invidious and Piped fallbacks")
for u in ["https://inv.nadeko.net/api/v1/stats", "https://yewtu.be/api/v1/stats", "https://pipedapi.kavin.rocks/healthcheck",
          "https://api.piped.private.coffee/healthcheck"]:
    code, out, _ = sh(["curl", "-s", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", u])
    print(u, out)
print("done: probe")
