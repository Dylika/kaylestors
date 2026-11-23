"""Make and post one Facebook Reel per Page per day from this site's stories.

    python pipeline/reels.py            # dry run: renders reels into reels_out/, posts nothing
    python pipeline/reels.py --live     # renders AND posts through Facebook's Graph API

Each story is used on ONE Page only (pipeline/reels_log.json), so Pages never carry identical
content. Reels show the opening of the story with a "Fictional story" label; the caption is the
story's existing Facebook caption and the first comment links to the full story on the site.
A Page that fails (blocked, bad token, upload error) is skipped and reported; nothing is retried.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402
import social  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "pipeline" / "reels.yml").read_text(encoding="utf-8"))
LOG = ROOT / "pipeline" / "reels_log.json"
OUT = ROOT / "reels_out"
GRAPH = "https://graph.facebook.com/" + os.environ.get("GRAPH_VERSION", "v21.0")


def load_log():
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {"posted": []}


def story_pool(log):
    """Standalone fiction posts that no Page has used yet, newest first."""
    used = {p["slug"] for p in log["posted"]} | set(log.get("reserved", []))
    pool = []
    for path in sorted(images.POSTS.glob("*.md"), reverse=True):
        meta = images.front_matter(path)
        if meta.get("fiction") and not meta.get("series") and path.stem[11:] not in used:
            pool.append((path, meta))
    return pool


def plain_text(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    body = re.sub(r"^\s*---\s*$", "", body, flags=re.M)         # scene breaks
    body = re.sub(r"[*_`#>]", "", body)
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def reel_script(path, max_words):
    """Opening of the story, cut at a sentence end, so the reel stops on the tension."""
    paras, out, count = plain_text(path), [], 0
    for para in paras:
        words = para.split()
        if count + len(words) <= max_words:
            out.append(para)
            count += len(words)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        part = ""
        for s in sentences:
            if count + len((part + " " + s).split()) > max_words and (out or part):
                break
            part = (part + " " + s).strip()
        if part:
            out.append(part)
        break
    hook = re.split(r"(?<=[.!?])\s+", paras[0])[0]
    return hook[:150], "\n\n".join(out)


def render(page, slug, path, meta, theme, out_dir):
    hook, story = reel_script(path, CFG["max_words"])
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"title": meta["title"], "hook": hook, "narrator": page["name"],
           "label": CFG["label"], "logo": "", "story": story}
    (out_dir / "story.json").write_text(json.dumps(cfg), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "pipeline" / "reel_render.py"),
                    str(out_dir / "story.json"), str(out_dir), "--theme", theme], check=True)
    return out_dir / "reel.mp4"


def caption_and_comment(slug, path):
    captions = yaml.safe_load(social.CAPTIONS.read_text(encoding="utf-8")) or {}
    c = captions.get(slug, {})
    caption = (c.get("caption") or "").strip()
    if not caption:
        raise RuntimeError(f"no Facebook caption for {slug}; add one to social/captions.yml")
    comment = (c.get("comment") or "👉 Read the full story here: {link}").strip()
    return caption + "\n\n#fictionalstory", comment.replace("{link}", social.post_url(path))


def check(r, what):
    data = r.json() if r.content else {}
    if not r.ok or "error" in data:
        raise RuntimeError(f"{what}: HTTP {r.status_code} {data.get('error', {}).get('message', r.text[:200])}")
    return data


def post_reel(creds, video, caption, comment):
    page_id, token = creds["page_id"], creds["token"]
    start = check(requests.post(f"{GRAPH}/{page_id}/video_reels",
                                data={"upload_phase": "start", "access_token": token}, timeout=60), "start")
    video_id, upload_url = start["video_id"], start["upload_url"]
    data = video.read_bytes()
    check(requests.post(upload_url, data=data, timeout=600, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}), "upload")
    check(requests.post(f"{GRAPH}/{page_id}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": caption, "access_token": token}), "publish")
    for _ in range(20):                      # processing usually takes under a minute
        st = check(requests.get(f"{GRAPH}/{video_id}", timeout=60,
                                params={"fields": "status", "access_token": token}), "status")
        phase = st.get("status", {}).get("video_status")
        if phase == "ready":
            break
        if phase == "error":
            raise RuntimeError(f"Facebook rejected the video: {st['status']}")
        time.sleep(15)
    try:
        check(requests.post(f"{GRAPH}/{video_id}/comments", timeout=60,
                            data={"message": comment, "access_token": token}), "comment")
    except RuntimeError as e:                # the reel is already live; just report it
        print(f"  comment failed: {e}")
    return video_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="post to Facebook (default: dry run)")
    ap.add_argument("--only", default=os.environ.get("REELS_ONLY", ""),
                    help="comma-separated Page keys to build reels for (default: all Pages)")
    args = ap.parse_args()

    creds = json.loads(os.environ.get("REEL_PAGES_JSON") or "{}")
    if args.live and not creds:
        raise SystemExit("REEL_PAGES_JSON is empty; nothing to post to.")

    log, report = load_log(), []
    pool = story_pool(log)
    OUT.mkdir(exist_ok=True)
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    for i, page in enumerate(CFG["pages"]):
        key = page["key"]
        if only and key not in only:
            continue
        if args.live and key not in creds:
            report.append((page["name"], "skipped", "no credentials in REEL_PAGES_JSON"))
            continue
        for _ in range(CFG.get("per_page_per_day", 1)):
            if not pool:
                report.append((page["name"], "skipped", "no unused stories left; write more"))
                break
            path, meta = pool.pop(0)
            slug = path.stem[11:]
            theme = (["cream", "maroon"][i % 2] if CFG.get("alternate_themes") else CFG.get("theme", "cream"))
            try:
                video = render(page, slug, path, meta, theme, OUT / key / slug)
                caption, comment = caption_and_comment(slug, path)
                if args.live:
                    vid = post_reel(creds[key], video, caption, comment)
                    log["posted"].append({"page": key, "slug": slug, "video_id": vid,
                                          "date": time.strftime("%Y-%m-%d")})
                    report.append((page["name"], "posted", f"{slug} -> video {vid}"))
                else:
                    report.append((page["name"], "dry-run", f"{slug} -> {video}"))
            except Exception as e:                       # one bad Page must not stop the others
                pool.insert(0, (path, meta))             # story stays unused for the next Page
                report.append((page["name"], "FAILED", f"{slug}: {e}"))
    if args.live:
        LOG.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    print("\n".join(f"{n:<24}{s:<10}{d}" for n, s, d in report))
    (OUT / "report.txt").write_text("\n".join(f"{n}\t{s}\t{d}" for n, s, d in report) + "\n", encoding="utf-8")
    if any(s == "FAILED" for _, s, _ in report):
        sys.exit(1)


if __name__ == "__main__":
    main()
