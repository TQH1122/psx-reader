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


print("== PSX briefing page")
jar = "/tmp/psxjar"
sh(["curl", "-s", "-m", "60", "-A", UA, "-c", jar, "-o", "/dev/null", "https://dps.psx.com.pk/announcements/companies"])
for data in ["type=C&symbol=&query=&count=50&offset=0&date_from=&date_to=&page=cbs", "type=C&symbol=OGDC&query=&count=50&offset=0&date_from=&date_to=&page=cbs"]:
    code, out, _ = sh(["curl", "-s", "-m", "60", "-A", UA, "-b", jar, "-X", "POST", "-H", "X-Requested-With: XMLHttpRequest",
                       "-H", "Referer: https://dps.psx.com.pk/announcements/companies", "-d", data, "https://dps.psx.com.pk/announcements"])
    rows = re.findall(r"<tr[\s\S]*?</tr>", out)
    print(data[:40], "bytes", len(out), "rows", len(rows), re.findall(r"data-total=\"(\d+)\"", out)[:1])
    for r in rows[:5]:
        print("  ", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", r))[:220], re.findall(r"(?:href|data-images)=\"([^\"]+)\"", r)[:3])
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
for model in [m for m in ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-omni-1.2-flash", "gemini-3.5-flash-lite"] if m in flash]:
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

print("done: probe")
