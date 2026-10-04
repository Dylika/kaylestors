"""Topic-driven article pipeline.

    python pipeline/generate.py write [--count N]   research + write the next N topics from topics.txt
    python pipeline/generate.py names "niche"       suggest website names for a niche

Each article: Claude researches the topic with web search, writes an original piece
that cites its sources, LanguageTool flags grammar issues, Claude fixes the genuine
ones, and the result is saved as a Jekyll post in _posts/.
"""

import argparse
import datetime as dt
import html
import re
import sys
import unicodedata
from itertools import zip_longest
from pathlib import Path

import anthropic
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "topics.txt"
DONE = ROOT / "pipeline" / "published.txt"
POSTS = ROOT / "_posts"
SUMMARY = ROOT / "pipeline" / "last_run.md"

SETTINGS = yaml.safe_load((ROOT / "pipeline" / "settings.yml").read_text(encoding="utf-8"))
SITE = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))
MODEL = SETTINGS.get("model", "claude-opus-5-5")

client = anthropic.Anthropic()


# ---------------------------------------------------------------- Claude calls

def ask(system, prompt, *, effort="high", tools=None):
    """Run one request to completion (resuming pause_turn) and return all content blocks."""
    user = {"role": "user", "content": prompt}
    messages = [user]
    blocks = []
    for _ in range(6):
        params = dict(
            model=MODEL,
            max_tokens=32000,
            system=system,
            messages=messages,
            output_config={"effort": effort},
            # On a safety decline, re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if tools:
            params["tools"] = tools
        with client.beta.messages.stream(**params) as stream:
            resp = stream.get_final_message()

        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model declined: {resp.stop_details}")
        blocks.extend(resp.content)
        if resp.stop_reason == "pause_turn":
            messages = [user, {"role": "assistant", "content": blocks}]
            continue
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("response hit max_tokens")
        return blocks
    raise RuntimeError("too many pause_turn continuations")


def text_of(blocks):
    return "".join(b.text for b in blocks if b.type == "text")


def searched_urls(blocks):
    urls = set()
    for b in blocks:
        if b.type == "web_search_tool_result" and isinstance(b.content, list):
            urls.update(r.url for r in b.content if getattr(r, "url", None))
    return urls


def section(text, name, end):
    m = re.search(rf"---{name}---\s*(.*?)(?=---{end}---|\Z)", text, re.S)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- writing

WRITER_SYSTEM = """You are the staff writer for "{title}" — {description}
Audience: {audience}. Voice: {style}. Today is {today}.

How you work:
1. Research the topic with web search. Use several independent, reputable sources;
   prefer primary sources and recent information.
2. Write an original article that synthesises what you learned, with your own structure,
   angle and wording. Never copy or closely paraphrase a single source; quote at most a
   short phrase (under 15 words) with attribution.
3. Only state facts your sources support. Where sources disagree or evidence is thin, say so.
4. Make it genuinely useful: specific details, examples, practical takeaways. No filler,
   hype or clichés. {min_words}-{max_words} words. Markdown with ## subheadings; no H1.

Reply in exactly this format and nothing after it:
---META---
title: <headline, under 70 characters>
description: <one-sentence summary, under 160 characters>
tags: <2-4 comma-separated tags>
---ARTICLE---
<the article in Markdown>
---SOURCES---
- [Source title](https://url)
---END---"""


def write_article(topic, notes):
    system = WRITER_SYSTEM.format(
        title=SITE.get("title", ""),
        description=SITE.get("description", ""),
        today=dt.date.today().isoformat(),
        **SETTINGS,
    )
    prompt = f"Write an article on: {topic}"
    if notes:
        prompt += f"\n\nEditor's notes: {notes}"
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": SETTINGS["max_searches"]}]
    blocks = ask(system, prompt, effort=SETTINGS.get("effort", "high"), tools=tools)

    text = text_of(blocks)
    meta, body = parse_meta(text, "ARTICLE", "SOURCES")
    sources_raw = section(text, "SOURCES", "END")

    # Keep only sources the model actually retrieved, so no invented links reach the page.
    found = searched_urls(blocks)
    sources = [(t, u) for t, u in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", sources_raw or "")
               if u in found]
    return meta, body, sources


def parse_meta(text, body_name, body_end):
    meta_raw, body = section(text, "META", body_name), section(text, body_name, body_end)
    if not (meta_raw and body):
        raise RuntimeError("could not parse the writer's response")
    meta = dict(line.split(":", 1) for line in meta_raw.splitlines() if ":" in line)
    return {k.strip().lower(): v.strip() for k, v in meta.items()}, body


STORY_SYSTEM = """You write original short fiction for "{title}". Genre: emotional, first-person
real-life drama — family conflict, betrayal, unexpected kindness, karma, and a satisfying
twist or reveal at the end.

- Invent every character, place and plot detail yourself. Do not retell, adapt or borrow the
  plot of any existing story, viral post or article.
- Write a vivid, well-paced first-person story: a hook in the first lines, believable dialogue,
  rising tension, a clear payoff. {story_min_words}-{story_max_words} words, plain Markdown
  paragraphs (no headings). Keep it suitable for a general audience.
- The headline may be long and intriguing in the style of the genre, but it must honestly
  reflect what happens in the story.

Reply in exactly this format and nothing after it:
---META---
title: <headline>
description: <one-sentence teaser, under 160 characters>
tags: <2-3 comma-separated tags, e.g. Family, Stories>
---STORY---
<the story>
---END---"""


def write_story(premise, notes):
    system = STORY_SYSTEM.format(title=SITE.get("title", ""), **SETTINGS)
    prompt = f"Write a story based on this premise: {premise}"
    if notes:
        prompt += f"\n\nEditor's notes: {notes}"
    meta, body = parse_meta(text_of(ask(system, prompt, effort=SETTINGS.get("effort", "high"))),
                            "STORY", "END")
    return meta, body, []


def inspiration_titles():
    """Recent post titles from the inspiration site, used only as a guide to themes."""
    url = SETTINGS.get("inspiration_site")
    if not url:
        return []
    try:
        r = requests.get(f"{url.rstrip('/')}/wp-json/wp/v2/posts",
                         params={"per_page": 20, "_fields": "title"}, timeout=30)
        r.raise_for_status()
        return [html.unescape(p["title"]["rendered"]) for p in r.json()]
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  (inspiration site unavailable: {e})")
        return []


def suggest_topics(n_stories, n_articles):
    """Have Claude invent fresh story premises and article topics, avoiding past ones."""
    past = DONE.read_text(encoding="utf-8").splitlines()[-150:] if DONE.exists() else []
    past = [line.split("\t")[1] for line in past if "\t" in line]
    trends = inspiration_titles()
    trend_note = ""
    if trends:
        trend_note = (
            "\n\nFor a sense of the themes and emotional hooks this audience responds to, here are "
            "recent headlines from a similar site. Use them only to understand the themes "
            "(e.g. family betrayal, hidden generosity, karma). Every premise must have its own "
            "characters, setting, conflict and twist; never reuse or lightly vary these plots:\n"
            + "\n".join(f"- {t}" for t in trends)
        )
    prompt = (
        f'Suggest new content for "{SITE.get("title", "")}", a site of emotional real-life drama '
        f"stories plus practical articles for the same readers.{trend_note}\n\n"
        f"- {n_stories} original story premises (1-2 sentences each: who, the conflict, the twist). "
        "Invent them fresh; do not base them on any existing story or viral post.\n"
        f"- {n_articles} practical, researchable article topics on the real-life issues such "
        "stories touch: family money, relationships, inheritance, workplace conflict, elder care, "
        "kindness and community.\n\nAvoid repeating these past items:\n"
        + ("\n".join(f"- {p}" for p in past) or "(none yet)")
        + "\n\nReply with one item per line, nothing else, formatted exactly as\n"
        "story: <premise>\narticle: <topic>"
    )
    text = text_of(ask("You are the editor of a popular content site.", prompt, effort="low"))
    return [line.strip() for line in text.splitlines()
            if re.match(r"^(story|article):\s*\S", line.strip(), re.I)]


# ---------------------------------------------------------------- grammar

def to_plain(md):
    md = re.sub(r"```.*?```", "", md, flags=re.S)
    md = re.sub(r"`[^`]*`", "", md)
    md = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"^\s{0,3}(#+|[-*+]|\d+\.)\s+", "", md, flags=re.M)
    return re.sub(r"[*_]{1,3}", "", md)


def grammar_issues(md):
    plain = to_plain(md)
    chunks, cur = [], ""
    for para in plain.split("\n\n"):  # the free API caps request size
        if len(cur) + len(para) > 15000:
            chunks.append(cur)
            cur = ""
        cur += para + "\n\n"
    chunks.append(cur)

    issues = []
    for chunk in chunks:
        r = requests.post(SETTINGS["languagetool_url"],
                          data={"text": chunk, "language": SETTINGS["language"]}, timeout=60)
        r.raise_for_status()
        for m in r.json()["matches"]:
            ctx = m["context"]
            bad = ctx["text"][ctx["offset"]:ctx["offset"] + ctx["length"]]
            fixes = ", ".join(x["value"] for x in m["replacements"][:3]) or "n/a"
            issues.append(f'- "{bad}" in "…{ctx["text"]}…": {m["message"]} (suggested: {fixes})')
    return issues


def fix_grammar(body, issues):
    prompt = ("An automated grammar checker flagged the issues below in this article. Fix the "
              "genuine errors. Ignore false positives such as names, technical terms, deliberate "
              "style choices and Markdown syntax. Change nothing else.\n\nFlagged issues:\n"
              + "\n".join(issues)
              + f"\n\n---ARTICLE---\n{body}\n---END---\n\n"
              "Reply with the full corrected article between ---ARTICLE--- and ---END--- markers.")
    fixed = section(text_of(ask("You are a meticulous copy editor.", prompt, effort="low")),
                    "ARTICLE", "END")
    if not fixed or len(fixed) < 0.8 * len(body):  # guard against a truncated rewrite
        return body
    return fixed


def grammar_gate(body):
    """Check -> fix -> re-check before anything is saved. Raises (topic stays queued) if the
    checker can't be reached, so nothing is published without a grammar check."""
    first = None
    for _ in range(SETTINGS.get("grammar_passes", 2)):
        try:
            issues = grammar_issues(body)
        except requests.RequestException as e:
            raise RuntimeError(f"grammar check unavailable ({e}); not publishing") from e
        first = len(issues) if first is None else first
        if not issues:
            return body, f"{first} flagged, all resolved" if first else "clean"
        body = fix_grammar(body, issues)
    try:
        remaining = len(grammar_issues(body))
    except requests.RequestException as e:
        raise RuntimeError(f"grammar re-check unavailable ({e}); not publishing") from e
    # Whatever is left after the editor's passes was judged a false positive (names, dialogue, style).
    return body, f"{first} flagged, {remaining} left as false positives"


# ---------------------------------------------------------------- saving

def slugify(title):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60].rstrip("-") or "article"


def save_post(meta, body, sources, fiction=False):
    now = dt.datetime.now(dt.timezone.utc)
    slug = slugify(meta["title"])
    path = POSTS / f"{now:%Y-%m-%d}-{slug}.md"
    n = 2
    while path.exists():
        path = POSTS / f"{now:%Y-%m-%d}-{slug}-{n}.md"
        n += 1

    front = {
        "layout": "post",
        "title": meta["title"],
        "description": meta.get("description", ""),
        "date": now.strftime("%Y-%m-%d %H:%M:%S +0000"),
        "tags": [t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
    }
    if fiction:
        front["fiction"] = True
    text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n"
    if sources:
        text += "\n## Sources\n\n" + "\n".join(f"- [{t}]({u})" for t, u in sources) + "\n"
    POSTS.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- queue

def read_queue():
    lines = TOPICS.read_text(encoding="utf-8").splitlines()
    queue = [(i, *map(str.strip, (l.split("|", 1) + [""])[:2]))
             for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")]
    return lines, queue


def refill_queue(needed):
    """Top up topics.txt with Claude-picked items, split between stories and articles."""
    batch = max(needed, SETTINGS.get("auto_topics_batch", 6))
    n_stories = round(batch * SETTINGS.get("story_share", 0.5))
    items = suggest_topics(n_stories, batch - n_stories)
    # Interleave so each run gets a mix rather than all stories first.
    stories = [i for i in items if i.lower().startswith("story:")]
    articles = [i for i in items if not i.lower().startswith("story:")]
    items = [x for pair in zip_longest(stories, articles) for x in pair if x]
    print(f"Queue low: added {len(items)} auto-picked topics")
    with TOPICS.open("a", encoding="utf-8") as f:
        f.write("".join(f"{item}\n" for item in items))


def split_kind(topic):
    m = re.match(r"^(story|article):\s*(.+)$", topic, re.I)
    return (m.group(1).lower(), m.group(2)) if m else ("article", topic)


def cmd_write(count):
    lines, queue = read_queue()
    if len(queue) < count and SETTINGS.get("auto_topics", True):
        refill_queue(count - len(queue))
        lines, queue = read_queue()
    if not queue:
        print("topics.txt is empty, nothing to write.")
        return 0

    done_idx, report, failures = set(), [], 0
    for idx, topic, notes in queue[:count]:
        kind, subject = split_kind(topic)
        print(f"Writing {kind}: {subject}")
        try:
            if kind == "story":
                meta, body, sources = write_story(subject, notes)
            else:
                meta, body, sources = write_article(subject, notes)
            body, grammar = grammar_gate(body)
            path = save_post(meta, body, sources, fiction=(kind == "story"))
        except Exception as e:  # keep the topic queued and move on to the next one
            print(f"  FAILED: {e}", file=sys.stderr)
            report.append(f"- ❌ **{topic}**: {e}")
            failures += 1
            continue

        words = len(body.split())
        print(f"  saved {path.relative_to(ROOT)} ({words} words, {len(sources)} sources, grammar: {grammar})")
        report.append(f"- ✅ **{meta['title']}** — `{path.relative_to(ROOT)}`, {words} words, "
                      f"{len(sources)} sources, grammar: {grammar}")
        done_idx.add(idx)
        with DONE.open("a", encoding="utf-8") as f:
            f.write(f"{dt.date.today()}\t{topic}\t{path.name}\n")

    TOPICS.write_text("\n".join(l for i, l in enumerate(lines) if i not in done_idx) + "\n", encoding="utf-8")
    SUMMARY.write_text("## Article run\n\n" + "\n".join(report) + "\n", encoding="utf-8")
    return 1 if failures and not done_idx else 0


def cmd_names(niche, count):
    prompt = (f"Suggest {count} names for a content website about: {niche}.\n"
              "Make them short, memorable, easy to spell and say aloud, and not the name of an "
              "existing well-known brand. Mix styles (descriptive, brandable, playful). For each give "
              "the name, a lowercase GitHub-repo-friendly slug, and a one-line tagline.\n"
              "Format each line as: Name | slug | tagline")
    print(text_of(ask("You are a sharp brand-naming consultant.", prompt, effort="low")))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--count", type=int, default=1)
    n = sub.add_parser("names")
    n.add_argument("niche")
    n.add_argument("--count", type=int, default=15)
    args = p.parse_args()
    if args.cmd == "write":
        sys.exit(cmd_write(args.count))
    cmd_names(args.niche, args.count)


if __name__ == "__main__":
    main()
