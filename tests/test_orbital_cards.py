"""Tests for orbital_cards.py against a fake Orbital (no Docker, no network).

The fetcher runs in a subprocess so it reads its own data folder; the fake
Orbital runs in this process and answers /api/taxiql by the email in the query.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import orbital_cards as oc  # noqa: E402

FACTS = {
    "priya.shah@harbourline-freight.com": {
        "name": "Priya Shah", "company": "Harbourline Freight", "lastMet": "2026-09-12",
        "lastSummary": "Schema drift keeps breaking their integrations",
        "open": [{"text": "Send the security pack", "due": "2026-10-01"}]},
    "ruth.adeyemi@kestrel-health.com": {
        "name": "Ruth Adeyemi", "company": "Kestrel Health", "lastMet": "2026-09-22",
        "lastSummary": "Wants one view of patient flow", "open": [{"text": "Return the contract redlines"}]},
}


class FakeOrbital(BaseHTTPRequestHandler):
    queries = []
    fail_for = set()

    def do_POST(self):
        query = self.rfile.read(int(self.headers["Content-Length"])).decode()
        FakeOrbital.queries.append((self.path, self.headers["Content-Type"], query))
        email = re.search(r'"([^"]+@[^"]+)"', query).group(1)
        if email in FakeOrbital.fail_for:
            self.send_error(500, "compiler says no")
            return
        body = json.dumps(FACTS[email]).encode() if email in FACTS else b""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class OrbitalCardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOrbital)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeOrbital.queries, FakeOrbital.fail_for = [], set()
        self.data = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "demo", self.data, dirs_exist_ok=True)
        self.cards_file = self.data / "cards" / "2026-10-01.json"

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    def run_fetcher(self, *args, url=True):
        env = dict(os.environ, DESK_DATA=str(self.data), DESK_WORK_DOMAINS="yourco.example")
        env.pop("DESK_ORBITAL_URL", None)
        if url:
            env["DESK_ORBITAL_URL"] = self.url
        return subprocess.run([sys.executable, "orbital_cards.py", "--date", "2026-10-01", *args], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=60)

    def cards(self):
        return {c["start"]: c for c in json.loads(self.cards_file.read_text())["cards"]}

    def test_without_orbital_it_does_nothing(self):
        # Arrange
        before = self.cards_file.read_text()
        # Act
        p = self.run_fetcher(url=False)
        # Assert
        self.assertEqual(p.returncode, 0)
        self.assertIn("optional", p.stdout)
        self.assertEqual(self.cards_file.read_text(), before)
        self.assertEqual(FakeOrbital.queries, [])

    def test_fills_missing_cards_and_keeps_the_agents_fields(self):
        # Arrange
        agent_card = self.cards()["10:00"]
        # Act
        p = self.run_fetcher()
        # Assert
        self.assertEqual(p.returncode, 0, p.stderr)
        cards = self.cards()
        self.assertEqual(cards["10:00"], agent_card)  # the agent already filled it: untouched
        kestrel = cards["12:30"]
        self.assertEqual(kestrel["who"], "Ruth Adeyemi (Kestrel Health)")
        self.assertEqual(kestrel["last"], "22 Sep: Wants one view of patient flow")
        self.assertEqual(kestrel["open"], ["Return the contract redlines"])
        self.assertTrue(kestrel["prepared"].startswith("facts joined by Orbital at "))
        path, ctype, query = FakeOrbital.queries[0]
        self.assertEqual((path, ctype), ("/api/taxiql", "application/taxiql"))
        self.assertIn("find { desk.Contact }", query)

    def test_overwrite_lets_orbital_replace_facts_but_not_judgement(self):
        # Act
        self.run_fetcher("--overwrite")
        # Assert
        card = self.cards()["10:00"]
        self.assertEqual(card["last"], "12 Sep: Schema drift keeps breaking their integrations")
        self.assertEqual(card["who"], "Priya Shah (Harbourline Freight), +1")
        self.assertTrue(card["why"].startswith("Scope a six-week pilot"))  # the agent's judgement stays

    def test_an_orbital_error_skips_that_meeting_only(self):
        # Arrange
        FakeOrbital.fail_for = {"priya.shah@harbourline-freight.com"}
        # Act
        p = self.run_fetcher("--overwrite")
        # Assert
        self.assertEqual(p.returncode, 0)
        self.assertIn("skipped", p.stderr)
        self.assertEqual(self.cards()["12:30"]["who"], "Ruth Adeyemi (Kestrel Health)")

    def test_dry_run_writes_nothing(self):
        before = self.cards_file.read_text()
        p = self.run_fetcher("--dry-run")
        self.assertIn('"12:30"', p.stdout)
        self.assertEqual(self.cards_file.read_text(), before)


class QueryTest(unittest.TestCase):
    def test_email_goes_into_the_query(self):
        q = oc.build_query('given { email : desk.EmailAddress = "{{email}}" }', "priya@harbourline-freight.com")
        self.assertIn('"priya@harbourline-freight.com"', q)

    def test_anything_but_a_plain_email_is_refused(self):
        for bad in ('x" } find { Secret } //@a.com', "no-at-sign", "a@b", "a@b.com\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                oc.build_query("{{email}}", bad)

    def test_facts_to_card(self):
        # Arrange
        facts = {"name": "Priya Shah", "company": "Harbourline Freight", "lastMet": "2026-09-12",
                 "lastSummary": "Schema drift", "open": [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}]}
        # Act
        card = oc.facts_to_card(facts, others=2)
        # Assert
        self.assertEqual(card, {"who": "Priya Shah (Harbourline Freight), +2", "last": "12 Sep: Schema drift",
                                "open": ["a", "b", "c"]})

    def test_missing_facts_leave_fields_out(self):
        self.assertEqual(oc.facts_to_card({"name": "Priya Shah"}, others=0), {"who": "Priya Shah"})


if __name__ == "__main__":
    unittest.main()
