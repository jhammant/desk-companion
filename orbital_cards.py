#!/usr/bin/env python3
"""orbital_cards.py: optional. Fill today's meeting cards with facts joined by Orbital.

    DESK_ORBITAL_URL=http://localhost:9022 python3 orbital_cards.py [--date YYYY-MM-DD] [--dry-run] [--overwrite]

Does nothing unless DESK_ORBITAL_URL is set: the display never needs Orbital.
For each of the day's meetings (the same ones desk_meetings.py lists), it takes
the first outside attendee, runs orbital/prep_card.taxiql (or DESK_ORBITAL_QUERY)
against Orbital's /api/taxiql, and writes who / last / open into
cards/<date>.json. Orbital answers by joining whatever systems you have mapped
onto the desk.* types in orbital/vocabulary (a CRM, notes, mail ...).

It only fills fields that are empty, so the judgement your agent wrote (why,
goal) and any facts it already found are kept. --overwrite lets Orbital's facts
replace the agent's who / last / open. Standard library only.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from desk_data import CARDS_DIR, TZ, company, load_calendar, load_people, meetings_on

HERE = Path(__file__).resolve().parent
QUERY = Path(os.environ.get("DESK_ORBITAL_QUERY", HERE / "orbital" / "prep_card.taxiql"))
# Conservative on purpose: the address is spliced into a TaxiQL string literal.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def build_query(template, email):
    if not EMAIL.fullmatch(email):
        raise ValueError(f"not a plain email address: {email!r}")
    return template.replace("{{email}}", email)


def run_query(url, taxiql, timeout=60):
    """POST TaxiQL to Orbital; the projected object, or None when Orbital found nothing."""
    req = urllib.request.Request(url.rstrip("/") + "/api/taxiql", data=taxiql.encode(),
                                 headers={"Content-Type": "application/taxiql", "Accept": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace").strip()
    if not body:
        return None
    data = json.loads(body)
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def short_date(iso):
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%-d %b")
    except ValueError:
        return ""


def facts_to_card(facts, others):
    """Card fields from Orbital's answer. others: how many more outside people are in the meeting."""
    card = {}
    name, org = facts.get("name"), facts.get("company")
    if name:
        card["who"] = (f"{name} ({org})" if org else name) + (f", +{others}" if others else "")
    if facts.get("lastSummary"):
        when = short_date(facts.get("lastMet"))
        card["last"] = f"{when}: {facts['lastSummary']}" if when else facts["lastSummary"]
    items = [o.get("text") for o in facts.get("open") or [] if isinstance(o, dict) and o.get("text")]
    if items:
        card["open"] = items[:3]
    return card


def merge(existing, facts, overwrite=False):
    """Orbital's facts into a card, without touching what the agent wrote unless asked."""
    out = dict(existing)
    for key, value in facts.items():
        if overwrite or not out.get(key):
            out[key] = value
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="the day to fill, YYYY-MM-DD (default today)")
    ap.add_argument("--dry-run", action="store_true", help="print the cards instead of writing them")
    ap.add_argument("--overwrite", action="store_true", help="let Orbital's facts replace the agent's")
    args = ap.parse_args(argv)

    url = os.environ.get("DESK_ORBITAL_URL", "")
    if not url:
        print("DESK_ORBITAL_URL is not set; nothing to do (Orbital is optional).")
        return 0
    day = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()
    template = QUERY.read_text()
    events, _ = load_calendar()
    names, me = load_people()

    path = CARDS_DIR / f"{day.isoformat()}.json"
    try:
        doc = json.loads(path.read_text())
    except Exception:
        doc = {"date": day.isoformat(), "cards": []}
    cards = {c.get("start"): c for c in doc.get("cards") or [] if isinstance(c, dict)}

    stamp = datetime.now(TZ).strftime("%H:%M")
    filled = 0
    for m in meetings_on(events, day, me):
        outside = [a for a in m["attendees"] if a not in me and company(a)]
        if not outside:
            continue
        start = f"{m['start']:%H:%M}"
        try:
            facts = run_query(url, build_query(template, outside[0]))
        except (ValueError, urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            detail = e.read().decode("utf-8", "replace")[:300] if isinstance(e, urllib.error.HTTPError) else e
            print(f"{start} {m['title']}: skipped ({detail})", file=sys.stderr)
            continue
        if not facts:
            print(f"{start} {m['title']}: Orbital found nothing for {outside[0]}")
            continue
        card = merge(cards.get(start) or {"start": start, "title": m["title"]},
                     facts_to_card(facts, len(outside) - 1), args.overwrite)
        card.setdefault("prepared", f"facts joined by Orbital at {stamp}")
        cards[start] = card
        filled += 1
        print(f"{start} {m['title']}: {', '.join(k for k in ('who', 'last', 'open') if k in card)}")

    doc["cards"] = sorted(cards.values(), key=lambda c: c.get("start") or "")
    if args.dry_run:
        print(json.dumps(doc, indent=1))
    elif filled:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(path)
        print(f"wrote {filled} card(s) to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
