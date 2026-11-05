"""Discord webhook reporting for the level checker.

The webhook URL is a secret, so it is never written into the source. It is read
from the DISCORD_WEBHOOK_URL environment variable or, when that is not set,
from a git-ignored ``.env`` file sitting next to this script:

    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>

A missing webhook only skips the notification - the checker itself keeps
working and still writes all of its result files.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

MAX_CONTENT = 1900      # Discord's hard limit is 2000 characters per message
SEND_INTERVAL = 1.2     # pause between messages to stay clear of rate limits

# (category written by app.py, label used in Discord)
TIERS = [
    ("22+ Lvl", "✅ 22+ Level"),
    ("20-21 Lvl", "⚡ 20-21 Level"),
    ("8-19 Lvl", "⭐ 8-19 Level"),
    ("< 8 Lvl", "🔻 Below 8 Level"),
]

SUMMARY_COLOR = 0x9B59B6


def _read_env_file(path):
    """Tiny KEY=VALUE reader so .env support needs no extra dependency."""
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def get_webhook_url():
    """Return the configured webhook URL, or None when it is not set."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        url = _read_env_file(ENV_FILE).get("DISCORD_WEBHOOK_URL", "").strip()
    return url or None


def _post(url, payload):
    """POST one payload, retrying once when Discord rate limits us."""
    for attempt in (1, 2):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=15,
                headers={"User-Agent": "GuestLevelChecker/4.0"},
            )
        except Exception as exc:
            return False, f"Net Error: {exc}"

        if response.status_code in (200, 204):
            return True, None
        if response.status_code == 429 and attempt == 1:
            try:
                wait = float(response.json().get("retry_after", 1))
            except Exception:
                wait = 1.0
            time.sleep(min(wait, 5))
            continue
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    return False, "Rate limited by Discord"


def _account_line(account):
    return (
        f"`{account['account_id']}` **{account['nickname']}** — "
        f"Lvl {account['level']} · {account['region']}"
    )


def _split_messages(header, lines):
    """Split account lines into messages that fit Discord's size limit."""
    messages = []
    current = header + "\n"
    for line in lines:
        if len(current) + len(line) + 1 > MAX_CONTENT:
            messages.append(current.rstrip())
            current = header + "\n"
        current += line + "\n"
    if current.strip() != header.strip():
        messages.append(current.rstrip())
    return messages


def send_report(accounts, counts, total, failed=0):
    """Post the run summary plus the valid accounts to the Discord webhook.

    ``accounts`` are the successful result dicts from app.py, ``counts`` maps a
    tier category to how many accounts landed in it, ``total`` is how many
    accounts were checked and ``failed`` how many failed.

    Returns ``(ok, error_message)``.
    """
    url = get_webhook_url()
    if not url:
        return False, "DISCORD_WEBHOOK_URL set kora hoyni (.env file ba env var)"

    embed = {
        "title": "🎮 Guest Level Check Complete",
        "color": SUMMARY_COLOR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "AYAN CODEX · VIP Multi-Tier Level Filter v4.0"},
        "fields": [
            {"name": "Checked", "value": str(total), "inline": True},
            {"name": "Valid", "value": str(len(accounts)), "inline": True},
            {"name": "Failed", "value": str(failed), "inline": True},
        ]
        + [
            {"name": label, "value": str(counts.get(category, 0)), "inline": True}
            for category, label in TIERS
        ],
    }

    ok, error = _post(url, {"embeds": [embed], "allowed_mentions": {"parse": []}})
    if not ok:
        return False, error

    messages = []
    for category, label in TIERS:
        tier_accounts = [acc for acc in accounts if acc.get("category") == category]
        if not tier_accounts:
            continue
        header = f"{label} ({len(tier_accounts)})"
        messages.extend(_split_messages(header, [_account_line(acc) for acc in tier_accounts]))

    for message in messages:
        time.sleep(SEND_INTERVAL)
        ok, error = _post(url, {"content": message, "allowed_mentions": {"parse": []}})
        if not ok:
            return False, error

    return True, None
