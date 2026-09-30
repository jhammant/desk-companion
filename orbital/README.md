# Optional: build the cards with Orbital

The display never needs Orbital. By default an agent writes `cards/<date>.json`
(see [`PROMPT.md`](../PROMPT.md)). This folder is for when the facts on a card
live in several systems and you'd rather have [Orbital](https://orbitalhq.com)
join them than have an agent search each one.

## How it works

1. **[`vocabulary/src/desk.taxi`](vocabulary/src/desk.taxi)** is the vocabulary a card
   needs, in [Taxi](https://taxilang.org): `desk.EmailAddress`, `desk.CompanyName`,
   `desk.LastMeeting`, `desk.OpenCommitment` and so on.
2. **You map your systems onto it.** Tag your CRM's contact email as
   `desk.EmailAddress`, its company as `desk.CompanyName`, your notes API's meeting
   record as `desk.LastMeeting`. [`demo/src/demo-services.taxi`](demo/src/demo-services.taxi)
   shows the mapping for two made-up systems.
3. **[`prep_card.taxiql`](prep_card.taxiql)** asks for one attendee's card facts by type.
   Orbital works out which systems to call and joins them: the contact from the
   email, the last meeting from the email, the open commitments from the company.
4. **[`orbital_cards.py`](../orbital_cards.py)** runs that query for each of today's
   meetings and writes the facts into the cards. It only fills empty fields, so
   your agent's `why` and `goal` are kept, and it does nothing unless
   `DESK_ORBITAL_URL` is set. Standard library only.

```bash
DESK_ORBITAL_URL=http://localhost:9022 python3 orbital_cards.py            # today
DESK_ORBITAL_URL=http://localhost:9022 python3 orbital_cards.py --dry-run  # print, don't write
```

Run it before your agent (or instead of the agent's lookups), for example every
30 minutes through the working day.

## Try it with the sample data

```bash
./orbital/demo/try.sh
```

Starts a throwaway Orbital (Docker) with the vocabulary and the demo services,
a mock CRM and notes server with the sample data, runs the query and a dry run
of `orbital_cards.py`, then removes everything it started.

## Using your own Orbital

Add `vocabulary/` as a project in your Orbital workspace (or copy `desk.taxi`
into an existing project), map your services onto the `desk.*` types, then point
`DESK_ORBITAL_URL` at it. To ask differently, for example through types you
already have, write your own query with the same output fields (`name`,
`company`, `lastMet`, `lastSummary`, `open[].text`) and set `DESK_ORBITAL_QUERY`
to its path.
