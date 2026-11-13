# -*- coding: utf-8 -*-
"""실제 HTTP 중복 X-Robots-Tag 보존과 UA 범위 분리 회귀 테스트."""

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "tools"))

import crawl  # noqa: E402
import generate  # noqa: E402
import verify  # noqa: E402


class RobotsHeaderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><html><head><title>x</title></head><body>x</body></html>"
        if self.path in ("/news", "/web"):
            scope = "googlebot-news" if self.path == "/news" else "googlebot"
            body = ('<html><head><meta name="%s" content="noindex"></head></html>' % scope).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if self.path == "/mixed":
            # 먼저 나온 다른 UA 범위가 뒤의 generic/Googlebot 지시를 삼키면 안 된다.
            self.send_header("X-Robots-Tag", "bingbot: noindex")
            self.send_header("X-Robots-Tag", "noindex")
            self.send_header("X-Robots-Tag", "googlebot: nosnippet, nofollow")
        elif self.path == "/bing-only":
            self.send_header("X-Robots-Tag", "bingbot: noindex, nofollow")
        elif self.path == "/multi-scope":
            self.send_header("X-Robots-Tag",
                             "bingbot: noindex, googlebot: max-snippet: 0, nofollow")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DuplicateRobotsHeaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RobotsHeaderHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def page(self, path):
        response = crawl.fetch(self.base + path)
        return crawl.page_record(self.base + path, response), response

    def test_news_only_restriction_keeps_article_in_web_sitemap(self):
        pages = [self.page(path)[0] for path in ("/news", "/web")]
        self.assertIn("noindex", crawl._robots_directives(pages[0], "googlebot-news"))
        self.assertFalse(crawl._noindex(pages[0]))
        self.assertTrue(crawl._noindex(pages[1]))
        ctx = generate.Ctx({"target": {"base": self.base, "host": crawl.host_of(self.base)},
                            "pages": pages}, {}, ".")
        self.assertEqual(generate.eligible_urls(ctx), [self.base + "/news"])
        session = verify.Session(crawl.host_of(self.base), delay=0)
        self.assertFalse(verify.is_noindex(session.get(self.base + "/news")))
        self.assertTrue(verify.is_noindex(session.get(self.base + "/web")))
        directives = {"x_robots_tag": "googlebot-news: noindex, nofollow"}
        self.assertFalse(crawl._noindex(directives))
        self.assertIn("noindex", crawl._robots_directives(directives, "googlebot-news"))

    def test_duplicate_header_lines_are_preserved_and_combined_for_google(self):
        page, response = self.page("/mixed")
        raw = response["headers"]["x-robots-tag"]
        self.assertIn("bingbot: noindex", raw)
        self.assertIn("googlebot: nosnippet", raw)
        self.assertGreaterEqual(raw.count("noindex"), 2)
        directives = crawl._robots_directives(page, "googlebot")
        self.assertIn("noindex", directives)    # generic line
        self.assertIn("nosnippet", directives)  # Googlebot line
        self.assertIn("nofollow", directives)

    def test_other_ua_scoped_header_does_not_apply_to_google(self):
        page, _ = self.page("/bing-only")
        directives = crawl._robots_directives(page, "googlebot")
        self.assertNotIn("noindex", directives)
        self.assertNotIn("nofollow", directives)

    def test_scope_can_switch_inside_one_combined_field_value(self):
        page, _ = self.page("/multi-scope")
        google = crawl._robots_directives(page, "googlebot")
        bing = crawl._robots_directives(page, "bingbot")
        self.assertIn("max-snippet:0", google)
        self.assertIn("nofollow", google)
        self.assertNotIn("noindex", google)
        self.assertIn("noindex", bing)
        self.assertNotIn("nofollow", bing)


if __name__ == "__main__":
    unittest.main()
