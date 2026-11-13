"""End-to-end robots-to-browser handoff with a real local origin and a mock desktop opener."""
from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import browser_review
import seo_geo


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        status, body = 404, b""
        if self.path == "/robots.txt":
            status, body = self.server.policy, b"User-agent: *\nDisallow: /private\nDisallow: /nested/private\n"
        elif self.path == "/":
            status, body = 200, (b'<html><title>Start</title><a href="/private?q=1&amp;b=2">one</a>'
                                 b'<a href="/private?q=1&amp;b=2">two</a><a href="/from">move</a></html>')
        elif self.path in ("/from", "/nested/move"):
            self.send_response(302)
            self.send_header("Location", "/nested/move" if self.path == "/from" else "private-redirect")
            self.end_headers()
            return
        elif self.path.startswith("/private"):
            status, body = 200, b"<title>Visible to a person</title>"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html")
        self.end_headers()
        self.wfile.write(body)


class TerminalBuffer(StringIO):
    def isatty(self):
        return True


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.append(dict(attrs).get("href"))


class BrowserReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.policy = 200
        self.server.requests = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        self.folder = Path(self.tmp.name) / ("127.0.0.1_%d" % self.server.server_port)
        self.audit = self.folder / "audit.json"
        opener = patch("browser_review.webbrowser.open_new_tab", return_value=True)
        self.opened = opener.start()
        self.addCleanup(opener.stop)

    def cli(self, *args):
        self.stdout = StringIO()
        with redirect_stdout(self.stdout), redirect_stderr(StringIO()):
            return seo_geo.main(list(args))

    def run_audit(self, path, *extra):
        return self.cli("audit", self.base + path, "--out", self.tmp.name, "--delay", "0", "--direct", *extra)

    def test_blocked_start_opens_one_tab_and_record_does_not_modify_audit(self):
        self.assertEqual(self.run_audit("/private?q=1", "--browser-fallback"), 2)
        url = self.base + "/private?q=1"
        self.opened.assert_called_once_with(url)
        self.assertNotIn("/private?q=1", self.server.requests)
        original = self.audit.read_bytes()
        self.assertFalse(json.loads(original)["pages"])
        self.assertEqual(self.cli("browser-review", str(self.audit)), 0)
        self.assertIn("미확인", self.stdout.getvalue())
        self.assertEqual(self.cli("browser-review", str(self.audit), "--result", "visible",
                                  "--note", "Page rendered"), 0)
        self.assertEqual(self.audit.read_bytes(), original)
        log, = (self.folder / "browser-reviews").glob("*.jsonl")
        observation = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual((observation["url"], observation["surface"], observation["note"]),
                         (url, "ordinary_browser", "Page rendered"))
        self.assertEqual(self.cli("browser-review", str(self.audit)), 0)
        self.assertIn("visible", self.stdout.getvalue())

    def test_report_and_selection_cover_internal_link_and_redirect(self):
        self.run_audit("/")
        self.opened.assert_not_called()
        items = browser_review.candidates(json.loads(self.audit.read_bytes()))
        urls = [r["url"] for r in items]
        self.assertEqual(urls, [self.base + "/private?q=1&b=2", self.base + "/nested/private-redirect"])
        self.assertFalse(any("private" in p for p in self.server.requests))
        links = Links()
        links.feed((self.folder / "report.html").read_text(encoding="utf-8"))
        self.assertTrue(all(u in links.urls for u in urls))
        self.assertEqual(self.cli("browser-review", str(self.audit), "--page", "2", "--open"), 0)
        self.opened.assert_called_once_with(urls[1])

    def test_503_offers_original_start_without_fetching_it(self):
        self.server.policy = 503
        self.assertEqual(self.run_audit("/catalog?q=1", "--browser-fallback"), 2)
        self.assertEqual(self.server.requests, ["/robots.txt"])
        self.opened.assert_called_once_with(self.base + "/catalog?q=1")
        self.assertEqual(browser_review.candidates(json.loads(self.audit.read_bytes()))[0]["reason"],
                         "robots_unavailable")

    def test_no_robots_block_no_browser_even_on_normal_404(self):
        self.assertEqual(self.run_audit("/ordinary-404", "--browser-fallback"), 2)
        self.opened.assert_not_called()
        self.assertEqual(browser_review.candidates(json.loads(self.audit.read_bytes())), [])

    def test_terminal_defaults_to_one_tab_and_opt_out_keeps_links(self):
        url = self.base + "/private"
        with patch("sys.stdin.isatty", return_value=True), patch(__name__ + ".StringIO", TerminalBuffer):
            self.run_audit("/private")
            self.opened.assert_called_once_with(url)
            self.run_audit("/private", "--no-browser-fallback")
            self.opened.assert_called_once_with(url)

    def test_constraints_and_legacy_audit(self):
        base = "https://example.com"
        bad = ["javascript:alert(1)", "https://example.com.evil.test/a",
               "https://user@example.com/a", "https://example.com:444/a", "http://example.com/a",
               "https://example.com\\@evil.test/a", "https://example.com/a\n"]
        old = {"target": {"base": base}}
        self.assertEqual(browser_review.candidates(old), [])
        old["coverage"] = {"browser_review": [{"url": u, "reason": "disallow"} for u in
                           bad + [base + "/" + str(i) for i in range(100)]]}
        urls = [r["url"] for r in browser_review.candidates(old)]
        self.assertEqual(len(urls), browser_review.LIMIT)
        self.assertFalse(set(urls).intersection(bad))
