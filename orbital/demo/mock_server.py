#!/usr/bin/env python3
"""mock_server.py: the two made-up systems in demo-services.taxi, serving the sample data.

    python3 orbital/demo/mock_server.py        # listens on :18901

A CRM (/contacts/<email>) and a notes system (/last-meeting/<email>,
/commitments/<company>). Unknown people get a 404, as a real system would.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

CONTACTS = {
    "priya.shah@harbourline-freight.com": {"name": "Priya Shah", "company": "Harbourline Freight"},
    "dan.okafor@harbourline-freight.com": {"name": "Dan Okafor", "company": "Harbourline Freight"},
    "ruth.adeyemi@kestrel-health.com": {"name": "Ruth Adeyemi", "company": "Kestrel Health"},
}
LAST_MEETINGS = {
    "priya.shah@harbourline-freight.com": {"date": "2026-09-12",
                                           "summary": "Schema drift across 40+ carrier APIs keeps breaking their integrations"},
    "ruth.adeyemi@kestrel-health.com": {"date": "2026-09-22",
                                        "summary": "Wants one view of patient flow across three legacy systems"},
}
COMMITMENTS = {
    "Harbourline Freight": [{"text": "Send the security pack we promised", "due": "2026-10-01"},
                            {"text": "Confirm who owns their data platform after the reorg", "due": None}],
    "Kestrel Health": [{"text": "Return the contract redlines", "due": "2026-10-02"}],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        kind, _, key = self.path.lstrip("/").partition("/")
        key = unquote(key)
        if kind == "contacts" and key in CONTACTS:
            body = {"email": key, **CONTACTS[key]}
        elif kind == "last-meeting" and key in LAST_MEETINGS:
            body = LAST_MEETINGS[key]
        elif kind == "commitments":
            body = COMMITMENTS.get(key, [])
        else:
            self.send_error(404)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"mock: {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "18901"))
    print(f"mock CRM and notes on :{port}")
    ThreadingHTTPServer((os.environ.get("MOCK_HOST", "127.0.0.1"), port), Handler).serve_forever()
