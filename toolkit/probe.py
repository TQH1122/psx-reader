#!/usr/bin/env python3
"""One off probe (11 Nov 2026), round 2: PSX notice search call, Gemini's own YouTube video reading (Google's official
route, free daily allowance), and briefing videos on the known channels. Prints only short status lines."""
import json, os, re, subprocess, sys, urllib.request

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36"
VID = "0Mt95KPrw_I"


def sh(cmd, t=180):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


print("== PSX script calls")
code, js, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "https://dps.psx.com.pk/static/script.js?v=1.81"])
print("js bytes", len(js))
for m in re.finditer(r"announce", js, re.I):
    print("  ..", js[max(0, m.start() - 250):m.start() + 350].replace("\n", " ")[:600])
code, page, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "https://dps.psx.com.pk/announcements/companies"])
for m in re.finditer(r"<form[\s\S]{0,1500}?</form>", page):
    if "keyword" in m.group(0):
        print("form", re.sub(r"\s+", " ", m.group(0))[:900])

print("== Gemini reads a YouTube video")
key = os.environ.get("GEMINI_API_KEY", "")
try:
    ms = json.load(urllib.request.urlopen("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + key, timeout=60))
    names = [m["name"].split("/")[1] for m in ms.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and "image" not in n and "tts" not in n and "live" not in n]
    print("flash models", flash[:12])
except Exception as e:
    flash = ["gemini-2.5-flash"]
    print("model list failed", str(e)[:120])
for model in [m for m in ["gemini-2.5-flash", "gemini-flash-latest"] if m in flash][:2] or flash[:1]:
    body = {"contents": [{"parts": [
        {"file_data": {"file_uri": f"https://www.youtube.com/watch?v={VID}"}, "video_metadata": {"start_offset": "600s", "end_offset": "780s"}},
        {"text": "Transcribe every spoken word of this clip exactly, in the language spoken, with [mm:ss] times. Then write ENGLISH: and a faithful English translation."}]}],
        "generationConfig": {"temperature": 0, "mediaResolution": "MEDIA_RESOLUTION_LOW"}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        j = json.load(urllib.request.urlopen(req, timeout=300))
        t = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        print(model, "ok, chars", len(t), "usage", j.get("usageMetadata", {}).get("promptTokenCount"))
        print("  ", re.sub(r"\s+", " ", t)[:500])
    except urllib.error.HTTPError as e:
        print(model, "http", e.code, e.read().decode()[:300])
    except Exception as e:
        print(model, "failed", str(e)[:200])

print("== briefing videos on known channels")
sh([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"], 300)
for q in ["Akseer Research", "Akseer corporate briefing session", "KTrade corporate briefing session", "Optimus capital corporate briefing session"]:
    code, out, err = sh(["yt-dlp", "--flat-playlist", "-J", f"ytsearch10:{q}"], 120)
    try:
        ents = json.loads(out).get("entries") or []
    except Exception:
        ents = []
    print("search", q, sorted({(e.get("channel"), e.get("channel_id")) for e in ents}))
for ch in ["UCr6RYtCwd1rsu6UyQZsPbNA", "UC1ilKViIpulidQm5UL6mGTQ", "UCtAosckRYJ95N6OPBH_ZOMg", "UC8iqMHYlaUCM3TRBvAcP1bA", "UCfXgbAAHzN_VxGJg2agyAYA"]:
    code, out, err = sh(["yt-dlp", "--flat-playlist", "-J", f"https://www.youtube.com/channel/{ch}/videos"], 240)
    try:
        es = json.loads(out).get("entries") or []
        cbs = [e for e in es if re.search(r"brief|cbs|analyst", e.get("title") or "", re.I)]
        print("channel", ch, "videos", len(es), "briefing titles", len(cbs), [e.get("title")[:60] for e in cbs[:3]])
    except Exception:
        print("channel", ch, "failed", (err.strip().splitlines() or [""])[-1][:150])
print("done: probe")
