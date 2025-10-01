"""Generate and email a daily finance-focused news digest."""
from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterable, List, Sequence

import feedparser
import requests
from dateutil import parser as dt_parser

PERPLEXITY_BASE_URL = os.environ.get("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar")
PERPLEXITY_TIMEOUT = int(os.environ.get("PERPLEXITY_TIMEOUT", "60"))


def load_feeds(csv_path: str) -> List[dict]:
    """Return a list of feeds that have a populated RSS URL."""
    feeds: List[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("rss_url") or "").strip()
            if not url:
                continue
            feeds.append(
                {
                    "name": row.get("name", "Unnamed Feed"),
                    "url": url,
                    "notes": row.get("notes", ""),
                }
            )
    return feeds


def _entry_timestamp(entry: dict) -> datetime | None:
    """Parse the first available timestamp from an RSS entry."""
    for key in ("published", "updated", "created", "modified"):
        raw_value = entry.get(key)
        if not raw_value:
            continue
        try:
            dt = dt_parser.parse(raw_value)
        except (ValueError, TypeError):
            continue
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def in_last_hours(entry: dict, hours: int) -> bool:
    """Return True when the entry is within the specified time window."""
    timestamp = _entry_timestamp(entry)
    if not timestamp:
        return True
    threshold = datetime.now(timezone.utc) - timedelta(hours=hours)
    return timestamp >= threshold


def fetch_items(feeds: Sequence[dict], hours: int, per_feed: int = 10) -> List[dict]:
    """Collect feed entries that fall within the look-back window."""
    items: List[dict] = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"])
        except Exception as exc:  # pragma: no cover - network edge cases
            print(f"[WARN] Failed parsing {feed['name']}: {exc}")
            continue
        for entry in parsed.entries[:per_feed]:
            if not in_last_hours(entry, hours):
                continue
            title = (entry.get("title") or "(no title)").strip()
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            items.append({"source": feed["name"], "title": title, "link": link})
    return items


def dedupe(items: Iterable[dict]) -> List[dict]:
    """Remove duplicate entries based on title/link pairs."""
    seen: set[tuple[str, str]] = set()
    unique: List[dict] = []
    for item in items:
        key = (item["title"].lower(), item["link"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def summarize_with_perplexity(items: Sequence[dict]) -> str:
    """Use Perplexity's Chat Completions API to generate HTML list items."""
    api_key = os.environ["PERPLEXITY_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    bullet_lines = "\n".join(f"- {item['title']} — {item['link']}" for item in items)
    user_prompt = (
        "Turn each line into a tight, neutral, finance-friendly one-liner (≤18 words), "
        "keep the original link, no emojis, no numbering. Return as HTML <li><a>Title</a></li> list.\n\n"
        f"{bullet_lines}"
    )
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {"role": "system", "content": "You are a concise finance news editor."},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    response = requests.post(
        f"{PERPLEXITY_BASE_URL}/chat/completions",
        json=payload,
        headers=headers,
        timeout=PERPLEXITY_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def render_email(html_list: str, count: int) -> str:
    """Build the HTML email body."""
    today = datetime.now().strftime("%A, %d %b %Y")
    return f"""
<html>
  <body>
    <h2>Morning Digest — {escape(today)}</h2>
    <p>{count} highlights from the last 24&nbsp;hours:</p>
    <ul>
      {html_list}
    </ul>
    <p style=\"color:#666;font-size:12px\">Automated delivery at ~06:00 Paris time.</p>
  </body>
</html>
""".strip()


def send_email(html_body: str, subject: str) -> None:
    """Send the digest via Gmail SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.utils import formatdate

    message = MIMEText(html_body, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = os.environ["MAIL_FROM"]
    message["To"] = os.environ["MAIL_TO"]
    message["Date"] = formatdate(localtime=True)

    with smtplib.SMTP_SSL(os.environ["SMTP_SERVER"], int(os.environ["SMTP_PORT"])) as smtp:
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        smtp.send_message(message)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compile and email a morning finance digest.")
    parser.add_argument("--csv", required=True, help="CSV file containing feed metadata")
    parser.add_argument("--hours", type=int, default=24, help="Look-back window for feed entries")
    parser.add_argument("--topn", type=int, default=8, help="Number of entries to include in the digest")
    parser.add_argument(
        "--per-feed",
        type=int,
        default=10,
        help="Maximum number of items fetched per feed before filtering",
    )
    args = parser.parse_args(argv)

    feeds = load_feeds(args.csv)
    if not feeds:
        raise SystemExit(f"No feeds found in {args.csv}.")

    items = dedupe(fetch_items(feeds, hours=args.hours, per_feed=args.per_feed))

    if not items:
        print("No fresh items found; sending placeholder email.")
        html = render_email("<li>No fresh items found in the last 24h.</li>", 0)
        send_email(html, "Morning Digest — No new items")
        return

    selected = items[: args.topn]
    html_list = summarize_with_perplexity(selected)
    body = render_email(html_list, len(selected))
    send_email(body, "Morning Digest — Top highlights")


if __name__ == "__main__":
    main()
