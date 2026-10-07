"""Publish a story or article written in Claude Code.

    python pipeline/publish_post.py <draft_dir>

<draft_dir> holds post.md (grammar-checked body) and post_meta.yml:
    topic: <exact line from topics.txt this post was written from>
    kind: story | article
    title: ...
    description: ...
    tags: "Family, Stories"
    sources:            # articles only: [title, url] pairs actually consulted
      - ["EPA composting guide", "https://www.epa.gov/..."]
    image_prompt: ...   # optional; otherwise built from title and description
    facebook_caption: | # required: Facebook caption (no spoilers, ends "Full story in the comments 👇")
      ...
    facebook_comment: "👉 Read the full story here: {link}"   # optional; {link} = post URL
Removes the topic from topics.txt, logs it in pipeline/published.txt, makes the image and
builds the post's Facebook kit in social/<slug>/ (image, link, image prompt, caption, comment).
"""

import datetime as dt
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate as g  # noqa: E402
import images  # noqa: E402
import social  # noqa: E402

draft = Path(sys.argv[1])
info = yaml.safe_load((draft / "post_meta.yml").read_text(encoding="utf-8"))
if not (info.get("facebook_caption") or "").strip():
    raise SystemExit("post_meta.yml needs a facebook_caption; every post gets a Facebook kit.")
body = (draft / "post.md").read_text(encoding="utf-8").strip()
kind = info.get("kind", "article")

meta = {"title": info["title"], "description": info["description"], "tags": info.get("tags", "")}
extra = {"image_prompt": info["image_prompt"]} if info.get("image_prompt") else None
post = g.save_post(meta, body, [tuple(s) for s in info.get("sources", [])],
                   fiction=(kind == "story"), extra=extra)

topic = info.get("topic", "").strip()
lines = g.TOPICS.read_text(encoding="utf-8").splitlines()
if topic and topic in (l.strip() for l in lines):
    lines.remove(next(l for l in lines if l.strip() == topic))
    g.TOPICS.write_text("\n".join(lines) + "\n", encoding="utf-8")
with g.DONE.open("a", encoding="utf-8") as f:
    f.write(f"{dt.date.today()}\t{topic or info['title']}\t{post.name}\n")
print("saved", post.name)

images.main()

slug = post.stem[11:]
default_comment = ("👉 Read the full guide here: {link}" if kind == "article"
                   else "👉 Read the full story here: {link}")
social.add_caption(slug, info["facebook_caption"], info.get("facebook_comment") or default_comment)
social.main()
social.require_kit(slug)
print(f"Facebook kit: social/{slug}/facebook.md")
