"""Generate and email a weekly finance-focused news digest."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Iterable, List, Sequence, Any, Dict
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dt_parser

DEFAULT_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
DEFAULT_PERPLEXITY_MODEL = "sonar"
DEFAULT_PERPLEXITY_TIMEOUT = 60

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system_prompt.txt"
USER_PROMPT_PATH = PROMPTS_DIR / "user_prompt_template.txt"
EDITORIAL_PATH = BASE_DIR / "editorial.md"

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


def _load_prompt(path: Path) -> str:
    """Load and validate a prompt file."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise SystemExit(f"Prompt file missing: {path}") from exc
    if not text:
        raise SystemExit(f"Prompt file {path} is empty; cannot proceed.")
    return text


SYSTEM_PROMPT = _load_prompt(SYSTEM_PROMPT_PATH)
USER_PROMPT_TEMPLATE = _load_prompt(USER_PROMPT_PATH)


def _mail_recipients() -> List[str]:
    """Return the list of recipient email addresses."""
    raw = os.environ.get("MAIL_TO", "")
    recipients = [addr.strip() for addr in raw.split(",") if addr.strip()]
    if not recipients:
        raise SystemExit("MAIL_TO is not set or contains no valid addresses.")
    return recipients


def _token_usage_payload(usage: Dict[str, Any] | None) -> Dict[str, int]:
    """Normalise token usage information from the Perplexity API."""
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    if total == 0:
        total = prompt + completion
    return {"prompt": prompt, "completion": completion, "total": total}


def _markdown_to_html(text: str) -> str:
    """Convert a minimal subset of Markdown to HTML."""
    if not text.strip():
        return ""
    lines = text.strip().splitlines()
    html_parts: List[str] = []
    in_list = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("# "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            heading_text = escape(stripped[2:].strip())
            html_parts.append(
                "<h3 style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#f8fafc;font-size:16px;margin:0 0 6px 0;\">"
                + heading_text
                + "</h3>"
            )
        elif stripped.startswith("- "):
            if not in_list:
                html_parts.append(
                    "<ul style=\"margin:0 0 10px 18px;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#cbd5f5;font-size:13px;line-height:20px;\">"
                )
                in_list = True
            html_parts.append("<li>" + escape(stripped[2:].strip()) + "</li>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(
                "<p style=\"margin:0 0 10px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#e2e8f0;font-size:13px;line-height:20px;\">"
                + escape(stripped)
                + "</p>"
            )
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def _load_editorial_html() -> str:
    """Return rendered HTML for the editorial section if available."""
    try:
        content = EDITORIAL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    return _markdown_to_html(content)


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
    tz = _newsletter_timezone()
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"])
        except Exception as exc:  # pragma: no cover - network edge cases
            logger.warning("Failed parsing %s: %s", feed.get("name", "Unknown"), exc)
            continue
        for entry in parsed.entries[:per_feed]:
            if not in_last_hours(entry, hours):
                continue
            title = (entry.get("title") or "(no title)").strip()
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            published_dt = _entry_timestamp(entry)
            published_display = "Date unavailable"
            published_iso = ""
            if published_dt:
                published_local = published_dt.astimezone(tz)
                published_display = published_local.strftime("%d %b %Y")
                published_iso = published_local.isoformat()
            notes = (feed.get("notes") or "").lower()
            paywalled = any(keyword in notes for keyword in ("paywall", "paywalled", "subscription"))
            items.append(
                {
                    "source": feed.get("name", "Unnamed Feed"),
                    "title": title,
                    "link": link,
                    "published_display": published_display,
                    "published_iso": published_iso,
                    "paywalled": paywalled,
                }
            )
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


def _build_user_prompt(items: Sequence[dict], lookback_hours: int) -> str:
    window_text = _window_description(lookback_hours)
    lines = []
    for idx, item in enumerate(items, start=1):
        lines.append(
            "\n".join(
                (
                    f"{idx}. Source: {item['source']}",
                    f"   Title: {item['title']}",
                    f"   URL: {item['link']}",
                    f"   Published: {item.get('published_display', 'Date unavailable')}",
                    f"   Paywalled: {'yes' if item.get('paywalled') else 'no'}",
                )
            )
        )
    items_block = "\n".join(lines) if lines else "(no items provided)"
    return USER_PROMPT_TEMPLATE.format(
        window_description=window_text,
        items_block=items_block,
    )


def _extract_json_payload(raw: str) -> Dict[str, Any]:
    candidate = raw.strip()
    if not candidate:
        raise PerplexityError("Empty response content from Perplexity")
    fence_matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", candidate)
    if fence_matches:
        candidate = fence_matches[0].strip()
    if not candidate.startswith("{"):
        brace_match = re.search(r"\{[\s\S]*\}", candidate)
        if not brace_match:
            raise PerplexityError("Perplexity response did not contain JSON payload")
        candidate = brace_match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise PerplexityError(f"Failed to parse Perplexity JSON: {exc}") from exc


def _normalise_tags(raw_tags: Any) -> List[str]:
    tags: List[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if not isinstance(tag, str):
                continue
            slug = tag.strip().lower()
            if not slug:
                continue
            slug = slug.replace(" ", "-")
            tags.append(slug)
    return tags[:4] if tags else ["markets"]


def _sanitize_digest_payload(payload: Dict[str, Any], defaults: Sequence[dict]) -> Dict[str, Any]:
    raw_highlights = payload.get("highlights")
    highlights = [
        str(entry).strip().rstrip(".")
        for entry in raw_highlights or []
        if isinstance(entry, str) and entry.strip()
    ]
    if not highlights:
        highlights = [
            f"{item['source']}: {item['title']}" for item in defaults[:3]
        ]
    highlights = highlights[:6]

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    sanitised_items: List[Dict[str, Any]] = []
    for idx, base in enumerate(defaults):
        if idx >= 10:
            break
        candidate: Dict[str, Any] = {}
        if idx < len(raw_items) and isinstance(raw_items[idx], dict):
            candidate = raw_items[idx]
        title = str(candidate.get("title") or base["title"]).strip()
        url = str(candidate.get("url") or base["link"]).strip()
        source = str(candidate.get("source") or base["source"]).strip()
        summary = str(candidate.get("summary") or "Headline only; see source link.").strip()
        market_impact = str(
            candidate.get("market_impact")
            or "Analyse direct source; market impact commentary unavailable this week."
        ).strip()
        action = str(
            candidate.get("action")
            or "Review the linked piece and note implications for your coverage list."
        ).strip()
        tags = _normalise_tags(candidate.get("tags"))
        sanitised_items.append(
            {
                "title": title,
                "url": url or base["link"],
                "source": source or base["source"],
                "summary": summary,
                "market_impact": market_impact,
                "action": action,
                "tags": tags,
                "paywalled": bool(base.get("paywalled", False)),
                "published_display": base.get("published_display", "Date unavailable"),
                "published_iso": base.get("published_iso", ""),
            }
        )

    return {"highlights": highlights, "items": sanitised_items}


def _build_fallback_item(item: dict) -> Dict[str, Any]:
    return {
        "title": item["title"],
        "url": item["link"],
        "source": item["source"],
        "summary": "Perplexity unavailable; sharing headline details only.",
        "market_impact": "Review primary source to assess potential portfolio impact.",
        "action": "Scan the linked piece and flag follow-ups during Monday's stand-up.",
        "tags": ["headline"],
        "paywalled": bool(item.get("paywalled", False)),
        "published_display": item.get("published_display", "Date unavailable"),
        "published_iso": item.get("published_iso", ""),
    }


def build_fallback_digest(items: Sequence[dict]) -> Dict[str, Any]:
    highlights = [
        f"{item['source']}: {item['title']}" for item in items[:3]
    ] or ["Quiet tape across tracked feeds"]
    fallback_items = [_build_fallback_item(item) for item in items]
    return {"highlights": highlights, "items": fallback_items}


def summarize_with_perplexity(
    items: Sequence[dict], lookback_hours: int
) -> tuple[Dict[str, Any], Dict[str, int]]:
    """Use Perplexity to build structured digest data."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise PerplexityError("PERPLEXITY_API_KEY is not set")
    base_url, model, timeout = _perplexity_config()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    user_prompt = _build_user_prompt(items, lookback_hours)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
    except requests.RequestException as exc:
        raise PerplexityError(str(exc)) from exc

    data = response.json()
    choices = data.get("choices")
    if not choices:
        raise PerplexityError("Perplexity response contained no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise PerplexityError("Perplexity response missing summary content")
    payload_json = _extract_json_payload(content)
    digest = _sanitize_digest_payload(payload_json, items)
    usage_summary = _token_usage_payload(data.get("usage"))
    return digest, usage_summary


def _window_description(hours: int) -> str:
    """Return a human-friendly description of the look-back window."""
    if hours % 24 == 0:
        days = hours // 24
        if days == 1:
            return "the last 24 hours"
        return f"the last {days} days"
    return f"the last {hours} hours"


def _newsletter_timezone() -> ZoneInfo:
    tz_name = os.environ.get("DIGEST_TZ", "Europe/Paris")
    try:
        return ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - invalid tz fallback
        logger.warning("Invalid DIGEST_TZ %s; defaulting to UTC", tz_name)
        return ZoneInfo("UTC")


def _format_time_range(now: datetime, lookback_hours: int) -> tuple[str, str]:
    period_start = now - timedelta(hours=lookback_hours)
    if lookback_hours >= 24:
        week_range = f"{period_start.strftime('%d %b %Y')} - {now.strftime('%d %b %Y')}"
        heading = f"Week of {week_range}"
    else:
        heading = now.strftime("%A, %d %b %Y")
        week_range = heading
    return heading, week_range


def _render_tag_badges(tags: List[str]) -> str:
    badges = []
    for tag in tags:
        badges.append(
            f"<span style=\"display:inline-block;background:#1f2937;color:#93c5fd;"
            f"font-size:11px;font-weight:700;padding:2px 10px;border-radius:999px;"
            f"margin-right:6px;margin-bottom:4px;\">{escape(tag)}</span>"
        )
    return "".join(badges)


def _render_item_card(index: int, item: Dict[str, Any]) -> str:
    title = escape(item["title"])
    url = escape(item["url"], quote=True)
    summary = escape(item["summary"])
    source = escape(item.get("source", ""))
    market_impact = escape(item["market_impact"])
    action = escape(item["action"])
    tags_html = _render_tag_badges(item.get("tags", []))
    published = escape(item.get("published_display", "Date unavailable"))
    paywall_label = ""
    if item.get("paywalled"):
        paywall_label = (
            "<span style=\"display:inline-block;background:#7c2d12;color:#fed7aa;"
            "font-size:11px;font-weight:700;padding:2px 10px;border-radius:999px;"
            "margin-left:8px;\">Paywalled</span>"
        )
    return f"""
      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin:8px 0;background:#0f1b34;border:1px solid #122041;border-radius:12px;\">
        <tr><td style=\"padding:16px 18px;\">
          <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#64748b;font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:700;margin-bottom:6px;\">#{index:02d}</div>
          <a href=\"{url}\" style=\"text-decoration:none;\">
            <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#e5f3ff;font-size:16px;line-height:22px;font-weight:700;margin:0 0 6px 0;\">
              {title}
            </div>
          </a>
          <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#9fb3c8;font-size:13px;line-height:18px;margin-bottom:10px;\">
            {summary}
          </div>
          <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#a5b4fc;font-size:12px;line-height:18px;margin-bottom:8px;\">
            {published} · {source}{paywall_label}
          </div>
          <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#60a5fa;font-size:12px;line-height:18px;margin-bottom:6px;\">
            Market impact: {market_impact}
          </div>
          <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#38bdf8;font-size:12px;line-height:18px;margin-bottom:8px;\">
            How to use it: {action}
          </div>
          <div style=\"margin-bottom:2px;\">
            {tags_html}
          </div>
        </td></tr>
      </table>
    """.strip()


def _render_empty_card(message: str) -> str:
    safe_message = escape(message)
    return f"""
      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin:8px 0;background:#0f1b34;border:1px dashed #1d2a44;border-radius:12px;\">
        <tr><td style=\"padding:16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#9fb3c8;font-size:13px;line-height:18px;\">
          {safe_message}
        </td></tr>
      </table>
    """.strip()


def render_email(
    digest: Dict[str, Any],
    lookback_hours: int,
    used_fallback: bool = False,
) -> str:
    """Build the HTML email body using the branded template."""
    now = datetime.now(_newsletter_timezone())
    week_number = now.isocalendar().week
    heading = f"🌊 Report - week {week_number:02d}"
    highlights = digest.get("highlights", [])
    if not highlights:
        highlights = ["Fresh intelligence from across the desk"]
    article_items = digest.get("items", [])[:10]
    article_count = len(article_items)
    highlight_sentence = (
        f"{article_count} article(s) curated this week. Highlights: "
        + ", ".join(escape(h) for h in highlights[:3])
        + "."
    )
    fallback_notice = ""
    if used_fallback:
        fallback_notice = (
            '<tr><td style="padding:8px 24px 0 24px;">'
            '<p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:#fbbf24;font-size:12px;line-height:18px;">'
            "Perplexity summaries unavailable; serving curated headlines and manual notes." "</p></td></tr>"
        )

    articles_html = "".join(
        _render_item_card(idx + 1, item) for idx, item in enumerate(article_items)
    )
    if not articles_html:
        articles_html = _render_empty_card("No items cleared editorial review for this cycle.")

    editorial_html = _load_editorial_html()
    editorial_section = ""
    if editorial_html:
        editorial_section = (
            '<tr><td style="padding:18px 24px 0 24px;">'
            '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:#93c5fd;font-weight:800;letter-spacing:.02em;margin:0 0 6px 0;">Editorial</div>'
            f'<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:#e2e8f0;font-size:13px;line-height:20px;">{editorial_html}</div>'
            "</td></tr>"
        )

    archive_url = escape(os.environ.get("ARCHIVE_URL", "#"), quote=True)
    manage_url = escape(os.environ.get("MANAGE_TOPICS_URL", "#"), quote=True)
    unsubscribe_url = escape(os.environ.get("UNSUBSCRIBE_URL", "#"), quote=True)
    sender_name = escape(os.environ.get("SENDER_NAME", "Wayve"))
    sender_address = escape(os.environ.get("SENDER_ADDRESS", "Paris, France"))
    send_time = escape(now.strftime("%H:%M %Z"))
    year = escape(str(now.year))

    manage_cta = ""
    if manage_url != "#":
        manage_cta = (
            f'<a href="{manage_url}" style="display:inline-block;margin-left:8px;background:#1f2937;color:#e5e7eb;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;font-size:12px;font-weight:800;padding:10px 14px;border-radius:999px;border:1px solid #374151;">Manage topics</a>'
        )
    archive_cta = ""
    if archive_url != "#":
        archive_cta = (
            f'<a href="{archive_url}" style="display:inline-block;background:#1f2937;color:#e5e7eb;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;font-size:12px;font-weight:800;padding:10px 14px;border-radius:999px;border:1px solid #374151;">View archive</a>'
        )

    footer_ctas = ""
    if archive_cta or manage_cta:
        footer_ctas = f"{archive_cta}{manage_cta}"

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>Wayve weekly research brief 🌊</title>
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
  <meta name=\"x-apple-disable-message-reformatting\">
  <style>
    .preheader{{display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;mso-hide:all;}}
    @media (max-width:620px){{.container{{width:100%!important}}.stack{{display:block!important;width:100%!important}}.p-md{{padding:16px!important}}.title-xl{{font-size:22px!important;line-height:28px!important}}}}
  </style>
</head>
<body style=\"margin:0;padding:0;background:#0f172a;\">
  <div class=\"preheader\">Wayve's weekly brief on finance intel and tucked-away research notes.</div>

  <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" style=\"background:#0f172a;\">
    <tr>
      <td align=\"center\" style=\"padding:24px 12px;\">
        <table role=\"presentation\" class=\"container\" width=\"600\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" style=\"width:600px;max-width:600px;background:#0b1224;border-radius:16px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.35);\">
          <tr>
            <td style=\"background:linear-gradient(135deg,#0ea5e9,#22d3ee);padding:28px 24px;\">
              <table role=\"presentation\" width=\"100%\">
                <tr>
                  <td align=\"left\">
                    <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#01344a;font-size:12px;letter-spacing:.08em;text-transform:uppercase;font-weight:800;\">Wayve weekly research brief</div>
                    <div class=\"title-xl\" style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#001a29;font-size:26px;line-height:32px;font-weight:800;margin-top:6px;\">
                      {escape(heading)}
                    </div>
                    <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#063142;font-size:14px;line-height:20px;margin-top:6px;\">
                      A 5-minute skim tracking what didn't make the Bloomberg homepage.
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td class=\"p-md\" style=\"padding:18px 24px 6px 24px;\">
              <p style=\"margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#e2e8f0;font-size:14px;line-height:22px;\">
                {highlight_sentence}
              </p>
            </td>
          </tr>
          {fallback_notice}
          {editorial_section}
          <tr>
            <td style=\"padding:12px 24px 8px 24px;\">
              <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#93c5fd;font-weight:800;letter-spacing:.02em;margin:8px 0 6px 0;\">Market intelligence</div>
            </td>
          </tr>
          <tr>
            <td style=\"padding:0 12px 8px 12px;\">
              {articles_html}
            </td>
          </tr>

          <tr>
            <td style=\"padding:8px 24px 20px 24px;\">
              <table role=\"presentation\" width=\"100%\">
                {f'<tr><td class="stack" style="padding:10px 0;">{footer_ctas}</td></tr>' if footer_ctas else ''}
                <tr>
                  <td style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#9ca3af;font-size:11px;line-height:17px;\">
                    Sent from Paris at ~{send_time}. Some links may require a subscription.
                    <br><br>
                    © {year} {sender_name} · {sender_address} · <a href=\"{unsubscribe_url}\" style=\"color:#a5b4fc;text-decoration:none;\">Unsubscribe</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def send_email(html_body: str, subject: str) -> int:
    """Send the digest via Gmail SMTP and return recipient count."""
    import smtplib
    from email.mime.text import MIMEText
    from email.utils import formatdate

    message = MIMEText(html_body, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = os.environ["MAIL_FROM"]
    recipients = _mail_recipients()
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP_SSL(os.environ["SMTP_SERVER"], int(os.environ["SMTP_PORT"])) as smtp:
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            smtp.send_message(message, to_addrs=recipients)
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Failed to send email: {exc}") from exc
    return len(recipients)


def _report_delivery(recipients: int, token_usage: Dict[str, int]) -> None:
    """Log and print a concise delivery summary."""
    summary = (
        f"Sent weekly brief to {recipients} recipient(s); "
        f"Perplexity tokens — prompt: {token_usage.get('prompt', 0)}, "
        f"completion: {token_usage.get('completion', 0)}, "
        f"total: {token_usage.get('total', 0)}"
    )
    logger.info(summary)
    print(summary)

def build_digest_payload(
    items: Sequence[dict], lookback_hours: int
) -> tuple[Dict[str, Any], bool, Dict[str, int]]:
    try:
        digest, usage = summarize_with_perplexity(items, lookback_hours)
        return digest, False, usage
    except PerplexityError as exc:
        logger.warning("Perplexity summarisation failed: %s", exc)
        return build_fallback_digest(items), True, {"prompt": 0, "completion": 0, "total": 0}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compile and email a weekly finance digest.")
    parser.add_argument("--csv", required=True, help="CSV file containing feed metadata")
    parser.add_argument(
        "--hours",
        type=int,
        default=168,
        help="Look-back window for feed entries",
    )
    parser.add_argument("--topn", type=int, default=10, help="Number of entries to include in the digest")
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
        now_local = datetime.now(_newsletter_timezone())
        quiet_digest = {
            "highlights": [
                f"No tracked updates in {_window_description(args.hours)}"
            ],
            "items": [
                {
                    "title": "No fresh items found",
                    "url": "#",
                    "source": "Wayve Monitor",
                    "summary": "Feeds were quiet; we'll resume next week with fresh intelligence.",
                    "market_impact": "No immediate market-moving headlines detected across monitored sources.",
                    "action": "Use the lull to review positioning and backlog research tasks.",
                    "tags": ["quiet-week"],
                    "paywalled": False,
                    "published_display": now_local.strftime("%d %b %Y"),
                    "published_iso": now_local.isoformat(),
                }
            ],
        }
        body = render_email(quiet_digest, args.hours, used_fallback=True)
        week_number = now_local.isocalendar().week
        subject = f"Weekly Digest — Week {week_number:02d} (no new items)"
        sent = send_email(body, subject)
        _report_delivery(sent, {"prompt": 0, "completion": 0, "total": 0})
        return

    max_articles = min(args.topn, 10)
    selected = items[:max_articles]
    digest, used_fallback, usage = build_digest_payload(selected, args.hours)
    body = render_email(digest, args.hours, used_fallback=used_fallback)
    now_local = datetime.now(_newsletter_timezone())
    week_number = now_local.isocalendar().week
    subject = f"Weekly Digest — Week {week_number:02d}"
    if used_fallback:
        subject += " (headlines)"
    sent = send_email(body, subject)
    _report_delivery(sent, usage)


if __name__ == "__main__":
    main()
