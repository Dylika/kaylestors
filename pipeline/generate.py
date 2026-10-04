"""Topic-driven article pipeline.

    python pipeline/generate.py write [--count N]   research + write the next N topics from topics.txt
    python pipeline/generate.py names "niche"       suggest website names for a niche

Each article: Claude researches the topic with web search, writes an original piece
that cites its sources, LanguageTool flags grammar issues, Claude fixes the genuine
ones, and the result is saved as a Jekyll post in _posts/.
"""

import argparse
import datetime as dt
import re
import sys
import unicodedata
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
    meta_raw, body, sources_raw = (section(text, "META", "ARTICLE"),
                                   section(text, "ARTICLE", "SOURCES"),
                                   section(text, "SOURCES", "END"))
    if not (meta_raw and body):
        raise RuntimeError("could not parse the writer's response")
    meta = dict(line.split(":", 1) for line in meta_raw.splitlines() if ":" in line)
    meta = {k.strip().lower(): v.strip() for k, v in meta.items()}

    # Keep only sources the model actually retrieved, so no invented links reach the page.
    found = searched_urls(blocks)
    sources = [(t, u) for t, u in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", sources_raw or "")
               if u in found]
    return meta, body, sources


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


# ---------------------------------------------------------------- saving

def slugify(title):
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60].rstrip("-") or "article"


def save_post(meta, body, sources):
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


def cmd_write(count):
    lines, queue = read_queue()
    if not queue:
        print("topics.txt is empty, nothing to write.")
        return 0

    done_idx, report, failures = set(), [], 0
    for idx, topic, notes in queue[:count]:
        print(f"Writing: {topic}")
        try:
            meta, body, sources = write_article(topic, notes)
            try:
                issues = grammar_issues(body)
                if issues:
                    body = fix_grammar(body, issues)
                grammar = f"{len(issues)} flagged, fixes applied"
            except requests.RequestException as e:
                grammar = f"skipped (LanguageTool error: {e})"
            path = save_post(meta, body, sources)
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
