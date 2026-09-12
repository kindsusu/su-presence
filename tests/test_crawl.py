# -*- coding: utf-8 -*-
"""crawl.py 회귀 테스트 — 네트워크를 쓰지 않는다.

실행: python -m unittest discover tests
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import crawl  # noqa: E402


# ─────────────────────────────────────────────────────────── robots 정책

class TestRobotsPolicy(unittest.TestCase):
    """test_audit.sh의 POLICY 8개 케이스를 그대로 옮긴 것."""

    CASES = [
        ("명시 차단", "User-agent: GPTBot\nDisallow: /\n", "GPTBot", "explicit-block"),
        ("명시 허용", "User-agent: GPTBot\nAllow: /\n", "GPTBot", "explicit-allow"),
        ("와일드카드 차단", "User-agent: *\nDisallow: /\n", "ClaudeBot", "star-block"),
        ("빈 Disallow=허용", "User-agent: *\nDisallow:\n", "GPTBot", "star-allow"),
        ("CRLF", "User-agent: GPTBot\r\nDisallow: /\r\n", "GPTBot", "explicit-block"),
        ("다중 UA 그룹", "User-agent: A\nUser-agent: B\nDisallow: /\n", "B", "explicit-block"),
        ("부분 제한", "User-agent: *\nDisallow: /admin/\n", "GPTBot", "star-partial"),
        ("빈 robots", "", "GPTBot", "none"),
    ]

    def test_policy_cases(self):
        for name, raw, ua, want in self.CASES:
            with self.subTest(name):
                self.assertEqual(crawl.robots_policy(raw, ua), want)

    def test_case_insensitive_ua(self):
        raw = "user-agent: gptbot\ndisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "explicit-block")

    def test_explicit_beats_star(self):
        raw = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "explicit-allow")
        self.assertEqual(crawl.robots_policy(raw, "ClaudeBot"), "star-block")

    def test_allow_wins_equal_specificity(self):
        raw = "User-agent: GPTBot\nDisallow: /\nAllow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "explicit-allow")

    def test_sitemap_line_does_not_split_group(self):
        raw = "User-agent: GPTBot\nSitemap: https://example.com/sitemap.xml\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "explicit-block")


class TestCrawlRules(unittest.TestCase):
    def test_prefers_own_group_over_star(self):
        raw = "User-agent: *\nDisallow: /\n\nUser-agent: su-presence-audit\nAllow: /\n"
        rules = crawl.crawl_rules(raw, crawl.UA.split('/')[0])
        self.assertTrue(crawl.crawl_allowed(rules, "/anything"))

    def test_falls_back_to_star(self):
        rules = crawl.crawl_rules("User-agent: *\nDisallow: /admin/\n", crawl.UA.split('/')[0])
        self.assertFalse(crawl.crawl_allowed(rules, "/admin/users"))
        self.assertTrue(crawl.crawl_allowed(rules, "/blog/post"))

    def test_longest_prefix_wins(self):
        rules = crawl.crawl_rules(
            "User-agent: *\nDisallow: /docs/\nAllow: /docs/public/\n", crawl.UA.split('/')[0])
        self.assertFalse(crawl.crawl_allowed(rules, "/docs/internal"))
        self.assertTrue(crawl.crawl_allowed(rules, "/docs/public/faq"))

    def test_no_rules_means_allowed(self):
        self.assertTrue(crawl.crawl_allowed([], "/anything"))


# ─────────────────────────────────────────────────────────── 사이트맵 SSRF

class TestSitemapCandidates(unittest.TestCase):
    BASE = "https://example.com"
    HOST = "example.com"

    def test_same_host_declaration_is_fetched(self):
        got = crawl.sitemap_candidates(self.BASE, self.HOST, ["https://example.com/sitemap.xml"])
        self.assertIn("https://example.com/sitemap.xml", got)

    def test_all_same_host_declarations_are_kept(self):
        declared = ["https://example.com/sitemap-%d.xml" % i for i in range(5)]
        got = crawl.sitemap_candidates(self.BASE, self.HOST, declared)
        for url in declared:
            self.assertIn(url, got)

    def test_foreign_and_nonurl_declarations_are_dropped(self):
        got = crawl.sitemap_candidates(self.BASE, self.HOST, [
            "http://127.0.0.1/steal",
            "https://evil.com/sitemap.xml",
            "--url=http://169.254.169.254/",
            "file:///etc/passwd",
        ])
        self.assertEqual(got, ["https://example.com/sitemap.xml",
                               "https://example.com/sitemap_index.xml"])


# ─────────────────────────────────────────────────────────── HTML 파싱

FIXTURE = """<!doctype html>
<html lang="ko">
<head>
<title>  예시 회사 페이지 </title>
<meta name="description" content="예시 설명입니다">
<meta name="robots" content="index, follow">
<meta name="naver-site-verification" content="abc123">
<meta property="og:title" content="OG 제목">
<meta property="og:image" content="https://example.com/og.png">
<link rel="canonical" href="https://example.com/a">
<style>body{color:red}</style>
<script>var hidden = "스크립트 본문은 세지 않는다";</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"Organization","name":"Example"},
  {"@type":["FAQPage","WebPage"]}]}
</script>
</head>
<body>
<h1>대표 제목</h1>
<p>본문 문단 하나.</p>
<a href="/b">다음</a><a href="https://other.example.org/x">외부</a>
</body></html>"""


class TestPageParser(unittest.TestCase):
    def setUp(self):
        self.p = crawl.PageParser()
        self.p.feed(FIXTURE)
        self.p.close()

    def test_head_fields(self):
        self.assertEqual(self.p.title, "예시 회사 페이지")
        self.assertEqual(self.p.meta_description, "예시 설명입니다")
        self.assertEqual(self.p.meta_robots, "index, follow")
        self.assertEqual(self.p.canonical, "https://example.com/a")
        self.assertEqual(self.p.lang, "ko")
        self.assertTrue(self.p.naver_site_verification)
        self.assertEqual(self.p.og["title"], "OG 제목")
        self.assertEqual(self.p.og["image"], "https://example.com/og.png")

    def test_h1_and_links(self):
        self.assertEqual(self.p.h1, ["대표 제목"])
        self.assertIn("/b", self.p.links)
        self.assertIn("https://other.example.org/x", self.p.links)

    def test_script_and_style_excluded_from_text(self):
        text = "".join(self.p._text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("스크립트 본문", text)
        self.assertNotIn("@context", text)
        self.assertIn("본문 문단 하나.", text)

    def test_text_chars_counts_characters_not_bytes(self):
        parser = crawl.PageParser()
        parser.feed("<html><body>한글다섯자다</body></html>")
        parser.close()
        self.assertEqual(parser.text_chars, 6)

    def test_jsonld_types_walk_graph(self):
        count, types = crawl.jsonld_types(self.p.jsonld_raw)
        self.assertEqual(count, 1)
        self.assertIn("Organization", types)
        self.assertIn("FAQPage", types)
        self.assertIn("WebPage", types)

    def test_broken_jsonld_is_not_counted(self):
        count, types = crawl.jsonld_types(["{not json"])
        self.assertEqual((count, types), (0, []))


class TestScriptDetection(unittest.TestCase):
    def test_korean_vs_english(self):
        self.assertEqual(crawl.script_of("한글 제목입니다"), "ko")
        self.assertEqual(crawl.script_of("An English page title"), "en")
        self.assertEqual(crawl.script_of("예시렌트카 | Example Rent a Car Seoul"), "ko")
        self.assertEqual(crawl.script_of(""), "en")


class TestNormalize(unittest.TestCase):
    def test_query_preserved_and_fragment_dropped(self):
        self.assertEqual(crawl.normalize("https://EXAMPLE.com/a?b=1#c"), "https://example.com/a?b=1")

    def test_empty_path_becomes_root(self):
        self.assertEqual(crawl.normalize("https://example.com"), "https://example.com/")

    def test_assets_are_not_pages(self):
        self.assertFalse(crawl.is_page("https://example.com/a.PNG"))
        self.assertFalse(crawl.is_page("https://example.com/app.js"))
        self.assertTrue(crawl.is_page("https://example.com/blog/post"))


# ─────────────────────────────────────────────────────────── findings 규칙

def page(url, **kw):
    base = {
        "url": url, "status": 200, "final_url": url, "title": "제목 %s" % url,
        # 유일한 설명 + 권장 길이(한글 70~80) 안쪽
        "meta_description": url[-1] + " " + "설명 " * 22,
        "meta_robots": None, "x_robots_tag": None,
        "canonical": url, "h1": ["제목"], "jsonld_count": 1,
        "jsonld_types": ["FAQPage"], "text_chars": 1200,
        "og": {}, "naver_site_verification": True, "lang": "ko",
        "response_ms": 10, "error": None,
    }
    base.update(kw)
    return base


def site(**kw):
    base = {
        "robots": {"status": 200, "present": True, "raw": "", "sitemap_declared":
                   ["https://example.com/sitemap.xml"],
                   "policies": {ua: "star-allow" for ua in crawl.ALL_UAS}},
        "sitemaps": [{"url": "https://example.com/sitemap.xml", "status": 200,
                      "is_index": False, "url_count": 1}],
        "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": []},
        "llms": {"llms.txt": 200, "llms-full.txt": 404},
        "hygiene": {"probe_404": 404, "redirect_hops": 0, "home_response_ms": 10,
                    "alt_host": {"host": "www.example.com", "result": "redirect",
                                 "status": 301, "location": "https://example.com/"}},
        "_all_pages_status": [],
    }
    base.update(kw)
    return base


def codes(findings):
    return {f["code"] for f in findings}


class TestFindings(unittest.TestCase):
    def test_clean_site_has_no_findings(self):
        pages = [page("https://example.com/", jsonld_types=["Organization", "FAQPage"]),
                 page("https://example.com/b")]
        findings, stats, board = crawl.analyze("https://example.com", site(), pages)
        self.assertEqual(codes(findings), set())
        self.assertEqual(board["SEO"]["status"], "ok")
        self.assertEqual(board["reputation"]["status"], "na")
        self.assertEqual(stats["pages_crawled"], 2)

    def test_noindex_from_meta_and_header(self):
        pages = [page("https://example.com/", meta_robots="noindex, nofollow"),
                 page("https://example.com/b", x_robots_tag="noindex")]
        findings, stats, board = crawl.analyze("https://example.com", site(), pages)
        self.assertIn("NOINDEX", codes(findings))
        self.assertEqual(stats["pages_noindex"], 2)
        self.assertEqual(board["SEO"]["status"], "bad")

    def test_duplicate_title_and_description(self):
        pages = [page("https://example.com/a", title="같은 제목", meta_description="같은 설명"),
                 page("https://example.com/b", title="같은 제목", meta_description="같은 설명")]
        findings, _, _ = crawl.analyze("https://example.com", site(), pages)
        self.assertIn("TITLE_DUPLICATE", codes(findings))
        self.assertIn("DESC_DUPLICATE", codes(findings))
        dup = [f for f in findings if f["code"] == "TITLE_DUPLICATE"][0]
        self.assertEqual(dup["data"]["pages"], 2)
        self.assertEqual(dup["data"]["ratio"], 1.0)

    def test_title_length_uses_korean_rule(self):
        ko_long = "가" * 40          # 한글 기준 25~30 초과
        en_ok = "A" * 40             # 영문 기준 50~60 이내
        pages = [page("https://example.com/a", title=ko_long),
                 page("https://example.com/b", title=en_ok)]
        findings, _, _ = crawl.analyze("https://example.com", site(), pages)
        long_finding = [f for f in findings if f["code"] == "TITLE_TOO_LONG"]
        self.assertEqual(len(long_finding), 1)
        self.assertEqual(long_finding[0]["urls"], ["https://example.com/a"])

    def test_description_length_uses_korean_rule(self):
        pages = [page("https://example.com/a", meta_description="다" * 120),
                 page("https://example.com/b", meta_description="B" * 120)]
        findings, _, _ = crawl.analyze("https://example.com", site(), pages)
        long_desc = [f for f in findings if f["code"] == "DESC_TOO_LONG"]
        self.assertEqual(long_desc[0]["urls"], ["https://example.com/a"])

    def test_missing_structure(self):
        pages = [page("https://example.com/a", jsonld_count=0, jsonld_types=[],
                      canonical=None, h1=[], meta_description=None, text_chars=40)]
        findings, _, board = crawl.analyze("https://example.com", site(), pages)
        for code in ("JSONLD_MISSING", "FAQ_MISSING", "ORG_JSONLD_MISSING",
                     "CANONICAL_MISSING", "H1_MISSING", "DESC_MISSING", "THIN_TEXT"):
            self.assertIn(code, codes(findings))
        self.assertEqual(board["AEO"]["status"], "ok")
        self.assertEqual(board["LLMO"]["status"], "ok")

    def test_canonical_not_self(self):
        pages = [page("https://example.com/a", canonical="https://example.com/other")]
        findings, _, _ = crawl.analyze("https://example.com", site(), pages)
        self.assertIn("CANONICAL_NOT_SELF", codes(findings))

    def test_ai_crawler_blocked_and_partial(self):
        policies = {ua: "star-allow" for ua in crawl.ALL_UAS}
        policies["OAI-SearchBot"] = "explicit-block"
        policies["Claude-SearchBot"] = "star-partial"
        policies["Yeti"] = "explicit-block"
        s = site()
        s["robots"]["policies"] = policies
        findings, _, board = crawl.analyze("https://example.com", s, [page("https://example.com/")])
        self.assertIn("AI_CRAWLER_BLOCKED", codes(findings))
        self.assertIn("AI_CRAWLER_PARTIAL", codes(findings))
        self.assertIn("NAVER_CRAWLER_BLOCKED", codes(findings))
        self.assertEqual(board["GEO"]["status"], "bad")
        self.assertEqual(board["NEO"]["status"], "bad")

    def test_undeclared_crawlers_are_info_only(self):
        s = site()
        s["robots"]["policies"] = {ua: "none" for ua in crawl.ALL_UAS}
        findings, _, board = crawl.analyze("https://example.com", s, [page("https://example.com/")])
        self.assertIn("AI_CRAWLER_UNDECLARED", codes(findings))
        self.assertEqual(board["GEO"]["status"], "ok")

    def test_site_level_hygiene(self):
        s = site(sitemaps=[{"url": "https://example.com/sitemap.xml", "status": 404,
                            "is_index": False, "url_count": 0}],
                 llms={"llms.txt": 404, "llms-full.txt": 404})
        s["robots"]["present"] = False
        s["robots"]["sitemap_declared"] = []
        s["hygiene"]["probe_404"] = 200
        s["hygiene"]["redirect_hops"] = 3
        s["hygiene"]["alt_host"]["result"] = "tls_fail"
        findings, _, _ = crawl.analyze("https://example.com", s,
                                       [page("https://example.com/", naver_site_verification=False)])
        for code in ("ROBOTS_MISSING", "SITEMAP_MISSING", "LLMS_TXT_MISSING", "SOFT_404",
                     "REDIRECT_HOPS", "ALT_HOST_UNREACHABLE", "NAVER_VERIFY_MISSING"):
            self.assertIn(code, codes(findings))

    def test_sitemap_crawl_mismatch(self):
        s = site(sitemap_vs_crawl={"only_in_sitemap": ["https://example.com/ghost"],
                                   "only_in_crawl": ["https://example.com/orphan"]})
        findings, _, _ = crawl.analyze("https://example.com", s, [page("https://example.com/")])
        mismatch = [f for f in findings if f["code"] == "SITEMAP_CRAWL_MISMATCH"][0]
        self.assertEqual(mismatch["data"], {"only_in_sitemap": 1, "only_in_crawl": 1})

    def test_broken_internal_links(self):
        s = site(_all_pages_status=[("https://example.com/", 200),
                                    ("https://example.com/gone", 500),
                                    ("https://example.com/dead", None)])
        findings, _, _ = crawl.analyze("https://example.com", s, [page("https://example.com/")])
        broken = [f for f in findings if f["code"] == "HTTP_ERROR"][0]
        self.assertEqual(broken["data"]["count"], 2)

    def test_every_finding_carries_a_known_lane_and_severity(self):
        s = site()
        s["robots"]["policies"] = {ua: "explicit-block" for ua in crawl.ALL_UAS}
        pages = [page("https://example.com/a", jsonld_count=0, jsonld_types=[], h1=[],
                      title=None, meta_description=None, canonical=None, text_chars=10)]
        findings, _, _ = crawl.analyze("https://example.com", s, pages)
        self.assertTrue(findings)
        for f in findings:
            self.assertIn(f["lane"], crawl.LANES)
            self.assertIn(f["severity"], ("critical", "warn", "info"))
            self.assertTrue(f["message"])
            self.assertIsInstance(f["urls"], list)
            self.assertIsInstance(f["data"], dict)

    def test_report_is_json_serializable(self):
        findings, stats, board = crawl.analyze("https://example.com", site(),
                                               [page("https://example.com/")])
        json.dumps({"findings": findings, "stats": stats, "scorecard": board},
                   ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
