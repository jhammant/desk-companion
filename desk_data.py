"""desk_data.py: calendar, people and text helpers shared by the desk display scripts.

Imported by desk_companion.py (the renderer) and desk_meetings.py (the list of
meetings your agent writes prep cards for), so both agree on what counts as a
meeting. Standard library only.

Configuration is by environment variable; see the README for the full list.
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("DESK_TZ", "Europe/London"))
DATA = Path(os.environ.get("DESK_DATA", "data"))
CAL = Path(os.environ.get("DESK_CAL", DATA / "calendar.json"))
PEOPLE = Path(os.environ.get("DESK_PEOPLE", DATA / "people.json"))
CARDS_DIR = Path(os.environ.get("DESK_CARDS_DIR", DATA / "cards"))


def _csv(name, default=""):
    return tuple(s.strip().lower() for s in os.environ.get(name, default).split(",") if s.strip())


# Calendars and addresses on these domains are "work" (blue); their people are colleagues.
WORK_DOMAINS = _csv("DESK_WORK_DOMAINS")
# Accounts or addresses whose calendars are "family" (orange, yellow on Spectra 6).
FAMILY_ACCOUNTS = _csv("DESK_FAMILY_ACCOUNTS")
# Your own addresses, used when there is no people index to find them in.
ME_EMAILS = _csv("DESK_ME_EMAILS")

# When one event sits in several calendars, keep the most personal copy
# (a gym session mirrored into the work calendar is still your own).
CATEGORY_RANK = {"me": 0, "work": 1, "family": 2, "holiday": 3}

# SoftBank-era emoji that iCloud keeps in the private-use area.
PUA_HEARTS = {"", "", "", "", "", "", "", ""}


def clean(text):
    """Printable text only: hearts kept as ♥, other emoji/private-use dropped."""
    out = []
    for ch in str(text or ""):
        if ch in PUA_HEARTS:
            out.append("♥")
        elif unicodedata.category(ch) in ("Co", "So", "Cs", "Cn") and ch != "♥":
            continue
        else:
            out.append(ch)
    text = "".join(out).replace("“", "").replace("”", "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


def _is_work(text):
    return any(d in text for d in WORK_DOMAINS)


def category(ev):
    name = (ev.get("calendar") or "").lower()
    account = (ev.get("account") or "").lower()
    address = (ev.get("account_address") or "").lower()
    if "holiday" in name or account == "subscribed calendars":
        return "holiday"
    if _is_work(account) or _is_work(address):
        return "work"
    if any(f in (account, address) for f in FAMILY_ACCOUNTS):
        return "family"
    return "me"


def load_calendar():
    """(events, fetched_at) with events de-duplicated across calendars."""
    try:
        data = json.loads(CAL.read_text())
    except Exception:
        return None, None
    best = {}
    for cal in (data.get("calendars") or {}).values():
        for ev in cal.get("events") or []:
            try:
                start = datetime.fromisoformat(ev["start"]).astimezone(TZ)
                end = datetime.fromisoformat(ev["end"]).astimezone(TZ)
            except Exception:
                continue
            title = clean(ev.get("title")) or "(untitled)"
            item = {"title": title, "start": start, "end": end, "all_day": bool(ev.get("all_day")),
                    "cat": category(ev), "where": re.sub(r"[\s,\-–—]+$", "", clean(ev.get("location"))),
                    "attendees": [a.lower() for a in ev.get("attendees") or []]}
            # Same start and same first three words = one event copied between calendars.
            key = (start.isoformat(), " ".join(re.findall(r"[a-z0-9]+", title.lower())[:3]))
            prev = best.get(key)
            if prev:  # merge: most personal colour, most descriptive title, any location/attendees
                keep = item if CATEGORY_RANK[item["cat"]] < CATEGORY_RANK[prev["cat"]] else prev
                keep["title"] = max(prev["title"], title, key=len)
                keep["where"] = keep["where"] or prev["where"] or item["where"]
                keep["attendees"] = keep["attendees"] or prev["attendees"] or item["attendees"]
                item = keep
            best[key] = item
    events = sorted(best.values(), key=lambda e: (not e["all_day"], e["start"], e["title"]))
    return events, data.get("fetched_at")


def events_on(events, day):
    lo = datetime.combine(day, datetime.min.time(), TZ)
    hi = lo + timedelta(days=1)
    out = []
    for e in events or []:
        if e["all_day"]:
            if e["start"].date() <= day < e["end"].date() or e["start"].date() == day:
                out.append(e)
        elif e["start"] < hi and e["end"] > lo:
            out.append(e)
    return out


def is_mine(e):
    return e["cat"] in ("me", "work")


def meetings_on(events, day, my_emails=()):
    """Work meetings with someone else in them: the ones worth a prep card.
    Not travel blocks, not solo holds, not all-day items."""
    out = []
    for e in events_on(events, day):
        if e["all_day"] or e["cat"] != "work" or e["start"].date() != day:
            continue
        if re.match(r"^(travel|hold|focus|block|lunch)\b", e["title"], re.I):
            continue
        others = [a for a in e["attendees"] if a not in my_emails]
        if others:
            out.append(e)
    return out


def load_people():
    """(email -> name, your own emails) from the people index.

    people.json: {"me": ["you@yourco.com"], "people": {"<id>": {"name": "...", "emails": ["..."]}}}"""
    try:
        data = json.loads(PEOPLE.read_text())
    except Exception:
        return {}, set(ME_EMAILS)
    names = {}
    for p in (data.get("people") or {}).values():
        for email in p.get("emails") or []:
            names[email.lower()] = p.get("name") or email
    me = {e.lower() for e in data.get("me") or []} | set(ME_EMAILS)
    return names, me


PERSONAL_DOMAINS = ("gmail.com", "icloud.com", "me.com", "hotmail.com", "outlook.com", "yahoo.com")


def person_name(email, names):
    """'Ada Lovelace' for ada@engines.example; the index may keep some names as 'Lovelace, Ada'."""
    name = names.get(email) or email.split("@")[0]
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        name = f"{first} {last}"
    return name


def company(email):
    """'Harbourline Freight' for priya@harbourline-freight.com; '' for colleagues and personal mail."""
    domain = email.split("@")[-1]
    if _is_work(domain) or domain in PERSONAL_DOMAINS:
        return ""
    return " ".join(part.capitalize() for part in domain.split(".")[0].split("-"))


def is_external(email):
    return bool(company(email))


def who(emails, names, my_emails, limit=4):
    """'Priya Shah (Harbourline Freight), Sam Patel, +2' from attendee emails."""
    parts = []
    others = [e for e in emails if e not in my_emails]
    for e in others[:limit]:
        name, org = person_name(e, names), company(e)
        parts.append(f"{name} ({org})" if org else name)
    if len(others) > limit:
        parts.append(f"+{len(others) - limit}")
    return ", ".join(parts)


def load_cards(day):
    """Your agent's prep cards for a day, keyed by 'HH:MM'."""
    try:
        data = json.loads((CARDS_DIR / f"{day.isoformat()}.json").read_text())
    except Exception:
        return {}
    return {c.get("start"): c for c in data.get("cards") or [] if isinstance(c, dict)}
