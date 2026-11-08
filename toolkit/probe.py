#!/usr/bin/env python3
"""One off probe (8 Nov 2026): can the free Google key list every file in his link shared PSX Library folder? The
folder view the Drive indexer reads stops at about 5,500 files, so his newest uploads were never seen."""
import json, os, urllib.parse, urllib.request

FOLDER = "1KZBgru0osNuRHUyypDF1V8Pxhf7Db02F"
key = os.environ.get("DRIVE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
q = urllib.parse.quote(f"'{FOLDER}' in parents and trashed = false")
url = f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize=1000&fields=nextPageToken,files(id,name,mimeType,createdTime)&key={key}"
try:
    d = json.load(urllib.request.urlopen(url, timeout=60))
    print("drive api ok:", len(d.get("files", [])), "files on page 1; more pages:", bool(d.get("nextPageToken")))
except urllib.error.HTTPError as e:
    print("drive api error", e.code, e.read().decode()[:600])
