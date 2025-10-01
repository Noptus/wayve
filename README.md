# Morning Digest

Morning Digest compiles finance and private-markets headlines from curated RSS feeds, condenses them with the Perplexity API, and emails the highlights to your distribution list each morning at 06:00 Europe/Paris via GitHub Actions.

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
| `MAIL_TO` | Recipient list (comma-separated allowed) |
| `LOG_LEVEL` | Optional log verbosity (`INFO`, `DEBUG`, etc.) |

## Install & Run Locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python morning_digest.py --csv rss_list.csv --hours 24 --topn 8
```

The script fetches up to 10 items per feed, filters for the last 24 hours, deduplicates, and asks Perplexity for one-line summaries. If the Perplexity API is unreachable or returns an invalid response, the email automatically falls back to formatted RSS headlines so delivery never fails.

## GitHub Actions Automation
The workflow in `.github/workflows/morning-digest.yml` runs daily at 04:00 UTC (06:00 Paris) and can also be triggered manually. Configure repository-level Secrets and Variables:

- **Secrets**: `PERPLEXITY_API_KEY`, `SMTP_USER`, `SMTP_PASS`
- **Variables**: `SMTP_SERVER` (`smtp.gmail.com`), `SMTP_PORT` (`465`), `MAIL_FROM`, `MAIL_TO`

Once secrets are set, the workflow installs dependencies and executes `python morning_digest.py --csv rss_list.csv --topn 8 --hours 24` on `ubuntu-latest`.

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
Use pytest (mocking out external calls) to extend coverage. Example entry point for future tests lives in `tests/test_digest.py` as specified in `codex_prompt.md`.
