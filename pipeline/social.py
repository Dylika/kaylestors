"""Build a Facebook kit for every post.

    python pipeline/social.py

Creates social/<post-slug>/ with:
    image.jpg     the post's featured image, ready to upload
    facebook.md   post link, image prompt, Facebook caption, and the comment text (with link)
Captions and comments come from social/captions.yml (keyed by post slug); posts without an
entry get a TODO placeholder. social/index.md lists every post and whether its kit is complete.
"""

import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOCIAL = ROOT / "social"
CAPTIONS = SOCIAL / "captions.yml"
SETTINGS = yaml.safe_load((ROOT / "pipeline" / "settings.yml").read_text(encoding="utf-8"))
# Where readers should land. Change in settings.yml once kaylestore.net points to Render.
PUBLIC_URL = SETTINGS.get("public_url", "https://kaylestore.onrender.com").rstrip("/")


def post_url(post):
    y, m, d = post.stem[:10].split("-")
    return f"{PUBLIC_URL}/{y}/{m}/{d}/{post.stem[11:]}/"


def main():
    SOCIAL.mkdir(exist_ok=True)
    captions = yaml.safe_load(CAPTIONS.read_text(encoding="utf-8")) if CAPTIONS.exists() else {}
    captions = captions or {}
    rows = []
    for post in sorted(images.POSTS.glob("*.md"), reverse=True):
        slug = post.stem[11:]
        meta = images.front_matter(post)
        url = post_url(post)
        folder = SOCIAL / slug
        folder.mkdir(exist_ok=True)

        img = next((images.IMAGES / f"{slug}{e}" for e in (".jpg", ".png", ".webp")
                    if (images.IMAGES / f"{slug}{e}").exists()), None)
        if img:
            shutil.copyfile(img, folder / f"image{img.suffix}")

        c = captions.get(slug, {})
        caption = (c.get("caption") or "TODO: write caption").strip()
        comment = (c.get("comment") or "👉 Read the full story here: {link}").strip().replace("{link}", url)
        done = bool(c.get("caption"))

        (folder / "facebook.md").write_text(
            f"# {meta.get('title', slug)}\n\n"
            f"**Post link:** {url}\n\n"
            f"**Image:** `image{img.suffix if img else '.jpg'}` in this folder"
            f"{'' if img else ' (not generated yet)'}\n\n"
            f"## Image prompt\n\n{images.prompt_for(meta)}\n\n"
            f"## Facebook caption\n\n{caption}\n\n"
            f"## First comment\n\n{comment}\n",
            encoding="utf-8",
        )
        rows.append(f"| {post.stem[:10]} | [{meta.get('title', slug)[:70]}]({slug}/facebook.md) | "
                    f"{'✅' if img else '❌'} | {'✅' if done else '✏️ TODO'} |")

    (SOCIAL / "index.md").write_text(
        "# Facebook kits\n\nEach folder has `image.jpg` and `facebook.md` (link, image prompt, "
        "caption, first comment).\n\n| Date | Post | Image | Caption |\n|---|---|---|---|\n"
        + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"Social kits: {len(rows)} posts")


if __name__ == "__main__":
    main()
