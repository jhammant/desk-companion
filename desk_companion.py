#!/usr/bin/env python3
"""desk_companion.py: a calm desk dashboard for a 7.3" colour e-ink panel (Pimoroni Inky Impression).

Renders one 800x480 PNG. Serve it over HTTP and point InkyPi's Image URL plugin at it
(https://github.com/fatihak/InkyPi); InkyPi redraws the panel only when the image changes.

The screen follows the day ("don't worry me at night"), and changes only at
planned moments, because every change is a 30-40 s full-panel colour flash:

  scenes   06:45 09:30 12:30 15:00 18:00 22:00 (weekends 07:30 12:30 15:00 18:00 22:00)
  meetings a prep card from 20 min before each work meeting with other people in it,
           back to the day 5 min after it starts
  Between those moments the image is frozen: weather, headline and todos are
  read once per moment and everything is drawn as of that moment.

  morning  06:45-09:30  today's meetings | Do this first, picks, Waiting on you
                        footer: world headline, jacket verdict, markets
  work     09:30-18:00  meetings, now/next highlighted | Priorities, Todos, Waiting on you
                        footer: world headline, GitHub stars, markets
  evening  18:00-22:00  rest of today (or tomorrow) | Wins today, picks to read
                        footer: tomorrow's weather. No todos, no markets, no warnings.
  card     meeting      who, why, last time, goal, open items (your agent's card for the meeting,
                        falling back to the brief's line and matching todos); footer: what comes
                        after, and "prepared" (who wrote the card, from what) when the card has it
  night    22:00-06:45  calm: first thing tomorrow and its weather. Nothing that changes,
                        so the panel never redraws overnight.
  weekend  07:30-18:00  family-first: the diary, weekend picks. No work todos or markets.

Inputs (all files, written by whatever you like; see the README for formats)
  calendar.json      every calendar, merged and de-duplicated: work / me / family
  people.json        names for attendee emails, and which addresses are yours
  cards/<date>.json  meeting prep cards, one per meeting, usually written by an AI agent
  brief/<date>*.md   the morning brief: Do this first, Priorities, Waiting on you,
                     Worth your attention, Markets, Weather
  todos.json         open todos, and the ones closed today
  Open-Meteo, an RSS feed and GitHub for weather, a world headline and a star count (cached)

Drawing rules for the panel
  - Only the panel's exact palette colours, blended at SATURATION, which must
    match image_settings.inky_saturation in InkyPi. The inky driver quantises
    with Floyd-Steinberg; exact colours leave it no error to diffuse.
  - Aliased text (fontmode "1"): anti-aliased edges would dither into speckle.
  - No clock and no relative times in the image. InkyPi only redraws when the
    image hash changes, so the picture stays identical until something on it
    really changes.

Run: desk_companion.py [width height] [--out PATH] [--now ISO] [--verbose]
"""
import argparse
import json
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from desk_data import (DATA, TZ, clean, company, events_on, is_external, is_mine, load_calendar, load_cards,
                       load_people, meetings_on, person_name, who)

HERE = Path(__file__).resolve().parent
BRIEF_DIR = Path(os.environ.get("DESK_BRIEF_DIR", DATA / "brief"))
WEEKEND_DIR = Path(os.environ.get("DESK_WEEKEND_DIR", DATA / "weekend"))
TODOS = Path(os.environ.get("DESK_TODOS", DATA / "todos.json"))
CACHE = Path(os.environ.get("DESK_CACHE", DATA / "cache.json"))
FONT_DIR = Path(os.environ.get("DESK_FONT_DIR", HERE / "fonts"))
OUT = os.environ.get("DESK_OUT", "desk.png")
LAT, LON = float(os.environ.get("DESK_LAT", "51.5074")), float(os.environ.get("DESK_LON", "-0.1278"))
HEADLINE_RSS = os.environ.get("DESK_HEADLINE_RSS", "https://feeds.bbci.co.uk/news/rss.xml")  # "" to turn off
GITHUB_USER = os.environ.get("DESK_GITHUB_USER", "")  # set to show your star count
AGENT = os.environ.get("DESK_AGENT_NAME", "")  # e.g. "Hunter" makes the picks heading "HUNTER'S PICKS"
PICKS_LABEL = f"{AGENT.upper()}'S PICKS" if AGENT else "PICKS"

# --- panel palette ---------------------------------------------------------
# Copied from the inky driver for the panel; the driver blends the two by saturation.
#   spectra6  Inky Impression 7.3" (2025, Spectra 6, inky_e673): no orange
#   acep7     Inky Impression 7.3" (2023, 7-colour ACeP, inky_ac073tc1a)
#   preview   clean screen colours for screenshots (see below)
PANEL = os.environ.get("DESK_PANEL", "spectra6")
SATURATION = float(os.environ.get("DESK_SATURATION", "0.8"))
PALETTES = {
    "spectra6": ({"black": (0, 0, 0), "white": (161, 164, 165), "yellow": (208, 190, 71),
                  "red": (156, 72, 75), "blue": (61, 59, 94), "green": (58, 91, 70)},
                 {"black": (0, 0, 0), "white": (255, 255, 255), "yellow": (255, 255, 0),
                  "red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 255, 0)}),
    "acep7": ({"black": (0, 0, 0), "white": (217, 242, 255), "green": (3, 124, 76), "blue": (27, 46, 198),
               "red": (245, 80, 34), "yellow": (255, 255, 68), "orange": (239, 121, 44)},
              {"black": (0, 0, 0), "white": (255, 255, 255), "green": (0, 255, 0), "blue": (0, 0, 255),
               "red": (255, 0, 0), "yellow": (255, 255, 0), "orange": (255, 140, 0)}),
}
# preview: the Spectra 6 colours as a screen shows them, for screenshots and browsers, not for a panel.
_PREVIEW = {"black": (0, 0, 0), "white": (255, 255, 255), "yellow": (236, 200, 30), "red": (200, 40, 40),
            "blue": (30, 50, 170), "green": (20, 125, 70)}
PALETTES["preview"] = (_PREVIEW, _PREVIEW)
if PANEL not in PALETTES:
    raise SystemExit(f"DESK_PANEL must be one of {', '.join(PALETTES)}, not {PANEL!r}")


def _ink(name):
    saturated, desaturated = PALETTES[PANEL]
    return tuple(int(s * SATURATION + d * (1.0 - SATURATION)) for s, d in zip(saturated[name], desaturated[name]))


BLACK, WHITE, GREEN, BLUE, RED, YELLOW = (_ink(n) for n in ("black", "white", "green", "blue", "red", "yellow"))
# Spectra 6 has no orange: warm text falls back to red, family diary bars to yellow.
ORANGE = _ink("orange") if PANEL == "acep7" else RED
FAMILY = _ink("orange") if PANEL == "acep7" else YELLOW
PALETTE = {BLACK, WHITE, GREEN, BLUE, RED, YELLOW, ORANGE, FAMILY}

CATEGORY_INK = {"work": BLUE, "me": GREEN, "family": FAMILY, "holiday": RED}

SCENES = {"weekday": ["06:45", "09:30", "12:30", "15:00", "18:00", "22:00"],
          "weekend": ["07:30", "12:30", "15:00", "18:00", "22:00"]}
# Render every 5 min and let InkyPi poll every 2, so a card opened 20 min out
# is on the panel 11-20 min before the meeting.
CARD_BEFORE, CARD_AFTER = timedelta(minutes=20), timedelta(minutes=5)

W, H = 800, 480
M = 20  # outer margin
TOP, BOTTOM = 82, 402  # body band between header and footer
SPLIT = 468  # left/right column divider


def font(bold, size):
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


F = {}


def load_fonts():
    F.update(
        date=font(True, 30), temp=font(True, 34), label=font(True, 14), small=font(False, 13),
        body=font(False, 17), body_b=font(True, 17), side=font(False, 16), side_b=font(True, 16),
        foot=font(False, 15), legend=font(False, 12), big=font(True, 26), calm=font(False, 22),
    )


# --- small helpers -----------------------------------------------------------

def fit(dr, text, fnt, maxw):
    """Truncate text with an ellipsis so it fits maxw pixels."""
    if dr.textlength(text, font=fnt) <= maxw:
        return text
    while text and dr.textlength(text + "…", font=fnt) > maxw:
        text = text[:-1]
    return text.rstrip(" ,;:-—·(") + "…"


def wrap(dr, text, fnt, maxw, max_lines):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if dr.textlength(trial, font=fnt) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(dr, lines[-1] + " …", fnt, maxw)
    return [fit(dr, ln, fnt, maxw) for ln in lines]


def first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].rstrip(".")


def tidy(text):
    """Screen-length phrasing: no parentheticals or clipped leading times ("00: ...")."""
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"\s*\[[\d:;,\s]+\]", "", text).replace(" -- ", " — ")
    text = re.sub(r"^(?:\d{1,2}:)?\d{2}:\s+", "", text)
    return sentence_case(re.sub(r"\s+([,;:])", r"\1", text).strip())


def sentence_case(text):
    return text[:1].upper() + text[1:] if text else text


def load_cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def save_cache(cache):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    except Exception:
        pass


def cached(cache, key, as_of, fetch):
    """cache[key] if fetched since this moment began, else fetch and store; stale on failure."""
    hit = cache.get(key)
    if hit and hit.get("at", 0) >= as_of.timestamp():
        return hit["data"]
    try:
        data = fetch()
        cache[key] = {"at": as_of.timestamp(), "data": data}
        return data
    except Exception:
        return hit["data"] if hit else None


def get_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "desk-companion/2"})
    return urllib.request.urlopen(req, timeout=10).read()


def mode_for(now):
    h = now.hour + now.minute / 60
    weekend = now.weekday() >= 5
    if h >= 22 or h < (7.5 if weekend else 6.75):
        return "night"
    if h >= 18:
        return "evening"
    if weekend:
        return "weekend"
    return "morning" if h < 9.5 else "work"


def moment(now, events, my_emails):
    """(as_of, meeting): the planned moment the screen should show at `now`.

    A meeting's card window runs from CARD_BEFORE its start to CARD_AFTER it; the
    latest-opened window wins. Otherwise the latest scene or card-end at or before
    now. Everything on screen is drawn as of that moment, so between moments the
    image (and so the panel) stays still."""
    points, windows = [], []
    for day in (now.date() - timedelta(days=1), now.date()):
        kind = "weekend" if day.weekday() >= 5 else "weekday"
        points += [datetime.combine(day, time.fromisoformat(t), TZ) for t in SCENES[kind]]
        for e in meetings_on(events, day, my_emails):
            windows.append((e["start"] - CARD_BEFORE, e["start"] + CARD_AFTER, e))
            points.append(e["start"] + CARD_AFTER)
    open_now = [w for w in windows if w[0] <= now < w[1] and mode_for(w[0]) != "night"]
    if open_now:
        opened, _, meeting = max(open_now, key=lambda w: w[0])
        return opened, meeting
    return max(p for p in points if p <= now), None


# --- the brief -------------------------------------------------------------------

def load_brief():
    """Latest brief's sections from its ## Response, plus the brief's date."""
    files = sorted(BRIEF_DIR.glob("*.md"), key=lambda p: p.name, reverse=True)
    if not files:
        return {}, None
    text = files[0].read_text(errors="ignore")
    at = text.rfind("## Response")
    body = text[at:] if at >= 0 else text
    parts = re.split(r"^\*\*([^*\n]+?)\*\*\s*[—–-]\s*", body, flags=re.M)
    sections = {parts[i].strip().lower(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    try:
        brief_day = date.fromisoformat(files[0].name[:10])
    except ValueError:
        brief_day = None
    return sections, brief_day


def parse_brief(sections):
    # C-handles ("before C1") become the event's time.
    handle_times = dict(re.findall(r"^(C\d+)\s+(\d{1,2}:\d{2})", sections.get("today", ""), re.M))

    def unhandle(s):
        return re.sub(r"\b(C\d+)\b", lambda m: handle_times.get(m.group(1), "the meeting"), s)

    out = {"do_first": "", "priorities": [], "waiting": [], "picks": [], "markets": [], "jacket": ""}

    raw = clean(sections.get("do this first", ""))
    if raw:
        action = first_sentence(raw)
        head = action.split(":")[0]
        out["do_first"] = unhandle(head if len(head) >= 25 else action)

    pri = clean(next((v for k, v in sections.items() if k.endswith("priorities")), ""))
    m = re.search(r"NOW:\s*(.*?)(?:\s+(?:THIS WEEK|BACKLOG|NEXT|LATER):|$)", pri)
    for item in re.split(r";\s*", m.group(1) if m else ""):
        item = item.strip().rstrip(".")
        if item:
            out["priorities"].append(unhandle(tidy(item)))

    for line in sections.get("waiting on you", "").splitlines():
        m = re.match(r"^W\d+\s+(.*)", clean(line))
        if m:
            item = re.split(r";|,\s*(?:unread|read|sent|received)\b", m.group(1))[0]
            out["waiting"].append(unhandle(item.strip().rstrip(".")))

    for line in sections.get("worth your attention", "").splitlines():
        m = re.match(r"^P\d+\s+<?(https?://[^>\s]+)>?\s*[—–-]\s*(.*)", clean(line))
        if m:
            why = m.group(2).strip()
            head = why.split(":")[0]
            out["picks"].append(tidy(head if 12 <= len(head) < len(why) else first_sentence(why)))

    for line in sections.get("markets", "").splitlines():
        m = re.match(r"^\s*([A-Z]{1,5})\s+\$([\d,]+(?:\.\d+)?).*?\b(?:UP|DOWN|FLAT)?\s*([+-]?\d+(?:\.\d+)?)%", line)
        if m:
            out["markets"].append((m.group(1), m.group(2), float(m.group(3))))

    m = re.search(r"jacket verdict:\s*([^.\n]+)", sections.get("weather", ""), re.I)
    if m:
        out["jacket"] = sentence_case(m.group(1).strip())
    return out


def load_weekend(today):
    """The weekend briefing for today: {section: [(title, why)]} from '- [Title](url) — why'."""
    files = sorted(WEEKEND_DIR.glob(f"{today.isoformat()}_*.md"), reverse=True)
    if not files:
        return {}
    text = files[0].read_text(errors="ignore")
    out, section = {}, None
    for line in text[text.rfind("## Response"):].splitlines():
        line = line.strip()
        head = re.match(r"^\*\*([^*]+)\*\*$", line)
        if head:
            section = head.group(1).strip().lower()
            continue
        m = re.match(r"^-\s*\[([^\]]+)\]\([^)]*\)\s*(?:[—–-]\s*(.*))?$", line)
        if section and m:
            why = tidy(first_sentence(clean(m.group(2) or "")))
            out.setdefault(section, []).append(clean(m.group(1)) + (f" — {why[:1].lower()}{why[1:]}" if why else ""))
    return out


# --- todos and wins ----------------------------------------------------------------

def load_todos_file():
    """todos.json: {"open": [{"text": "...", "due": "YYYY-MM-DD" or null}], "closed": [{"text": "...", "on": "YYYY-MM-DD"}]}"""
    try:
        return json.loads(TODOS.read_text())
    except Exception:
        return None


def load_todos():
    """Open todos, tidied for the screen and de-duplicated; None when there is no todos file."""
    data = load_todos_file()
    if data is None:
        return None
    rows = []
    for r in data.get("open") or []:
        text = tidy(first_sentence(clean(r.get("text", ""))))
        if text and not any(similar(text, x["text"]) for x in rows):
            rows.append({"text": text, "due": r.get("due")})
    return rows


STOP = {"with", "from", "that", "this", "their", "about", "before", "after", "into"}


def similar(a, b):
    """Two todos about the same thing: 3+ shared content words in their openings."""
    def words(t):
        return {w for w in re.findall(r"[a-z][a-z'-]{3,}", t.lower()[:90]) if w not in STOP}
    return len(words(a) & words(b)) >= 3


def wins_today(today):
    """Todos closed today."""
    data = load_todos_file() or {}
    return sum(1 for r in data.get("closed") or [] if str(r.get("on", ""))[:10] == today.isoformat())


# --- weather / world / metrics ------------------------------------------------------

WMO = {0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "part"),
       3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Fog", "fog"),
       51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Drizzle", "rain"),
       56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
       61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
       66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
       71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow", "snow"),
       80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
       85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
       95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm")}


def fetch_weather():
    return json.loads(get_url(
        f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&timezone={urllib.parse.quote(str(TZ), safe='')}&forecast_days=3"))


def fetch_headline():
    if not HEADLINE_RSS:
        return None
    root = ET.fromstring(get_url(HEADLINE_RSS))
    item = root.find("./channel/item")
    title = clean(item.findtext("title")) if item is not None else ""
    return re.sub(r"^(Watch|Listen|Video|In pictures|Live)\s*:\s*", "", title, flags=re.I) or None


def fetch_stars():
    if not GITHUB_USER:
        return None
    total, page = 0, 1
    while True:
        repos = json.loads(get_url(
            f"https://api.github.com/users/{GITHUB_USER}/repos?per_page=100&type=owner&page={page}"))
        total += sum(r.get("stargazers_count", 0) for r in repos)
        if len(repos) < 100:
            return total
        page += 1


def star_metrics(cache, now):
    stars = cached(cache, "stars", now, fetch_stars)
    if stars is None:
        return None, None
    hist = cache.setdefault("stars_hist", {})
    hist[now.date().isoformat()] = stars
    for d in sorted(hist)[:-60]:  # keep ~two months
        hist.pop(d)
    week_ago = (now.date() - timedelta(days=7)).isoformat()
    older = [d for d in sorted(hist) if d <= week_ago]
    base = hist[older[-1]] if older else None
    return stars, (stars - base if base is not None else None)


# --- drawing primitives ---------------------------------------------------------------

def weather_icon(dr, kind, x, y, s=44):
    """Tiny flat weather glyphs in panel colours, s x s box at (x, y)."""
    def sun(cx, cy, r):
        for i in range(8):
            a = i * math.pi / 4
            dr.line([(cx + math.cos(a) * (r + 3), cy + math.sin(a) * (r + 3)),
                     (cx + math.cos(a) * (r + 8), cy + math.sin(a) * (r + 8))], fill=ORANGE, width=3)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=ORANGE, width=2)

    def cloud(ox, oy, w):
        h = w * 0.55
        dr.ellipse([ox, oy + h * 0.35, ox + w * 0.5, oy + h], fill=WHITE, outline=BLACK, width=2)
        dr.ellipse([ox + w * 0.25, oy, ox + w * 0.8, oy + h * 0.8], fill=WHITE, outline=BLACK, width=2)
        dr.ellipse([ox + w * 0.5, oy + h * 0.3, ox + w, oy + h], fill=WHITE, outline=BLACK, width=2)
        dr.rectangle([ox + w * 0.22, oy + h * 0.6, ox + w * 0.78, oy + h - 2], fill=WHITE)
        dr.line([(ox + w * 0.2, oy + h - 1), (ox + w * 0.8, oy + h - 1)], fill=BLACK, width=2)

    if kind == "sun":
        sun(x + s / 2, y + s / 2, s * 0.22)
    elif kind == "part":
        sun(x + s * 0.62, y + s * 0.36, s * 0.16)
        cloud(x, y + s * 0.3, s * 0.8)
    elif kind in ("cloud", "fog"):
        cloud(x + 2, y + s * 0.15, s * 0.92)
        if kind == "fog":
            for i in range(3):
                dr.line([(x + 4, y + s * 0.78 + i * 5), (x + s - 4, y + s * 0.78 + i * 5)], fill=BLACK, width=2)
    else:
        cloud(x + 2, y, s * 0.92)
        drop = ORANGE if kind == "storm" else BLUE
        for i in range(3):
            bx = x + s * 0.25 + i * s * 0.22
            if kind == "snow":
                dr.ellipse([bx - 2, y + s * 0.7, bx + 3, y + s * 0.7 + 5], fill=drop)
            else:
                dr.line([(bx + 3, y + s * 0.66), (bx - 2, y + s * 0.92)], fill=drop, width=3)


def moon(dr, cx, cy, r):
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=ORANGE, width=2)
    dr.ellipse([cx - r + r * 0.7, cy - r - r * 0.25, cx + r + r * 0.7, cy + r - r * 0.25], fill=WHITE)


def label(dr, x, y, text, ink):
    dr.text((x, y), text, font=F["label"], fill=ink)


def draw_header(dr, title, weather, day_index, caption):
    """Title (the date), a caption, and the day's forecast: condition, high, low, rain.
    The day's forecast, not a live reading: the image is frozen between moments."""
    dr.text((M, 14), title, font=F["date"], fill=BLACK)
    if caption:
        dr.text((M + 2, 50), caption, font=F["small"], fill=RED)
    if weather:
        daily = weather["daily"]
        cond, kind = WMO.get(daily["weather_code"][day_index], ("", "cloud"))
        hi, lo = round(daily["temperature_2m_max"][day_index]), round(daily["temperature_2m_min"][day_index])
        rain = daily["precipitation_probability_max"][day_index]
        line2 = f"low {lo}°  ·  rain {rain}%"
        big = f"{hi}°"
        w2 = max(dr.textlength(cond, font=F["side_b"]), dr.textlength(line2, font=F["side"]))
        tx = W - M - w2
        dr.text((tx, 16), cond, font=F["side_b"], fill=BLACK)
        dr.text((tx, 40), line2, font=F["side"], fill=BLUE if rain >= 50 else BLACK)
        bw = dr.textlength(big, font=F["temp"])
        dr.text((tx - 14 - bw, 18), big, font=F["temp"], fill=BLACK)
        weather_icon(dr, kind, tx - 14 - bw - 56, 16)
    dr.line([(M, 72), (W - M, 72)], fill=BLACK, width=2)


ROW, LEAD_EXTRA, MORE_H = 27, 18, 20


def day_title(day, today):
    if day == today:
        return "TODAY"
    return ("TOMORROW · " if day == today + timedelta(days=1) else "") + day.strftime("%a %-d %b").upper()


def draw_day_label(dr, y, title, tally):
    label(dr, M, y, title, RED)
    dr.text((M + dr.textlength(title, font=F["label"]) + 10, y + 1), tally, font=F["small"], fill=BLACK)


def list_events(dr, now, day, all_day, timed, lead, y, bottom):
    """Draw one day's rows from y down to bottom; returns the y it stopped at."""
    lx, rx = M, SPLIT - 16
    for e in all_day[:2]:
        dr.rectangle([lx, y + 3, lx + 5, y + 20], fill=CATEGORY_INK[e["cat"]])
        dr.text((lx + 12, y + 3), "all day", font=F["small"], fill=BLACK)
        extra = f" (+{len(all_day) - 2})" if e is all_day[-1] and len(all_day) > 2 else ""
        dr.text((lx + 72, y + 1), fit(dr, e["title"], F["body"], rx - lx - 72 - 40) + extra, font=F["body"],
                fill=BLACK)
        y += ROW
    heights = {id(e): ROW + (LEAD_EXTRA if e is lead else 0) for e in timed}
    room = bottom - y
    shown = timed
    if sum(heights.values()) > room:
        # Keep the lead and your own events first, then family; show in time order.
        order = sorted(timed, key=lambda e: (e is not lead, not is_mine(e), e["start"]))
        shown, used = [], MORE_H
        for e in order:
            if used + heights[id(e)] <= room:
                shown.append(e)
                used += heights[id(e)]
        shown.sort(key=lambda e: e["start"])
    for e in shown:
        t = e["start"].strftime("%H:%M") if e["start"].date() == day else "…"
        is_lead = e is lead
        if is_lead:
            dr.rectangle([lx - 4, y - 2, rx, y + ROW + LEAD_EXTRA - 2], fill=YELLOW)
        dr.rectangle([lx, y + 3, lx + 5, y + 20], fill=CATEGORY_INK[e["cat"]])
        fnt = F["body_b"] if is_lead else F["body"]
        dr.text((lx + 12, y + 1), t, font=F["body_b"], fill=BLACK)
        dr.text((lx + 72, y + 1), fit(dr, e["title"], fnt, rx - lx - 76), font=fnt, fill=BLACK)
        if is_lead:
            on_now = e["start"] <= now
            detail = f"now · until {e['end']:%H:%M}" if on_now else f"next · {e['start']:%H:%M}–{e['end']:%H:%M}"
            if e["where"]:
                detail += f" · {e['where'].split(',')[0]}"
            dr.text((lx + 72, y + ROW - 4), fit(dr, detail, F["small"], rx - lx - 76), font=F["small"],
                    fill=RED if on_now else BLACK)
        y += heights[id(e)]
    hidden = len(timed) - len(shown)
    if hidden:
        dr.text((lx + 12, y + 3), f"+{hidden} more, last ends {max(e['end'] for e in timed):%H:%M}",
                font=F["small"], fill=BLACK)
        y += MORE_H
    return y


def draw_agenda(dr, now, events, day, highlight, then_day=None, skip_all_day=False):
    """Left column: the day's diary, colour-coded, your current/next event highlighted.
    With then_day, the next day's diary fills whatever room the first day leaves."""
    today = now.date()
    agenda = events_on(events, day)
    all_day = [] if skip_all_day else [e for e in agenda if e["all_day"] and e["cat"] != "family"]
    timed = [e for e in agenda if not e["all_day"]]
    if day == today:
        done = [e for e in timed if e["end"] <= now]
        timed = [e for e in timed if e["end"] > now]
        tally = f"{len(done)} done · {len(timed)} to go" if done else f"{len(timed)} on"
    else:
        tally = f"{len(timed)} on"
    draw_day_label(dr, TOP, day_title(day, today), tally)

    x = SPLIT - 16  # legend, right-aligned on the label row
    for name in ("family", "me", "work"):
        x -= dr.textlength(name, font=F["legend"])
        dr.text((x, TOP + 2), name, font=F["legend"], fill=BLACK)
        x -= 13
        dr.rectangle([x, TOP + 4, x + 9, TOP + 13], fill=CATEGORY_INK[name])
        x -= 10

    y = TOP + 24
    if events is None:
        dr.text((M, y), "Calendar unavailable", font=F["body"], fill=RED)
        return
    if not all_day and not timed:
        dr.text((M, y), "Nothing in the diary.", font=F["body"], fill=BLACK)
        y += ROW
    # Your own current/next event leads; family events never take the highlight.
    lead = next((e for e in timed if is_mine(e)), None) if highlight else None
    reserve = 24 + 3 * ROW if then_day else 0
    y = list_events(dr, now, day, all_day, timed, lead, y, BOTTOM - reserve)

    if then_day and BOTTOM - y >= 24 + 2 * ROW:
        y += 10
        nxt = events_on(events, then_day)
        n_timed = [e for e in nxt if not e["all_day"]]
        draw_day_label(dr, y, day_title(then_day, today), f"{len(n_timed)} on")
        list_events(dr, now, then_day, [e for e in nxt if e["all_day"] and e["cat"] != "family"], n_timed, None,
                    y + 24, BOTTOM)


def draw_blocks(dr, blocks):
    """Right column: stacked blocks of (heading, ink, items, max_items, max_lines, note).
    An item is text or (text, tag, tag_ink); a block that can't fit its first item is skipped."""
    sx, sw = SPLIT + 16, W - M - SPLIT - 16
    y = TOP
    for heading, ink, items, max_items, max_lines, note in blocks:
        if not items or y + 21 + 20 > BOTTOM:
            continue
        label(dr, sx, y, heading, ink)
        if note:
            dr.text((sx + dr.textlength(heading, font=F["label"]) + 8, y + 1), note, font=F["small"], fill=BLACK)
        y += 21
        for item in items[:max_items]:
            text, tag, tag_ink = item if isinstance(item, tuple) else (item, "", BLACK)
            bullet = max_items > 1
            indent = 14 if bullet else 0
            tag_w = dr.textlength(tag, font=F["small"]) + 6 if tag else 0
            lines = wrap(dr, text, F["side"], sw - indent - tag_w, max_lines)
            if y + 20 * len(lines) > BOTTOM:
                lines = lines[:max(0, (BOTTOM - y) // 20)]
                if not lines:
                    break
            if bullet:
                dr.rectangle([sx + 1, y + 7, sx + 6, y + 12], fill=ink)
            if tag:
                dr.text((W - M - tag_w + 6, y + 2), tag, font=F["small"], fill=tag_ink)
            for ln in lines:
                dr.text((sx + indent, y), ln, font=F["side"], fill=BLACK)
                y += 20
            y += 3
        y += 9
    dr.line([(SPLIT, TOP), (SPLIT, BOTTOM)], fill=BLACK, width=1)


def draw_footer(dr, line1, line2):
    """Two footer lines, each (tag, tag_ink, text) on the left plus optional right-hand spans."""
    dr.line([(M, 410), (W - M, 410)], fill=BLACK, width=2)
    for y, (left, right) in zip((418, 446), (line1, line2)):
        x = W - M
        for text, ink in reversed(right or []):
            x -= dr.textlength(text, font=F["foot"])
            dr.text((x, y), text, font=F["foot"], fill=ink)
        if left:
            tag, tag_ink, text = left
            label(dr, M, y + 2, tag, tag_ink)
            tx = M + (dr.textlength(tag, font=F["label"]) + 10 if tag else 0)
            if text:
                dr.text((tx, y), fit(dr, text, F["foot"], x - tx - 12), font=F["foot"], fill=BLACK)


def markets_spans(markets):
    spans = []
    for sym, price, pct in markets[:2]:
        if spans:
            spans.append(("   ", BLACK))
        spans.append((f"{sym} {price} ", BLACK))
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "■"
        spans.append((f"{arrow}{abs(pct):.1f}%", GREEN if pct > 0 else RED if pct < 0 else BLACK))
    return spans


def todo_items(todos, today, limit=3):
    """Due today first, then the most recently overdue, then the next due."""
    def due(r):
        try:
            return date.fromisoformat(r["due"]) if r.get("due") else None
        except ValueError:
            return None
    dated = [(due(r), r) for r in todos]
    today_rows = [r for d, r in dated if d == today]
    overdue = [r for d, r in sorted((x for x in dated if x[0] and x[0] < today), key=lambda x: x[0], reverse=True)]
    upcoming = [r for d, r in sorted((x for x in dated if x[0] and x[0] > today), key=lambda x: x[0])]
    items = []
    for r in (today_rows + overdue + upcoming)[:limit]:
        d = due(r)
        if d == today:
            tag, ink = "today", RED
        elif d < today:
            tag, ink = f"{(today - d).days}d late", RED
        else:
            tag, ink = d.strftime("%a"), BLACK
        items.append((r["text"], tag, ink))
    n_over = sum(1 for d, _ in dated if d and d < today)
    note = f"{len(todos)} open" + (f" · {n_over} overdue" if n_over else "")
    return items, note


def first_thing(events, day):
    timed = [e for e in events_on(events, day) if not e["all_day"] and e["start"].date() == day]
    mine = [e for e in timed if is_mine(e)]
    return (mine or timed or [None])[0], len(timed)


# --- screens ------------------------------------------------------------------------

def render_night(dr, now, weather, events):
    """Calm, static: the next day's first thing and its weather. Nothing to worry about."""
    day = now.date() + timedelta(days=1) if now.hour >= 12 else now.date()
    draw_header(dr, "Good night", weather, (day - now.date()).days, f"Weather for {day:%A}")
    ev, n = first_thing(events, day)
    label(dr, M, 110, f"FIRST THING · {day:%A}".upper(), GREEN)
    if ev:
        lines = wrap(dr, f"{ev['start']:%H:%M}  {ev['title']}", F["big"], W - 2 * M - 18, 2)
        dr.rectangle([M, 138, M + 6, 138 + 34 * len(lines) - 4], fill=CATEGORY_INK[ev["cat"]])
        for i, ln in enumerate(lines):
            dr.text((M + 18, 138 + 34 * i), ln, font=F["big"], fill=BLACK)
        if ev["where"]:
            dr.text((M + 18, 142 + 34 * len(lines)), fit(dr, ev["where"].split(",")[0], F["side"], W - 2 * M - 18),
                    font=F["side"], fill=BLACK)
    else:
        dr.text((M + 18, 138), "Nothing booked. A slow start.", font=F["big"], fill=BLACK)
    moon(dr, W // 2, 300, 30)
    msg = "Nothing needs you tonight."
    dr.text(((W - dr.textlength(msg, font=F["calm"])) / 2, 350), msg, font=F["calm"], fill=BLACK)
    sub = "Sleep well."
    dr.text(((W - dr.textlength(sub, font=F["side"])) / 2, 384), sub, font=F["side"], fill=GREEN)


TITLE_NOISE = {"meeting", "zoom", "call", "sync", "intro", "weekly", "catch", "chat", "with"}


def meeting_keywords(meeting, names):
    """Words that tie text to this meeting: the outside attendees' names and companies,
    and the title's proper nouns. Colleagues' names are left out: they are in everything."""
    words = set()
    for email in meeting["attendees"]:
        if is_external(email):
            words.update(re.findall(r"[a-z]{4,}", person_name(email, names).lower()))
            words.update(re.findall(r"[a-z]{4,}", company(email).lower()))
    words.update(w.lower() for w in re.findall(r"\b[A-Z][A-Za-z]{3,}", meeting["title"]))
    return {w.rstrip("'s") for w in words} - TITLE_NOISE


def mentions(texts, keywords):
    return [t for t in texts if keywords & set(re.findall(r"[a-z]{4,}", t.lower()))]


def render_card(dr, as_of, meeting, weather, events, sections, todos, names, my_emails):
    """The pre-meeting brief: who, why, last time, goal, what's open."""
    draw_header(dr, as_of.strftime("%A %-d %B"), weather, 0, "Meeting prep")
    x0, colw = M + 118, W - 2 * M - 118
    label(dr, M, TOP + 2, f"NEXT · {meeting['start']:%H:%M}–{meeting['end']:%H:%M}", RED)
    y = TOP + 24
    for ln in wrap(dr, meeting["title"], F["date"], W - 2 * M, 2):
        dr.text((M, y), ln, font=F["date"], fill=BLACK)
        y += 36
    card = load_cards(as_of.date()).get(f"{meeting['start']:%H:%M}") or {}
    people = card.get("who") or who(meeting["attendees"], names, my_emails)
    where = meeting["where"].split(",")[0] if meeting["where"] else ""
    for ln in wrap(dr, "with " + people + (f"  ·  {where}" if where else ""), F["side"], W - 2 * M, 2):
        dr.text((M, y + 2), ln, font=F["side"], fill=BLACK)
        y += 20
    y += 10
    dr.line([(M, y), (W - M, y)], fill=BLACK, width=1)
    y += 12

    # The agent's card first; without one, the brief's line for this slot and matching todos.
    keys = meeting_keywords(meeting, names)
    brief = parse_brief(sections)
    # Field by field: a card the agent could only half fill still gets the brief's lines.
    from_brief = [] if (card.get("why") and card.get("goal")) else \
        mentions([brief["do_first"]] + brief["priorities"] + brief["waiting"], keys)[:2]
    opens = [clean(o) for o in card.get("open") or [] if clean(o)] or \
        mentions([t["text"] for t in todos or []], keys)[:3]
    rows = [("WHY", BLUE, clean(card.get("why"))),
            ("LAST TIME", BLACK, clean(card.get("last"))),
            ("YOUR GOAL", RED, clean(card.get("goal"))),
            ("FROM BRIEF", BLUE, from_brief),
            ("OPEN", ORANGE, opens)]
    for head, ink, value in rows:
        if not value or y > BOTTOM - 20:
            continue
        label(dr, M, y + 2, head, ink)
        items = value if isinstance(value, list) else [value]
        for item in items:
            bullet = isinstance(value, list)
            width = colw - (14 if bullet else 0)
            room = (BOTTOM - y) // 23
            if room < 1:
                break
            lines = wrap(dr, item, F["body"], width, min(2, room))
            if bullet:
                dr.rectangle([x0 + 1, y + 8, x0 + 6, y + 13], fill=ink)
            for ln in lines:
                dr.text((x0 + (14 if bullet else 0), y), ln, font=F["body"], fill=BLACK)
                y += 23
            y += 2
        y += 8

    after = [e for e in events_on(events, as_of.date())
             if not e["all_day"] and e["start"] >= meeting["end"] and is_mine(e)]
    line1 = (("AFTER", RED, "  ·  ".join(f"{e['start']:%H:%M} {e['title']}" for e in after[:2])), None) \
        if after else (("AFTER", RED, "nothing else in your diary today"), None)
    # Provenance: who wrote the card and from what, when the card says so.
    prepared = clean(card.get("prepared"))
    draw_footer(dr, line1, (("PREPARED", BLUE, prepared), None) if prepared else (None, None))


def render(now):
    events, cal_fetched = load_calendar()
    names, my_emails = load_people()
    as_of, meeting = moment(now, events, my_emails)
    now = as_of  # everything below is drawn as of the moment, not the minute
    mode = "card" if meeting else mode_for(now)
    cache = load_cache()
    weather = cached(cache, "weather", as_of, fetch_weather)

    img = Image.new("RGB", (W, H), WHITE)
    dr = ImageDraw.Draw(img)
    dr.fontmode = "1"

    if mode == "card":
        todos = load_todos()
        save_cache(cache)
        sections, brief_day = load_brief()
        render_card(dr, as_of, meeting, weather, events, sections if brief_day == as_of.date() else {}, todos,
                    names, my_emails)
        return img, mode

    if mode == "night":
        save_cache(cache)
        render_night(dr, now, weather, events)
        return img, mode

    work_day = mode in ("morning", "work")
    headline = cached(cache, "headline", as_of, fetch_headline) if mode != "evening" else None
    todos = load_todos() if mode == "work" else None
    stars, stars_delta = star_metrics(cache, now) if mode == "work" else (None, None)
    save_cache(cache)

    today = now.date()
    tomorrow = today + timedelta(days=1)
    sections, brief_day = load_brief()
    brief = parse_brief(sections if brief_day == today else {})  # never yesterday's brief

    # which day the agenda shows
    todays = events_on(events, today)
    remaining = [e for e in todays if not e["all_day"] and e["end"] > now]
    show_tomorrow = mode == "evening" and (now.hour >= 21 or not remaining)
    agenda_day = tomorrow if show_tomorrow else today

    caption = {"morning": "Good morning", "weekend": "Weekend", "evening": "Evening"}.get(mode, "")
    draw_header(dr, now.strftime("%A %-d %B"), weather, 0, caption)
    if mode == "evening" and not show_tomorrow:
        draw_agenda(dr, now, events, today, highlight=True, then_day=tomorrow, skip_all_day=True)
    else:
        draw_agenda(dr, now, events, agenda_day, highlight=agenda_day == today)

    picks = brief["picks"]
    if mode == "morning":
        blocks = [("DO THIS FIRST", RED, [brief["do_first"]] if brief["do_first"] else [], 1, 3, ""),
                  (PICKS_LABEL, BLUE, picks, 3, 2, ""),
                  ("WAITING ON YOU", ORANGE, brief["waiting"], 3, 1, "")]
    elif mode == "work":
        t_items, t_note = todo_items(todos, today) if todos is not None else ([], "")
        blocks = [("PRIORITIES · NOW", BLUE, brief["priorities"], 2, 2, ""),
                  ("TODOS", RED, t_items, 3, 2, t_note),
                  ("WAITING ON YOU", ORANGE, brief["waiting"], 3, 1, "")]
    elif mode == "weekend":
        wk = load_weekend(today)
        london = next((v for k, v in wk.items() if k.startswith("things to do")), [])
        blocks = [("WITH THE KIDS", ORANGE, wk.get("with the kids", []), 3, 2, ""),
                  ("OUT IN LONDON", BLUE, london, 3, 2, ""),
                  (PICKS_LABEL, BLUE, picks, 3, 2, "")]
    else:  # evening: wins and something to read, nothing to chase
        closed = wins_today(today)
        done = [e for e in todays if not e["all_day"] and e["end"] <= now and is_mine(e)]
        wins = []
        if done:
            wins.append(f"{len(done)} diary item{'s' if len(done) != 1 else ''} done")
        if closed:
            wins.append(f"{closed} todo{'s' if closed != 1 else ''} closed")
        blocks = [("WINS TODAY", GREEN, wins or ["A day done. Well played."], 2, 1, ""),
                  ("TO READ, IF YOU LIKE", BLUE, picks, 3, 2, "")]
    if work_day and not (brief["do_first"] or brief["priorities"] or brief["waiting"]):
        blocks.append(("FOCUS", RED, ["Today's brief isn't in yet."], 1, 1, ""))
    draw_blocks(dr, blocks)

    # footer
    line1 = (("WORLD", RED, headline), None) if headline else (None, None)
    line2 = (None, None)
    if mode == "morning":
        line2 = (("JACKET", RED, brief["jacket"]) if brief["jacket"] else None, markets_spans(brief["markets"]))
    elif mode == "work":
        bits = []
        if stars is not None:
            bits.append(f"★ {stars} GitHub stars" + (f" (+{stars_delta} this week)" if stars_delta else ""))
        line2 = (("METRICS", RED, "  ·  ".join(bits)) if bits else None, markets_spans(brief["markets"]))
    elif mode == "weekend" and tomorrow.weekday() >= 5:
        ev, n = first_thing(events, tomorrow)
        if ev:
            line2 = (("TOMORROW", RED, f"{ev['start']:%H:%M} {ev['title']}" + (f"  (+{n - 1})" if n > 1 else "")),
                     None)
    elif mode == "evening" and weather:
        d = weather["daily"]
        cond = WMO.get(d["weather_code"][1], ("", ""))[0]
        line1 = (("TOMORROW", RED, f"{cond}  ·  {round(d['temperature_2m_min'][1])}–"
                                   f"{round(d['temperature_2m_max'][1])}°  ·  rain {d['precipitation_probability_max'][1]}%"),
                 None)
    if work_day:  # stale-data warnings only in working hours: don't worry me after six
        w = []
        if cal_fetched and now.timestamp() - float(cal_fetched) > 3 * 3600:
            w.append(f"calendar last synced {int((now.timestamp() - float(cal_fetched)) // 3600)}h ago")
        if now.hour >= 8 and brief_day != today:
            w.append("no brief today")
        if w:
            line1 = (line1[0], [(" · ".join(w), ORANGE)])
    draw_footer(dr, line1, line2)
    return img, mode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("size", nargs="*", type=int)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--now", help="ISO time to render as (testing)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    global W, H
    if len(args.size) == 2:
        W, H = args.size
    now = datetime.fromisoformat(args.now).astimezone(TZ) if args.now else datetime.now(TZ)
    load_fonts()
    img, mode = render(now)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.png")
    img.save(tmp, optimize=True)
    tmp.replace(out)  # atomic: InkyPi never fetches a half-written file
    if args.verbose:
        print(f"desk.png ({mode}) written {out} {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
