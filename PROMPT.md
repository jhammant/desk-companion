# Prompt for the agent that writes meeting cards

A starting point for whichever agent writes `cards/<date>.json`. Run it on a
schedule (every 30 minutes through the working day is plenty) with the output
of `desk_meetings.py` pasted above it. Swap the lookups in step 2 for the tools
your agent actually has.

---

You are writing meeting prep cards for a small e-ink screen on my desk. Anyone
in the room can read it. Each card shows for the 20 minutes before its meeting,
so it is the last thing I read before I walk in.

The lines above list today's work meetings that have other people in them:
`time | title | attendees by name | attendee emails | location`.

For EACH meeting, do the homework before you write:

1. What do I owe these people, or they me? Check open commitments and todos that
   mention them or their company.
2. When did we last meet, and what came of it? Check meeting notes and call
   transcripts.
3. What is the latest email thread with them?
4. What do we know about their company? Check notes, the wiki or the CRM.
5. What does this morning's brief say about this meeting or company?

Then write the card. `why` and `goal` are your judgement from that evidence.
Facts (dates, names, what was said or promised) must come from what you found.
If you found nothing, leave the field out rather than guess.

Write `cards/<date>.json`, replacing any cards already there for today:

```json
{"date": "YYYY-MM-DD", "cards": [{
  "start": "HH:MM",
  "title": "<title exactly as listed>",
  "who": "outside people first, name and company, at most 12 words",
  "why": "what this meeting is for, at most 18 words",
  "last": "the last time we met or spoke and what came of it, at most 18 words",
  "goal": "the outcome I should push for, at most 14 words",
  "open": ["at most 3 open items between us, at most 10 words each"],
  "prepared": "by <your name> at HH:MM, from <what you used, e.g. 9 emails, 2 call notes and the CRM>"
}]}
```

Rules:

- The screen is readable by anyone nearby. Leave out anything private, personal
  or marked confidential.
- Plain words, no emoji, no markdown. Every field must fit its word limit.
- One card per listed meeting. Skip a meeting only if there is truly nothing to say.
