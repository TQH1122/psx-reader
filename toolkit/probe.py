#!/usr/bin/env python3
"""One off probe (8 Nov 2026): can the robots list his PSX Library Drive folder by themselves (no Claude)?
Prints only counts, since this repository's logs are public."""
import json, os, re, subprocess, urllib.parse
FOLDER = "1KZBgru0osNuRHUyypDF1V8Pxhf7Db02F"


def get(url):
    p = subprocess.run(["curl", "-sL", "-m", "90", "-w", "\n%{http_code}", url], capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    return code, body


code, body = get(f"https://drive.google.com/embeddedfolderview?id={FOLDER}")
ids = set(re.findall(r"/file/d/([A-Za-z0-9_-]{20,})", body))
print(f"embedded view: http {code}, {len(ids)} files, {len(body)} bytes")

key = os.environ.get("GEMINI_API_KEY", "")
q = urllib.parse.quote(f"'{FOLDER}' in parents and trashed=false")
code, body = get(f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize=1000&fields=nextPageToken,files(id,name)&key={key}")
try:
    d = json.loads(body)
    print(f"drive api with the AI Studio key: http {code}, {len(d.get('files', []))} files, more pages: {bool(d.get('nextPageToken'))}, "
          f"error: {(d.get('error') or {}).get('message', '')[:160]}")
except Exception:
    print(f"drive api: http {code}, not json")
