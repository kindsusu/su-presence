# -*- coding: utf-8 -*-
"""미노출 진단 신호 — 출처 채널 분류와 원시 HTML 링크 도달 판정. 네트워크를 쓰지 않는다.

실행: python -m unittest discover tests
"""

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import collect  # noqa: E402
import crawl  # noqa: E402
import measure  # noqa: E402

HOST = "example.com"
BASE = "https://example.com"
QUERY = {"id": "Q1", "text": "제품 추천", "type": "nonbrand", "note": ""}


def row(engine, run_no, cited_urls=(), comps=(), outcome="observed", ours=False):
    return measure.make_row(
        "2026-10-01", "Q1", engine, run_no, "manual", True,
        ours if outcome == "observed" else None, list(cited_urls), ours, list(comps),
        outcome=outcome, query_fingerprint_value=measure.query_fingerprint(QUERY))


def summarize(rows):
    return measure.aggregate(rows, [dict(QUERY, fingerprint=measure.query_fingerprint(QUERY))],
                             HOST, BASE, cumulative=True)


class SourceChannelTests(unittest.TestCase):
    def test_known_platforms_and_subdomains(self):
        cases = {
            "https://www.youtube.com/watch?v=1": "video",
            "m.blog.naver.com": "naver_blog",
            "https://cafe.naver.com/x": "community",
            "someone.tistory.com": "open_blog",
            "https://namu.wiki/w/x": "wiki",
            "https://ko.wikipedia.org/wiki/x": "wiki",
            "https://www.gmarket.co.kr/item": "marketplace",
            "https://m.place.naver.com/x": "review_map",
            "https://www.jobplanet.co.kr/x": "jobs",
            "https://n.news.naver.com/x": "news",
            "https://www.mois.go.kr/x": "public",
            "https://rival.co.kr/pricing": "other",
            "": "other",
        }
        for value, want in cases.items():
            with self.subTest(value):
                self.assertEqual(measure.source_channel(value), want)

    def test_every_channel_has_a_label(self):
        for key, _label, suffixes in measure.SOURCE_CHANNELS:
            self.assertIn(key, measure.SOURCE_LABEL)
            self.assertTrue(suffixes)
        self.assertIn("other", measure.SOURCE_LABEL)


class SourceAggregationTests(unittest.TestCase):
    def test_platforms_leave_competitors_and_count_per_run(self):
        rows = [
            row("chatgpt", 1, ["https://www.youtube.com/watch?v=1", "https://rival.co.kr/a"],
                ["youtube.com", "rival.co.kr"]),
            row("chatgpt", 2, ["https://namu.wiki/w/x", "https://youtu.be/2"],
                ["namu.wiki", "youtu.be"]),
            row("chatgpt", 3),   # 출처 미기록
            row("gemini", 1, outcome="unmeasured"),
        ]
        summary = summarize(rows)
        self.assertEqual(summary["urls"]["competitors"], [{"domain": "rival.co.kr", "count": 1}])
        chatgpt = next(s for s in summary["sources"] if s["engine"] == "chatgpt")
        self.assertEqual((chatgpt["runs"], chatgpt["runs_with_sources"]), (3, 2))
        channels = {c["channel"]: c["runs"] for c in chatgpt["channels"]}
        # 한 회차에 유튜브 URL이 둘이어도 채널은 회차당 한 번만 센다
        self.assertEqual(channels, {"video": 2, "wiki": 1, "other": 1})
        gemini = next(s for s in summary["sources"] if s["engine"] == "gemini")
        self.assertEqual((gemini["runs"], gemini["runs_with_sources"]), (0, 0))

        md = measure.render_measure_md(summary)
        self.assertIn("## 출처 채널", md)
        self.assertIn("| 영상 | 2/2 | 미기록 |", md)
        self.assertIn("ChatGPT 2/3", md)

    def test_no_recorded_sources_says_so_instead_of_zero(self):
        md = measure.render_measure_md(summarize([row("chatgpt", 1), row("chatgpt", 2)]))
        self.assertIn("근거 채널을 판단할 수 없다", md)
        self.assertNotIn("| 영상 |", md)


class RecordSourcesTests(unittest.TestCase):
    @staticmethod
    def args(**kw):
        base = dict(record="chatgpt", query="Q1", run=1, cited="", competitors="", sources="",
                    brand="no", note="", login="in", search="on")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_sources_land_in_urls_and_domains_without_counting_as_citation(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = collect.record_one(self.args(
                sources="https://www.youtube.com/watch?v=1, namu.wiki, https://example.com/x"),
                tmp, HOST, "2026-10-01", [QUERY])
            self.assertEqual(code, 0)
            got = measure.load_log(os.path.join(tmp, "log.jsonl"))[0]
        self.assertFalse(got["cited"])
        self.assertEqual(got["cited_urls"], ["https://www.youtube.com/watch?v=1"])
        self.assertEqual(got["competitor_domains"], ["youtube.com", "namu.wiki"])

    def test_args_without_sources_still_work(self):
        legacy = types.SimpleNamespace(record="chatgpt", query="Q1", run=1, cited="",
                                       competitors="", brand="no", note="", login="in",
                                       search="on")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(collect.record_one(legacy, tmp, HOST, "2026-10-01", [QUERY]), 0)


def response(url, body="", status=200, content_type="text/html"):
    return {"status": status, "final_url": url, "headers": {}, "body": body,
            "ms": 1, "redirects": 0, "error": None, "content_type": content_type}


def crawl_with(bodies, seeds=()):
    def fake(url, **kw):
        return response(url, bodies.get(url, "<p>본문</p>"))
    coverage = {}
    with patch.object(crawl, "fetch", side_effect=fake):
        pages = crawl.crawl_site(BASE, 20, 0, [], seeds=list(seeds), coverage=coverage)
    return pages, coverage


def codes_for(pages, coverage):
    site = {"_coverage": coverage, "robots": {"present": True, "sitemap_declared": ["x"],
                                              "policies": {}, "raw": "", "status": 200},
            "sitemaps": [], "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": []},
            "llms": {"llms.txt": 200}, "_all_pages_status": []}
    findings = []
    crawl._check_link_discovery(findings, BASE, site, [p for p in pages if p["status"] == 200])
    return {f["code"]: f for f in findings}


class LinkDiscoveryTests(unittest.TestCase):
    def test_script_only_menu_is_flagged_even_when_sitemap_reaches_everything(self):
        pages, coverage = crawl_with(
            {BASE + "/": '<div id="app"></div><script>render()</script>'},
            seeds=[BASE + "/pricing", BASE + "/guide"])
        self.assertTrue(coverage["complete"])
        home = next(p for p in pages if p["url"] == BASE + "/")
        self.assertEqual(home["internal_links"], 0)
        got = codes_for(pages, coverage)
        self.assertIn("LINKS_NOT_IN_HTML", got)
        self.assertEqual(got["SITEMAP_ONLY_PAGES"]["data"]["count"], 2)
        self.assertEqual(got["SITEMAP_ONLY_PAGES"]["severity"], "warn")

    def test_linked_pages_are_not_orphans_despite_slash_or_scheme(self):
        pages, coverage = crawl_with(
            {BASE + "/": '<a href="/pricing/">요금</a><a href="http://example.com/guide">안내</a>'
                         '<a href="#top">위로</a>'},
            seeds=[BASE + "/pricing", BASE + "/guide"])
        home = next(p for p in pages if p["url"] == BASE + "/")
        self.assertEqual(home["internal_links"], 2)
        got = codes_for([p for p in pages if p["url"] != BASE + "/pricing/"], coverage)
        self.assertEqual(got, {})

    def test_incomplete_crawl_does_not_judge_orphans(self):
        pages, coverage = crawl_with({BASE + "/": "<p>링크 없음</p>"}, seeds=[BASE + "/a"])
        coverage = dict(coverage, complete=False)
        got = codes_for(pages, coverage)
        self.assertIn("LINKS_NOT_IN_HTML", got)
        self.assertNotIn("SITEMAP_ONLY_PAGES", got)

    def test_old_audit_pages_without_link_fields_are_not_judged(self):
        old = [{"url": BASE + "/", "status": 200}, {"url": BASE + "/a", "status": 200}]
        self.assertEqual(codes_for(old, {"complete": True}), {})

    def test_non_html_response_is_unknown_not_zero(self):
        with patch.object(crawl, "fetch", side_effect=lambda u, **kw: response(
                u, "%PDF", content_type="application/pdf")):
            pages = crawl.crawl_site(BASE, 5, 0, [])
        self.assertIsNone(pages[0]["internal_links"])

    def test_new_codes_have_report_text(self):
        import report
        for code in ("LINKS_NOT_IN_HTML", "SITEMAP_ONLY_PAGES"):
            self.assertIn(code, report.MSG_EN)
            self.assertIn(code, report.ROADMAP)


if __name__ == "__main__":
    unittest.main()
