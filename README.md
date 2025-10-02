# Weekly Digest

Weekly Digest compiles finance and private-markets intelligence from curated RSS feeds, transforms it with the Perplexity API into a structured research brief, and emails the highlights to your distribution list every Monday at 06:00 Europe/Paris via GitHub Actions.

## Prerequisites
- Python 3.11+
- A Perplexity account with API access. Create an API key at <https://www.perplexity.ai/settings/api> (Upgrade → Developer Access → Generate key). Copy the value as `PERPLEXITY_API_KEY`.
- A Gmail account with 2-Step Verification enabled and an App Password dedicated to this workflow. Generate one at <https://myaccount.google.com/apppasswords> (Security → 2-Step Verification → App passwords → Select "Mail" / "Other" → copy the 16-character password). Use it as `SMTP_PASS`. The associated Gmail address becomes `SMTP_USER` and should match `MAIL_FROM`.
- GitHub repository access to configure Secrets and Variables.

## Configuration
1. Copy `.env.example` to `.env` and replace placeholder values.
2. (Optional) Adjust Perplexity defaults in `.env` (`PERPLEXITY_MODEL`, `PERPLEXITY_TIMEOUT`).
3. Edit `rss_list.csv` to add or remove sources. Leave `rss_url` blank when a publisher does not expose a feed—those rows are skipped automatically.
4. Maintain `members.csv` for a quick reference to your subscriber roster (email, name, join date, interests).
5. (Optional) Tailor the prompts in `prompts/system_prompt.txt` and `prompts/user_prompt_template.txt` to tweak tone, schema, or audience focus.
6. Keep `editorial.md` updated with a short, hand-written note — it renders ahead of the numbered articles.

The script automatically loads `.env` if present, so local runs can rely on environment variables declared in that file.

### Required environment variables
| Variable | Purpose |
| --- | --- |
| `PERPLEXITY_API_KEY` | Auth token for Perplexity Chat Completions |
| `PERPLEXITY_BASE_URL` | Optional override (defaults to `https://api.perplexity.ai`) |
| `PERPLEXITY_MODEL` | Perplexity model name (`sonar` by default) |
| `PERPLEXITY_TIMEOUT` | HTTP timeout in seconds (defaults to `60`) |
| `SMTP_SERVER` | SMTP host (`smtp.gmail.com`) |
| `SMTP_PORT` | SMTP SSL port (`465`) |
| `SMTP_USER` | Gmail address that owns the App Password |
| `SMTP_PASS` | Gmail App Password |
| `MAIL_FROM` | Friendly From email sent to subscribers |
| `MAIL_TO` | Optional extra recipients or fallback list (comma-separated) |
| `LOG_LEVEL` | Optional log verbosity (`INFO`, `DEBUG`, etc.) |

#### Optional environment variables
| Variable | Purpose |
| --- | --- |
| `ARCHIVE_URL` | Archive CTA in the footer |
| `MANAGE_TOPICS_URL` | Preferences management CTA |
| `UNSUBSCRIBE_URL` | Unsubscribe link in the footer |
| `SENDER_NAME` | Name displayed in the copyright notice |
| `SENDER_ADDRESS` | Mailing address shown in the footer |
| `DIGEST_TZ` | IANA timezone name for the send timestamp (`Europe/Paris` default) |
| `MEMBERS_CSV` | Path to CSV listing subscriber emails (defaults to `members.csv` in repo) |

## Install & Run Locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python morning_digest.py --csv rss_list.csv --hours 168 --topn 10 --members-csv members.csv
```

The script fetches up to 10 items per feed, filters for the last 7 days, deduplicates, and asks Perplexity for structured JSON that includes highlights, market-impact commentary, and suggested follow-up actions. If the Perplexity API is unavailable, the email automatically falls back to curated headlines while preserving the newsletter shell.

## GitHub Actions Automation
The workflow in `.github/workflows/morning-digest.yml` runs every Monday at 04:00 UTC (06:00 Paris) and can also be triggered manually. Configure repository-level Secrets and Variables:

- **Secrets**: `PERPLEXITY_API_KEY`, `SMTP_USER`, `SMTP_PASS`
- **Variables**: `SMTP_SERVER` (`smtp.gmail.com`), `SMTP_PORT` (`465`), `MAIL_FROM`, `MAIL_TO`, `MEMBERS_CSV`

Once secrets are set, the workflow installs dependencies and executes `python morning_digest.py --csv rss_list.csv --topn 10 --hours 168` on `ubuntu-latest`, producing the fully branded HTML template.

## Newsletter template & prompts
- The HTML sent to subscribers mirrors `Wayve weekly research brief` from the design above and now presents a numbered list (max 10) with tags, paywall badges, publication dates, and a hand-edited editorial intro.
- Edit `prompts/system_prompt.txt` or `prompts/user_prompt_template.txt` to adjust tone, schema, or emphasis. Changes take effect on the next run—no code edits required.
- Update `editorial.md` before each send to surface bespoke talking points.

## Maintaining Feeds
- Add rows to `rss_list.csv` with `name`, `rss_url`, `notes`.
- Keep notes as reminders about paywalls or non-RSS sources. Blank `rss_url` entries are ignored but preserved for future work (for example Finimize or Beehiiv feeds that may become available).

## Data Files
- `rss_list.csv`: curated finance and PE feeds seeded with 10 starters.
- `members.csv`: sample subscriber list with interests that mirror digest themes.

## Troubleshooting & Logging
- Set `LOG_LEVEL=DEBUG` for verbose output during local runs.
- If Gmail rejects authentication, double-check that 2-Step Verification is enabled and that you are using the App Password (not the account password).
- When the digest email shows “Summaries unavailable; showing headlines.”, Perplexity was unreachable or the API key is missing. Investigate network or credential issues while the job continues delivering headlines.

## Testing
Install dev dependencies (`python -m pip install pytest`) and run:
```bash
python -m pytest
```
The suite stubs Perplexity, feed fetching, and SMTP delivery so you can validate the workflow without hitting external services or mailing subscribers.
