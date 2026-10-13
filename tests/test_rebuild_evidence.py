# -*- coding: utf-8 -*-
"""Offline coverage of conservative rebuild evidence collection."""

import os
import sys
import unittest
import http.client
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import rebuild_evidence as evidence  # noqa: E402


def brief():
    return {"schema": evidence.SCHEMA, "site_url": "https://example.com/",
            "queries": [{"id": "q1", "text": "price", "intent_id": "pricing", "terms": ["cost"],
                         "required_sections": ["comparison"], "page_type": "guide", "priority": 1}],
            "competitors": [], "channels": [], "facts": [], "pages": []}


class BriefTests(unittest.TestCase):
    def test_empty_optional_evidence_is_valid(self):
        self.assertEqual(evidence.validate_brief(brief())["facts"], [])

    def test_invalid_schema_priority_and_fact_are_rejected(self):
        data = brief()
        data["schema"] = "other"
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)

    def test_optional_query_type_and_fact_intents(self):
        data = brief()
        data["queries"][0]["type"] = "brand"
        data["facts"] = [{"id": "f", "text": "Fact", "source_url": "https://example.com/f",
                          "verified": False, "intent_ids": ["pricing"]}]
        self.assertEqual(evidence.validate_brief(data)["facts"][0]["intent_ids"], ["pricing"])
        data["queries"][0]["type"] = "other"
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)
        data["queries"][0]["type"] = "nonbrand"
        data["facts"][0]["intent_ids"] = ["unknown"]
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)

    def test_pages_require_known_intent_and_site_host(self):
        data = brief()
        data["pages"] = [{"intent_id": "pricing", "url": "https://example.com/a"},
                         {"intent_id": "pricing", "url": "http://example.com/b"}]
        self.assertEqual(len(evidence.validate_brief(data)["pages"]), 2)
        data["pages"][1]["url"] = "https://other.example/a"
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)
        data["pages"][1] = {"intent_id": "unknown", "url": "https://example.com/b"}
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)
        data = brief()
        data["queries"][0]["priority"] = True
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)
        data = brief()
        data["facts"] = [{"id": "f1", "text": "A", "source_url": "https://example.com/f", "verified": "yes"}]
        with self.assertRaises(ValueError):
            evidence.validate_brief(data)

    def test_public_urls_reject_local_credentials_and_odd_syntax(self):
        for url in ("http://localhost/x", "http://127.0.0.1/", "http://192.168.1.1/",
                    "http://[::1]/", "https://a:b@example.com/", "file:///etc/passwd",
                    "https://example.com/a#fragment", "https://example.com\\@evil.test/"):
            self.assertFalse(evidence.public_url(url), url)
        self.assertTrue(evidence.public_url("https://example.com:8443/a"))
        self.assertTrue(evidence.public_url("https://例子.测试/경로"))
        self.assertFalse(evidence.public_url("https://example.com/\u200bhidden"))
        self.assertFalse(evidence.public_url("https://example.com/has\u00a0space"))


class FetchTests(unittest.TestCase):
    def test_dns_rejects_mixed_public_private_results(self):
        rows = [(None, None, None, None, ("8.8.8.8", 80)),
                (None, None, None, None, ("127.0.0.1", 80))]
        with patch.object(evidence.socket, "getaddrinfo", return_value=rows):
            with self.assertRaisesRegex(ValueError, "nonpublic"):
                evidence._resolve_host("example.com", 80)

    def test_dns_delay_respects_request_deadline(self):
        release = threading.Event()
        entered = threading.Event()
        def delayed(host, port):
            entered.set()
            release.wait(1)
            return ["8.8.8.8"]
        try:
            with patch.object(evidence, "_resolve_host", side_effect=delayed):
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "deadline"):
                    evidence._request_once("https://example.com/", 100, 0.03)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 0.3)
        finally:
            release.set()

    def test_drip_response_respects_total_request_deadline(self):
        release = threading.Event()
        entered = threading.Event()
        class Response:
            status = 200
            def getheaders(self): return [("Content-Type", "text/html")]
            def read(self, n):
                entered.set()
                release.wait(1)
                return b"ok"
        class Connection:
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        try:
            with patch.object(evidence, "_resolve_host", return_value=["8.8.8.8"]), \
                    patch.object(evidence, "_PinnedHTTPS", return_value=Connection()):
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "deadline"):
                    evidence._request_once("https://example.com/", 100, 0.03)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - started, 0.3)
        finally:
            release.set()

    def test_robots_disallow_prevents_page_request(self):
        calls = []
        def request(url, limit, timeout):
            calls.append(url)
            return 200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /secret"
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/secret")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(calls, ["https://example.com/robots.txt"])

    def test_robots_failure_is_blocked(self):
        with patch.object(evidence, "_request_once", return_value=(503, {}, b"")):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "blocked")

    def test_robots_network_failure_is_blocked(self):
        with patch.object(evidence, "_request_once", side_effect=OSError("connection refused")):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "blocked")

    def test_robots_redirect_to_other_origin_is_not_followed(self):
        calls = []
        def request(url, limit, timeout):
            calls.append(url)
            return 302, {"location": "https://other.example/robots.txt"}, b""
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(calls, ["https://example.com/robots.txt"])

    def test_observed_page_keeps_original_url(self):
        def request(url, limit, timeout):
            if url.endswith("robots.txt"):
                return 404, {}, b""
            if url == "https://example.com/start":
                return 301, {"location": "/finish"}, b""
            return 200, {"content-type": "text/html"}, b"<h1>Done</h1>"
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/start")
        self.assertEqual(result["outcome"], "observed")
        self.assertEqual(result["url"], "https://example.com/start")
        self.assertEqual(result["final_url"], "https://example.com/finish")

    def test_redirect_to_private_url_is_blocked_before_request(self):
        calls = []
        def request(url, limit, timeout):
            calls.append(url)
            if url.endswith("robots.txt"):
                return 404, {}, b""
            return 302, {"location": "http://127.0.0.1/private"}, b""
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(len(calls), 2)

    def test_redirect_target_robots_disallow(self):
        calls = []
        def request(url, limit, timeout):
            calls.append(url)
            if url == "https://example.com/robots.txt":
                return 404, {}, b""
            if url == "https://example.com/":
                return 302, {"location": "https://other.example/secret"}, b""
            if url == "https://other.example/robots.txt":
                return 200, {}, b"User-agent: *\nDisallow: /secret"
            self.fail(f"unsafe fetch {url}")
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "blocked")
        self.assertNotIn("https://other.example/secret", calls)

    def test_request_response_limit_is_enforced(self):
        class Response:
            status = 200
            def getheaders(self): return [("Content-Type", "text/html")]
            def read(self, n): return b"x" * n
        class Connection:
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(evidence, "_resolve_host", return_value=["8.8.8.8"]), \
                patch.object(evidence, "_PinnedHTTPS", return_value=Connection()):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                evidence._request_once("https://example.com/", 8, 1)

    def test_unicode_path_is_encoded_for_http_and_idn_host_is_ascii(self):
        seen = {}
        class Response:
            status = 200
            def getheaders(self): return []
            def read(self, n): return b""
        class Connection:
            def __init__(self, host, port, address, timeout):
                seen["host"] = host
            def request(self, method, path, headers): seen["path"] = path
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(evidence, "_resolve_host", return_value=["8.8.8.8"]), \
                patch.object(evidence, "_PinnedHTTPS", Connection):
            evidence._request_once("https://例子.测试/경로?q=검색", 100, 1)
        self.assertEqual(seen["host"], "xn--fsqu00a.xn--0zwm56d")
        self.assertIn("%EA%B2%BD%EB%A1%9C", seen["path"])
        self.assertIn("%EA%B2%80%EC%83%89", seen["path"])

    def test_bad_http_response_returns_explicit_error(self):
        def request(url, limit, timeout):
            if url.endswith("robots.txt"):
                return 404, {}, b""
            raise http.client.BadStatusLine("nonsense")
        with patch.object(evidence, "_request_once", side_effect=request):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "error")
        self.assertIn("nonsense", result["error"])

    def test_inert_html_extraction_and_features(self):
        html = ('<html><head><title>Observed</title><script>evil()</script></head><body>'
                '<nav><h1>Menu</h1></nav><h2>Overview</h2><h1>Product <em>Guide</em></h1>'
                '<table><tr><td>A</td></tr></table><h2>FAQ</h2><h2>Sources</h2>'
                '<script><h1>Injected</h1></script><footer>Footer</footer>'
                '<div hidden><h2>Secret</h2></div><aside>Sidebar</aside>'
                '<div aria-hidden="true">Hidden</div><div style="display:none">Invisible</div>'
                '<h2>Visible</h2></body></html>')
        page = evidence.extract_page(html, "https://example.com/")
        self.assertEqual(page["h1"], "Product Guide")
        self.assertNotIn("evil", page["text_excerpt"])
        self.assertNotIn("Injected", page["headings"])
        for hidden in ("Secret", "Sidebar", "Hidden", "Invisible"):
            self.assertNotIn(hidden, page["text_excerpt"])
        self.assertIn("Visible", page["text_excerpt"])
        self.assertEqual(page["features"], {"table": True, "faq": True, "sources": True})


if __name__ == "__main__":
    unittest.main()
