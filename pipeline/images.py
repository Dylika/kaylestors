"""Give every post a featured image.

    python pipeline/images.py          # create images for all posts that don't have one yet

For each post in _posts/ without images/<post-slug>.jpg, generate one with Pollinations
(free, no key) from the post's `image_prompt` (series episodes) or, failing that, from its
title and description. Set POLLINATIONS_TOKEN to use a free Pollinations account (no watermark).
"""

import os
import re
import sys
import time
import urllib.parse
import zlib
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "_posts"
IMAGES = ROOT / "images"
API = "https://image.pollinations.ai/prompt/"


def front_matter(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    return yaml.safe_load(m.group(1)) if m else {}


def prompt_for(meta):
    if meta.get("image_prompt"):
        return meta["image_prompt"]
    return (f"Realistic editorial photograph illustrating this story: {meta.get('title', '')}. "
            f"{meta.get('description', '')} Emotional, natural light, shallow depth of field. "
            "No text or logos.")


def generate(prompt, dest, seed):
    params = {"width": 1280, "height": 720, "model": "flux", "seed": seed, "nologo": "true"}
    headers = {}
    if os.environ.get("POLLINATIONS_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['POLLINATIONS_TOKEN']}"
    url = API + urllib.parse.quote(prompt[:1500])
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=180)
            if r.ok and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 5000:
                dest.write_bytes(r.content)
                return True
            print(f"  attempt {attempt + 1}: HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  attempt {attempt + 1}: {e}")
        time.sleep(30 * (attempt + 1))  # free tier rate-limits bursts (HTTP 402/429)
    return False


def main():
    IMAGES.mkdir(exist_ok=True)
    made = failed = 0
    for post in sorted(POSTS.glob("*.md")):
        slug = post.stem[11:]  # drop the YYYY-MM-DD- prefix, matching Jekyll's page.slug
        if any((IMAGES / f"{slug}{ext}").exists() for ext in (".jpg", ".png", ".webp")):
            continue
        meta = front_matter(post)
        if meta.get("image"):
            continue
        print(f"Image for: {meta.get('title', slug)}")
        if generate(prompt_for(meta), IMAGES / f"{slug}.jpg", seed=zlib.crc32(slug.encode()) % 100000):
            made += 1
            time.sleep(15)  # stay under the free tier rate limit
        else:
            failed += 1
            print("  failed; the post stays imageless and will be retried next run", file=sys.stderr)
    print(f"Images: {made} created, {failed} failed")


if __name__ == "__main__":
    main()
