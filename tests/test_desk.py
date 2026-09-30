"""Tests for the desk display, run against the sample data in demo/.

    python3 -m unittest discover -s tests

The render tests run each panel in a subprocess, because the palette and data
paths are read from the environment when the modules are imported.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_ENV = {"DESK_DATA": str(ROOT / "demo"), "DESK_WORK_DOMAINS": "yourco.example",
            "DESK_FAMILY_ACCOUNTS": "family@example.com", "DESK_AGENT_NAME": "Hunter", "DESK_HEADLINE_RSS": ""}

# In-process tests use the sample data too; set it before the modules read it.
os.environ.update(DEMO_ENV)
os.environ["DESK_CACHE"] = str(Path(tempfile.mkdtemp()) / "cache.json")
sys.path.insert(0, str(ROOT))
import desk_companion as dc  # noqa: E402
import desk_data as dd  # noqa: E402

PROBE = r"""
import json
from datetime import datetime
import desk_companion as dc
dc.load_fonts()
out = {}
for scene, at in [("morning", "07:15"), ("work", "09:30"), ("card", "09:45"), ("night", "22:30")]:
    img, mode = dc.render(datetime.fromisoformat(f"2026-10-01T{at}:00+01:00").astimezone(dc.TZ))
    colours = {c for _, c in img.getcolors(1 << 20)}
    out[scene] = {"mode": mode, "size": list(img.size), "stray": len(colours - dc.PALETTE)}
print(json.dumps(out))
"""


def render_all(panel):
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "cache.json"
        shutil.copy(ROOT / "demo" / "cache-template.json", cache)  # weather without the network
        env = dict(os.environ, **DEMO_ENV, DESK_PANEL=panel, DESK_CACHE=str(cache))
        p = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=60)
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


class RenderTest(unittest.TestCase):
    def test_every_scene_on_every_panel(self):
        for panel in ("spectra6", "acep7", "preview"):
            with self.subTest(panel=panel):
                # Arrange / Act
                result = render_all(panel)
                # Assert: the planned scene, the panel size, and only the panel's exact colours
                self.assertEqual({s: r["mode"] for s, r in result.items()},
                                 {"morning": "morning", "work": "work", "card": "card", "night": "night"})
                for scene, r in result.items():
                    self.assertEqual(r["size"], [800, 480], scene)
                    self.assertEqual(r["stray"], 0, f"{scene}: colours outside the {panel} palette")

    def test_unknown_panel_is_refused(self):
        env = dict(os.environ, DESK_PANEL="rainbow")
        p = subprocess.run([sys.executable, "-c", "import desk_companion"], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DESK_PANEL", p.stderr)


class BriefTest(unittest.TestCase):
    def test_sample_brief_sections(self):
        # Arrange
        sections, day = dc.load_brief()
        # Act
        brief = dc.parse_brief(sections)
        # Assert
        self.assertEqual(day, date(2026, 10, 1))
        self.assertEqual(brief["do_first"], "Send Priya the security pack before your call")
        self.assertEqual(brief["priorities"], ["Close the Harbourline pilot", "Hire the founding engineer"])
        self.assertEqual(len(brief["waiting"]), 3)
        self.assertEqual(brief["picks"][0], "Semantic layers are having a moment")
        self.assertEqual(brief["jacket"], "Light jacket, dry until the evening")

    def test_missing_brief_is_empty(self):
        brief = dc.parse_brief({})
        self.assertEqual((brief["do_first"], brief["priorities"], brief["waiting"]), ("", [], []))


class TodoTest(unittest.TestCase):
    def test_open_todos_and_wins(self):
        todos = dc.load_todos()
        self.assertEqual([t["text"] for t in todos][0], "Send Harbourline the security pack")
        self.assertEqual(dc.wins_today(date(2026, 10, 1)), 1)

    def test_due_tags(self):
        # Arrange
        todos = [{"text": "Today's thing", "due": "2026-10-01"}, {"text": "Late thing", "due": "2026-09-29"},
                 {"text": "Next thing", "due": "2026-10-02"}]
        # Act
        items, note = dc.todo_items(todos, date(2026, 10, 1))
        # Assert
        self.assertEqual([(t, tag) for t, tag, _ in items],
                         [("Today's thing", "today"), ("Late thing", "2d late"), ("Next thing", "Fri")])
        self.assertEqual(note, "3 open · 1 overdue")


class CalendarTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(dd.category({"account": "you@yourco.example"}), "work")
        self.assertEqual(dd.category({"account_address": "family@example.com"}), "family")
        self.assertEqual(dd.category({"calendar": "UK Holidays"}), "holiday")
        self.assertEqual(dd.category({"account": "iCloud"}), "me")

    def test_meetings_need_other_people(self):
        # Arrange
        events, _ = dd.load_calendar()
        names, me = dd.load_people()
        # Act
        titles = [e["title"] for e in dd.meetings_on(events, date(2026, 10, 1), me)]
        # Assert: work meetings with someone else in them; not the gym or the family diary
        self.assertIn("Harbourline Freight: pilot scoping", titles)
        self.assertNotIn("Gym", titles)
        self.assertNotIn("Parents' evening", titles)

    def test_who_names_outside_people_with_company(self):
        names, me = dd.load_people()
        self.assertEqual(dd.who(["priya.shah@harbourline-freight.com", "sam.patel@yourco.example"], names, me),
                         "Priya Shah (Harbourline Freight), Sam Patel")


if __name__ == "__main__":
    unittest.main()
