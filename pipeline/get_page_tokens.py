"""Collect Facebook Page tokens for the daily reels (run by YOU, in your own Terminal).

    python3 pipeline/get_page_tokens.py

For each Facebook profile that manages your Pages, in turn:
  1. Open https://developers.facebook.com/tools/explorer/ (logged in as that profile), pick your
     Meta app, click "Generate Access Token" with permissions pages_show_list,
     pages_manage_posts, pages_read_engagement, pages_manage_engagement. Copy the token.
  2. Run this script and paste the token (and your app ID + secret) when asked.
It swaps the token for a long-lived one, lists that profile's Pages, and saves the ones named
in pipeline/reels.yml to ~/reel_pages.json (outside the repo). Run once per profile, then:

    gh secret set REEL_PAGES_JSON -R Dylika/kaylestors < ~/reel_pages.json

Nothing is sent anywhere except to graph.facebook.com. Tokens are never printed.
"""

import getpass
import json
import re
from pathlib import Path

import requests
import yaml

GRAPH = "https://graph.facebook.com/v21.0"
OUT = Path.home() / "reel_pages.json"
CFG = yaml.safe_load((Path(__file__).resolve().parent / "reels.yml").read_text(encoding="utf-8"))
WANT = {re.sub(r"\W", "", p["name"]).lower(): p["key"] for p in CFG["pages"]}


def get(path, **params):
    r = requests.get(f"{GRAPH}/{path}", params=params, timeout=60)
    data = r.json()
    if not r.ok or "error" in data:
        raise SystemExit(f"Facebook said: {data.get('error', {}).get('message', r.text[:200])}")
    return data


def main():
    app_id = input("App ID: ").strip()
    secret = getpass.getpass("App secret (hidden): ").strip()
    short = getpass.getpass("Access token from Graph API Explorer (hidden): ").strip()
    long = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
               client_secret=secret, fb_exchange_token=short)["access_token"]
    pages = get("me/accounts", access_token=long, limit=100)["data"]   # Page tokens from a long-lived user token don't expire
    saved = json.loads(OUT.read_text()) if OUT.exists() else {}
    found = []
    for p in pages:
        key = WANT.get(re.sub(r"\W", "", p["name"]).lower())
        if key:
            saved[key] = {"page_id": p["id"], "token": p["access_token"]}
            found.append(p["name"])
    OUT.write_text(json.dumps(saved), encoding="utf-8")
    OUT.chmod(0o600)
    print("Saved:", ", ".join(found) or "none of the Pages in reels.yml")
    print("Still missing:", ", ".join(k for k in WANT.values() if k not in saved) or "nothing")


if __name__ == "__main__":
    main()
