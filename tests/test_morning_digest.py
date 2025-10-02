import os
from datetime import datetime

import morning_digest as md


def _write_members_csv(path, rows):
    path.write_text("email,name\n" + "\n".join(rows), encoding="utf-8")
    return path


def _write_feeds_csv(path, rows):
    path.write_text("name,rss_url,notes\n" + "\n".join(rows), encoding="utf-8")
    return path


def test_load_member_emails_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "BASE_DIR", tmp_path)
    members_path = _write_members_csv(
        tmp_path / "roster.csv",
        [
            "alpha@example.com,Alpha",
            "",
            "ALPHA@example.com,Duplicate",
            "beta@example.com,Beta",
        ],
    )

    recipients = md._load_member_emails("roster.csv")

    assert recipients == ["alpha@example.com", "beta@example.com"]


def test_mail_recipients_combines_csv_and_env(tmp_path, monkeypatch):
    members_path = _write_members_csv(
        tmp_path / "members.csv",
        [
            "one@example.com,One",
            "two@example.com,Two",
        ],
    )
    monkeypatch.setenv("MAIL_TO", "two@example.com, extra@example.com ,")

    recipients = md._mail_recipients(str(members_path))

    assert recipients == ["one@example.com", "two@example.com", "extra@example.com"]


def test_main_sends_digest_with_mocked_dependencies(tmp_path, monkeypatch):
    feeds_csv = _write_feeds_csv(
        tmp_path / "feeds.csv",
        ["Feed One,https://example.com/feed.xml,notes"],
    )
    members_csv = _write_members_csv(
        tmp_path / "recipients.csv",
        [
            "alpha@example.com,Alpha",
            "beta@example.com,Beta",
        ],
    )
    monkeypatch.setenv("MAIL_TO", "gamma@example.com")

    items = [
        {
            "title": "Interesting headline",
            "link": "https://example.com/article",
            "source": "Feed One",
            "published_display": "01 Jan 2025",
            "published_iso": datetime.utcnow().isoformat(),
            "paywalled": False,
        }
    ]

    monkeypatch.setattr(md, "fetch_items", lambda *args, **kwargs: items)
    monkeypatch.setattr(md, "summarize_with_perplexity", lambda *args, **kwargs: (
        {
            "highlights": ["Highlight"],
            "items": [
                {
                    "title": "Interesting headline",
                    "url": "https://example.com/article",
                    "source": "Feed One",
                    "summary": "Summary",
                    "market_impact": "Impact",
                    "action": "Action",
                    "tags": ["tag"],
                    "paywalled": False,
                    "published_display": "01 Jan 2025",
                    "published_iso": datetime.utcnow().isoformat(),
                }
            ],
        },
        {"prompt": 10, "completion": 5, "total": 15},
    ))

    captured = {}

    def fake_send_email(html_body, subject, recipients):
        captured["html"] = html_body
        captured["subject"] = subject
        captured["recipients"] = list(recipients)
        return len(recipients)

    monkeypatch.setattr(md, "send_email", fake_send_email)
    monkeypatch.setattr(md, "_report_delivery", lambda *args, **kwargs: None)

    md.main([
        "--csv",
        str(feeds_csv),
        "--topn",
        "5",
        "--hours",
        "24",
        "--members-csv",
        str(members_csv),
    ])

    assert captured["recipients"] == [
        "alpha@example.com",
        "beta@example.com",
        "gamma@example.com",
    ]
    assert captured["subject"].startswith("Weekly Digest — Week ")
    assert "Interesting headline" in captured["html"]
