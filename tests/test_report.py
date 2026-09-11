# -*- coding: utf-8 -*-
"""report.py 회귀 테스트 — 네트워크를 쓰지 않는다.

실행: python -m unittest discover tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import report  # noqa: E402

XSS_URL = 'https://example.com/<script>alert("x")</script>'

AUDIT = {
    "schema": "su-multi-geo/audit/1",
    "generated_at": "2026-01-02T03:04:05+00:00",
    "target": {"input": "example.com", "base": "https://example.com", "host": "example.com"},
    "site": {
        "robots": {"status": 200, "present": True, "raw": "User-agent: *\nDisallow: /admin/",
                   "policies": {"GPTBot": "explicit-block", "OAI-SearchBot": "star-partial",
                                "ChatGPT-User": "none", "ClaudeBot": "star-allow",
                                "Claude-SearchBot": "none", "Claude-User": "none",
                                "PerplexityBot": "none", "Perplexity-User": "none",
                                "Google-Extended": "none", "Yeti": "star-allow",
                                "Daumoa": "none"},
                   "sitemap_declared": []},
        "sitemaps": [{"url": "https://example.com/sitemap.xml", "status": 404,
                      "is_index": False, "url_count": 0}],
        "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": []},
        "llms": {"llms.txt": 404, "llms-full.txt": 404},
        "hygiene": {"probe_404": 200, "redirect_hops": 0, "home_response_ms": 120,
                    "alt_host": {"host": "www.example.com", "result": "tls_fail",
                                 "status": None, "location": None}},
    },
    "pages": [
        {"url": "https://example.com/", "status": 200, "final_url": "https://example.com/",
         "title": "예시 <b>제목</b>", "meta_description": None, "meta_robots": None,
         "x_robots_tag": None, "canonical": None, "h1": [], "jsonld_count": 0,
         "jsonld_types": [], "text_chars": 42, "og": {}, "naver_site_verification": False,
         "lang": "ko", "response_ms": 120, "error": None},
        {"url": XSS_URL, "status": 200, "final_url": XSS_URL,
         "title": None, "meta_description": None, "meta_robots": None,
         "x_robots_tag": None, "canonical": None, "h1": [], "jsonld_count": 0,
         "jsonld_types": [], "text_chars": 10, "og": {}, "naver_site_verification": False,
         "lang": None, "response_ms": 30, "error": None},
    ],
    "stats": {"pages_crawled": 2, "unique_titles": 1, "unique_descriptions": 0,
              "pages_with_jsonld": 0, "pages_noindex": 0},
    "findings": [
        {"lane": "SEO", "severity": "critical", "code": "SITEMAP_MISSING",
         "message": "접근 가능한 사이트맵이 없다 <b>주의</b>", "urls": [XSS_URL], "data": {}},
        {"lane": "AEO", "severity": "warn", "code": "JSONLD_MISSING",
         "message": "JSON-LD가 한 건도 없는 페이지가 2개다.", "urls": [], "data":
             {"count": 2, "ratio": 1.0}},
        {"lane": "GEO", "severity": "critical", "code": "AI_CRAWLER_BLOCKED",
         "message": "AI 크롤러 1종이 차단돼 있다.", "urls": [], "data": {"blocked": ["GPTBot"]}},
        {"lane": "NEO", "severity": "warn", "code": "NAVER_VERIFY_MISSING",
         "message": "naver-site-verification 메타가 없다.", "urls": [], "data": {}},
        {"lane": "LLMO", "severity": "warn", "code": "ORG_JSONLD_MISSING",
         "message": "Organization JSON-LD가 없다.", "urls": [], "data": {}},
    ],
    "scorecard": {
        "SEO": {"status": "bad", "evidence": ["SITEMAP_MISSING"]},
        "AEO": {"status": "warn", "evidence": ["JSONLD_MISSING"]},
        "GEO": {"status": "bad", "evidence": ["AI_CRAWLER_BLOCKED"]},
        "LLMO": {"status": "warn", "evidence": ["ORG_JSONLD_MISSING"]},
        "NEO": {"status": "warn", "evidence": ["NAVER_VERIFY_MISSING"]},
        "reputation": {"status": "na", "evidence": [], "note": "사이트 밖 표면"},
    },
}


class TestRender(unittest.TestCase):
    def setUp(self):
        self.ko = report.render(AUDIT, "ko")
        self.en = report.render(AUDIT, "en")

    def test_all_eight_sections_exist(self):
        for html in (self.ko, self.en):
            for n in range(1, 9):
                self.assertIn('<section id="p%d">' % n, html)

    def test_one_chip_per_lane(self):
        # 레인이 늘면 이 숫자도 같이 는다. 고정 6은 KEO 를 세면서 깨졌다.
        self.assertEqual(self.ko.count('class="chip '), len(report.LANES))
        for lane in report.LANES:
            self.assertIn('<span class="lane">%s</span>' % lane, self.ko)

    def test_tabs_and_hash_routing(self):
        for n in range(1, 9):
            self.assertIn('href="#p%d"' % n, self.ko)
        self.assertIn("hashchange", self.ko)

    def test_no_unsubstituted_placeholders(self):
        for html in (self.ko, self.en):
            self.assertNotIn("${", html)

    def test_host_and_timestamp_come_from_audit(self):
        self.assertIn("example.com", self.ko)
        self.assertIn("2026-01-02", self.ko)

    def test_external_input_is_escaped(self):
        for html in (self.ko, self.en):
            self.assertNotIn("<script>alert", html)
            self.assertIn("&lt;script&gt;alert", html)
        # 국문은 audit.json의 message를 그대로 싣는다 — 태그가 살아나가면 안 된다
        self.assertNotIn("사이트맵이 없다 <b>주의</b>", self.ko)
        self.assertIn("&lt;b&gt;주의&lt;/b&gt;", self.ko)

    def test_glossary_terms_are_annotated(self):
        self.assertIn('<span class="t" tabindex="0" data-d=', self.ko)
        self.assertNotIn("<details", self.ko.split('<section id="p8"')[0].split("class=\"t\"")[0])

    def test_roadmap_is_generated_from_findings(self):
        self.assertIn("SITEMAP_MISSING", self.ko.split('<section id="p6">')[1])
        self.assertIn("AI_CRAWLER_BLOCKED", self.ko.split('<section id="p6">')[1])

    def test_english_messages_are_rewritten_from_data(self):
        # 용어 주석이 단어를 <span>으로 감싸므로 용어가 없는 구간으로 확인한다
        self.assertIn("is never handed to search engines", self.en)
        self.assertIn("2 pages carry no", self.en)

    def test_theme_tokens_are_three_tier(self):
        self.assertIn("@media (prefers-color-scheme: dark)", self.ko)
        self.assertIn(':root:not([data-theme="light"])', self.ko)
        self.assertIn(':root[data-theme="dark"]', self.ko)

    def test_layout_rules(self):
        self.assertIn("word-break:keep-all", self.ko.replace(" ", ""))
        self.assertEqual(self.ko.count(".wrap{max-width:1060px"), 1)
        self.assertIn("overflow-x:auto", self.ko.replace(" ", ""))


class TestCli(unittest.TestCase):
    def test_writes_report_next_to_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json")
            with open(audit, "w", encoding="utf-8") as fh:
                json.dump(AUDIT, fh, ensure_ascii=False)
            rc = subprocess.call(
                [sys.executable, os.path.join(ROOT, "tools", "report.py"), audit],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(rc, 0)
            out = os.path.join(tmp, "report.html")
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as fh:
                self.assertIn('<section id="p1">', fh.read())

    def test_rejects_foreign_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "audit.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"schema": "something/else"}, fh)
            rc = subprocess.call(
                [sys.executable, os.path.join(ROOT, "tools", "report.py"), bad],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
