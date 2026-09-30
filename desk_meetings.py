#!/usr/bin/env python3
"""desk_meetings.py: today's meetings that deserve a prep card, as input for your agent.

Prints one line per work meeting with other people in it. The output has no
clock in it, so a scheduler that only wakes the agent when the output changes
(a new day, a meeting added or moved) will not re-run it for nothing. Give the
output to your agent with PROMPT.md and have it write cards/<date>.json.
"""
from datetime import datetime

from desk_data import CARDS_DIR, TZ, load_calendar, load_people, meetings_on, who

today = datetime.now(TZ).date()
events, _ = load_calendar()
names, me = load_people()
meetings = meetings_on(events, today, me)
print(f"DESK MEETINGS for {today.isoformat()} (write cards to {CARDS_DIR / f'{today.isoformat()}.json'})")
for e in meetings:
    print(f"{e['start']:%H:%M}-{e['end']:%H:%M} | {e['title']} | {who(e['attendees'], names, me, limit=8)}"
          f" | {', '.join(a for a in e['attendees'] if a not in me)} | {e['where'] or 'no location'}")
if not meetings:
    print("none")
