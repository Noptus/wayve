You are Codex, an elite software automation agent. Create a repository named `morning-digest` with the following files and behaviour:

1. `morning_digest.py`
   - CLI: `python morning_digest.py --csv rss_list.csv --topn 8 --hours 24 --per-feed 10`.
   - Loads RSS metadata from the CSV (skip blank URLs).
   - Pulls up to `--per-feed` items per feed via `feedparser`.
   - Keeps entries published in the last `--hours` hours (fall back to including if timestamp missing).
   - Deduplicates by lowercased title + link.
   - Sends the top `--topn` items to Perplexity Chat Completions (OpenAI-compatible) using `requests` with headers `Authorization: Bearer $PERPLEXITY_API_KEY` and `Content-Type: application/json`.
   - Prompt: "Turn each line into a tight, neutral, finance-friendly one-liner (≤18 words), keep the original link, no emojis, no numbering. Return as HTML <li><a>Title</a></li> list." Include a system message "You are a concise finance news editor.".
   - Render an HTML email with headline, count and footer mentioning automated delivery at 06:00 Paris time.
   - Send via Gmail SMTP over SSL using `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` from env vars.
   - If no fresh items, send placeholder HTML with a single `<li>` message and subject `"Morning Digest — No new items"`.
   - Provide configurable Perplexity base URL/model via env vars `PERPLEXITY_BASE_URL`, `PERPLEXITY_MODEL`, `PERPLEXITY_TIMEOUT`.

2. `requirements.txt`
   - Contents:
     ```
     feedparser==6.0.11
     requests>=2.31
     python-dateutil>=2.9
     html5lib>=1.1
     beautifulsoup4>=4.12
     ```

3. `rss_list.csv`
   - Include the curated starter list of feeds:
     ```
     name,rss_url,notes
     Exec Sum,https://www.execsum.co/feed,If 404 then the site may have disabled RSS; keep for reference
     Finimize Daily Brief,,No public RSS; consider Finimize API (AWS Marketplace) or email ingestion
     Road To Carry (PE Newsletter),,Beehiiv can expose RSS if enabled; ask owner or check for a generated .xml feed
     Beckford Capital Blog,https://www.beckfordcapital.com/blog-1-2?format=rss,Posts on FX/macro (Squarespace feed)
     Reuters Business & Markets,https://feeds.reuters.com/reuters/businessNews,High-signal global business headlines
     FT Due Diligence,https://www.ft.com/due-diligence?format=rss,FT M&A/PE scoop (paywalled content)
     Private Equity Wire,https://www.privateequitywire.co.uk/rss,PE industry headlines
     PitchBook News (Blog),https://pitchbook.com/news/rss,Deal flow & private markets (some items tease full article)
     Axios Pro Rata,https://www.axios.com/feeds/newsletters/pro-rata.xml,Daily deal & private markets brief (if this URL changes, check Axios RSS index)
     The Economist – Finance & economics,https://www.economist.com/finance-and-economics/rss.xml,Macro/finance context
     ```

4. `tests/test_digest.py`
   - Pytest suite with fixtures that mock `feedparser.parse`, `requests.post`, and `smtplib.SMTP_SSL`.
   - Cover: filtering by timestamps, dedupe logic, Perplexity payload contents, rendering fallback email, and successful send path.
   - Use temporary CSV samples in tests.

5. `.github/workflows/morning-digest.yml`
   - Workflow `name: morning-digest`.
   - Trigger: schedule at `cron: "0 4 * * *"` (Paris 06:00) and `workflow_dispatch`.
   - Env `TZ: Europe/Paris`.
   - Steps: checkout, setup Python 3.11, install requirements, run `python morning_digest.py --csv rss_list.csv --topn 8 --hours 24` with env vars wired from GitHub secrets/variables:
     - secrets: `PERPLEXITY_API_KEY`, `SMTP_USER`, `SMTP_PASS`
     - variables: `SMTP_SERVER`, `SMTP_PORT`, `MAIL_FROM`, `MAIL_TO`

6. Documentation
   - Update `README.md` with setup instructions, required env vars, and how to test locally.

Constraints:
- Keep files ASCII unless the content already demands otherwise.
- Add concise comments only for non-obvious logic.
- Structure code cleanly; prefer functions over inline scripts.
- Ensure tests pass via `pytest`.
