"""Drive access as the owner (9 Nov 2026).

Google refuses downloads of his link shared files to anyone but him ("Quota exceeded"), and his API key is refused
too. A small Google Apps Script in his own account (toolkit/drive_relay.gs in the tracker) puts a fresh one hour read
only Drive token into the private tracker every 30 minutes (data/files/drive_token.json). Robots fetch it with the
TRACKER_TOKEN key and download his files as him, which Google allows. The token is never printed.

    from gtoken import fetch_pdf
    ok = fetch_pdf(file_id, out_path)    # True when a PDF arrived through the owner token
"""
import json, os, subprocess, time

URL = "https://api.github.com/repos/TQH1122/psx-tracker/contents/data/files/drive_token.json"
_c = {"tok": None, "at": 0.0}


def token():
    if time.time() - _c["at"] < 300:
        return _c["tok"]
    tok = None
    gt = os.environ.get("TRACKER_TOKEN") or os.environ.get("GH_TOKEN")
    if gt:
        try:
            p = subprocess.run(["curl", "-s", "-m", "30", "-H", f"Authorization: Bearer {gt}", "-H",
                                "Accept: application/vnd.github.raw", URL], capture_output=True, text=True, timeout=45)
            d = json.loads(p.stdout or "{}")
            if d.get("token") and d.get("exp", 0) > time.time() + 120:
                tok = d["token"]
        except Exception:
            tok = None
    _c.update(tok=tok, at=time.time())
    return tok


def fetch_pdf(fid, out, timeout=600):
    tok = token()
    if not tok:
        return False
    try:
        p = subprocess.run(["curl", "-sL", "-m", str(timeout), "-o", out, "-w", "%{http_code}", "-H", f"Authorization: Bearer {tok}",
                            f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&acknowledgeAbuse=true"],
                           capture_output=True, text=True, timeout=timeout + 30)
        return p.stdout == "200" and os.path.isfile(out) and open(out, "rb").read(5) == b"%PDF-"
    except Exception:
        return False
