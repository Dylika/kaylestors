# Kaylestore — automated article site

A Jekyll site hosted on Render, plus a daily GitHub Action that turns topics you give it
into researched, original, grammar-checked articles.

```
topics.txt ──► Claude researches (web search) ──► writes original article with sources
           ──► LanguageTool flags grammar ──► Claude fixes genuine issues
           ──► _posts/…md ──► push to main ──► Render auto-deploys kaylestore.net
```

## Day-to-day use

- **Add topics:** one topic or title per line in `topics.txt` (edit on github.com or push).
  Optional writer notes go after `|`.
- **Publishing:** runs daily at 09:00 UTC, writing `ARTICLES_PER_RUN` topics (default 1).
  To run it now: **Actions → Write & publish articles → Run workflow**.
- **Review first (optional):** set repository variable `PUBLISH_MODE` = `pr`. Articles then
  arrive as pull requests, and merging one publishes it.

## Page layout

- **Read Full Story:** posts of 400+ words show their opening, then a button that expands
  the rest. The full text is always in the page, so search engines index all of it.
- **Adskeeper widgets:** loader script in every page's `<head>`, widget `1992086` after the
  third paragraph, and widget `1992385` under the article. IDs live in `_config.yml`.

## One-time setup

1. **GitHub:** repo **Settings → Secrets and variables → Actions** → add secret
   `ANTHROPIC_API_KEY`. Optional variables: `ARTICLES_PER_RUN`, `PUBLISH_MODE`.
   For PR mode, also enable **Settings → Actions → General → Allow GitHub Actions to create
   pull requests**.
2. **Render:** **New → Blueprint**, pick this repo. `render.yaml` creates the static site
   (`bundle exec jekyll build` → `_site`) with auto-deploy on every push.
3. **Domain:** in the Render service's **Settings → Custom Domains**, follow the DNS
   instructions for `kaylestore.net` and `www.kaylestore.net` at your domain registrar.
4. **Adskeeper:** make sure `kaylestore.net` is added in your Adskeeper dashboard, since
   the widgets only serve on approved domains.

## Cost

Each article is roughly one Claude Opus 5.5 research and writing call with up to 8 web searches,
plus a small grammar-fix call. Expect roughly $0.30–$1 per article (estimate; check your usage).
Lower `effort` or `max_searches` in `pipeline/settings.yml` to reduce it.
