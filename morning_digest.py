"""Generate and email a daily finance-focused news digest."""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterable, List, Sequence

import feedparser
import requests
from dateutil import parser as dt_parser

DEFAULT_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
DEFAULT_PERPLEXITY_MODEL = "sonar"
DEFAULT_PERPLEXITY_TIMEOUT = 60


class PerplexityError(RuntimeError):
    """Raised when Perplexity summarisation fails."""


def load_env_file(path: str = ".env") -> None:
    """Populate environment variables from a simple KEY=VALUE .env file."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError as exc:
        raise SystemExit(f"Failed to read {path}: {exc}")


load_env_file()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("morning_digest")


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
            logger.warning("Failed parsing %s: %s", feed["name"], exc)
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


def _perplexity_config() -> tuple[str, str, int]:
    base_url = os.environ.get("PERPLEXITY_BASE_URL", DEFAULT_PERPLEXITY_BASE_URL)
    model = os.environ.get("PERPLEXITY_MODEL", DEFAULT_PERPLEXITY_MODEL)
    timeout = int(os.environ.get("PERPLEXITY_TIMEOUT", str(DEFAULT_PERPLEXITY_TIMEOUT)))
    return base_url, model, timeout


def summarize_with_perplexity(items: Sequence[dict]) -> str:
    """Use Perplexity's Chat Completions API to generate HTML list items."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise PerplexityError("PERPLEXITY_API_KEY is not set")
    base_url, model, timeout = _perplexity_config()
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
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise finance news editor."},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices")
        if not choices:
            raise PerplexityError("Perplexity response contained no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise PerplexityError("Perplexity response missing summary content")
        return content
    except (requests.RequestException, ValueError) as exc:
        raise PerplexityError(str(exc)) from exc


def build_fallback_html(items: Sequence[dict]) -> str:
    """Format RSS items into HTML list items when Perplexity is unavailable."""
    lines = []
    for item in items:
        safe_title = escape(item["title"])
        safe_source = escape(item["source"])
        safe_link = escape(item["link"], quote=True)
        lines.append(f'<li><strong>{safe_source}</strong>: <a href="{safe_link}">{safe_title}</a></li>')
    return "\n      ".join(lines)


def render_email(html_list: str, count: int, used_fallback: bool = False) -> str:
    """Build the HTML email body."""
    today = datetime.now().strftime("%A, %d %b %Y")
    summary_note = "Summaries provided by Perplexity." if not used_fallback else "Summaries unavailable; showing headlines."
    return f"""
<html>
  <body>
    <h2>Morning Digest — {escape(today)}</h2>
    <p>{count} highlights from the last 24&nbsp;hours.</p>
    <p style=\"color:#555;font-size:13px\">{escape(summary_note)}</p>
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

    try:
        with smtplib.SMTP_SSL(os.environ["SMTP_SERVER"], int(os.environ["SMTP_PORT"])) as smtp:
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            smtp.send_message(message)
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Failed to send email: {exc}") from exc


def format_items_with_fallback(items: Sequence[dict]) -> tuple[str, bool]:
    try:
        html_list = summarize_with_perplexity(items)
        return html_list, False
    except PerplexityError as exc:
        logger.warning("Perplexity summarisation failed: %s", exc)
        return build_fallback_html(items), True


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
        logger.info("No fresh items found; sending placeholder email.")
        html = render_email("<li>No fresh items found in the last 24h.</li>", 0, used_fallback=True)
        send_email(html, "Morning Digest — No new items")
        return

    selected = items[: args.topn]
    html_list, used_fallback = format_items_with_fallback(selected)
    body = render_email(html_list, len(selected), used_fallback=used_fallback)
    subject = "Morning Digest — Top highlights"
    if used_fallback:
        subject += " (headlines)"
    send_email(body, subject)


if __name__ == "__main__":
    main()
