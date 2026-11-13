# -*- coding: utf-8 -*-
"""robots.txt 응답 분류·제품 토큰·관리 블록·Content-Signal·깨진 JSON-LD·평가 범위 회귀 테스트.

전부 합성 픽스처와 로컬 HTTP 서버만 쓴다 — 외부 사이트에 접속하지 않는다.
"""

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import generate  # noqa: E402
import report  # noqa: E402
import verify  # noqa: E402


def res(status, body="", error=None):
    return {"status": status, "body": body, "error": error}


# ─────────────────────────────────────────────────────────── 1. 응답 분류

class RobotsFetchStateTests(unittest.TestCase):
    def test_classification_follows_rfc9309(self):
        cases = [
            (res(200, "User-agent: *\nAllow: /\n"), "ok"),
            (res(200, ""), "ok"),
            (res(200, "x", "body_truncated"), "ok"),
            (res(200, "<!doctype html><html>not found</html>"), "absent"),
            (res(404), "absent"), (res(410), "absent"), (res(403), "absent"), (res(401), "absent"),
            (res(429), "unreachable"), (res(500), "unreachable"), (res(503), "unreachable"),
            (res(None, error="timeout"), "unreachable"),
            (res(None, error="dns_fail"), "unreachable"),
            (res(200, "", "invalid_gzip"), "unreachable"),
            (res(301, "", "external_redirect_blocked"), "unreachable"),
        ]
        for response, want in cases:
            with self.subTest(response=response):
                self.assertEqual(crawl.robots_fetch_state(response), want)


class RobotsHandler(BaseHTTPRequestHandler):
    robots_status = 503
    robots_body = b""

    def do_GET(self):
        if self.path == "/robots.txt":
            status, body, ctype = self.robots_status, self.robots_body, "text/plain"
        elif self.path == "/":
            status, ctype = 200, "text/html; charset=utf-8"
            body = ('<!doctype html><html lang="ko"><head><title>합성 시험 페이지 제목입니다</title>'
                    '<link rel="canonical" href="/"></head><body><h1>홈</h1>'
                    '<a href="/busy">busy</a><a href="/down">down</a><a href="/gone">gone</a>'
                    '<script type="application/ld+json">{"@type": "Organization",}</script>'
                    + "본문 " * 200 + "</body></html>").encode("utf-8")
        elif self.path == "/busy":
            status, body, ctype = 429, b"slow down", "text/plain"
        elif self.path == "/down":
            status, body, ctype = 503, b"maintenance", "text/plain"
        else:
            status, body, ctype = 404, b"not found", "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, *args):
        self.server.requests.append(self.path)

    def log_message(self, *_args):
        pass


class CleanSiteHandler(RobotsHandler):
    """깨진 링크 없는 한 페이지 사이트 — robots.txt 응답만 범위 판정에 영향을 준다."""

    def do_GET(self):
        if self.path == "/":
            body = ('<!doctype html><html lang="ko"><head><title>합성 시험 페이지 제목입니다</title>'
                    '<link rel="canonical" href="/"></head><body><h1>홈</h1>'
                    + "본문 " * 200 + "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class _LocalSite(unittest.TestCase):
    status = 503
    body = b""
    handler = RobotsHandler

    @classmethod
    def setUpClass(cls):
        handler = type("H", (cls.handler,), {"robots_status": cls.status, "robots_body": cls.body})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port
        with patch.object(sys, "stderr", io.StringIO()):
            cls.report = crawl.build_report(cls.base, 10, 0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def codes(self):
        return {f["code"] for f in self.report["findings"]}


class RobotsUnreachableServer(_LocalSite):
    status = 503

    def test_policies_are_unknown_not_none(self):
        robots = self.report["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "unreachable")
        self.assertEqual(set(robots["policies"].values()), {"unknown"})

    def test_no_requests_after_policy_failure(self):
        self.assertEqual(self.server.requests, ["/robots.txt"])
        self.assertFalse(self.report["pages"])
        before = dict(self.report, findings=[{"code": "TITLE_MISSING", "severity": "critical",
                                              "lane": "SEO", "urls": [self.base]}])
        checks = {c["id"]: c for c in verify.verify_diff(before, self.report)["checks"]}
        self.assertFalse(checks["diff.resolved"]["evidence"]["items"])
        self.assertEqual(checks["diff.pages"]["status"], "skip")
        self.assertTrue({"SITEMAP_MISSING", "LLMS_TXT_MISSING", "SOFT_404",
                         "NAVER_VERIFY_MISSING"}.isdisjoint(self.codes()))

    def test_only_unreachable_finding_and_no_default_allow_claims(self):
        codes = self.codes()
        self.assertIn("ROBOTS_UNREACHABLE", codes)
        for code in ("ROBOTS_MISSING", "AI_CRAWLER_UNDECLARED", "AI_CRAWLER_BLOCKED",
                     "AI_CRAWLER_PARTIAL", "AI_TRAINING_BLOCKED", "DAUM_CRAWLER_UNDECLARED",
                     "DAUM_CRAWLER_BLOCKED", "NAVER_CRAWLER_BLOCKED"):
            self.assertNotIn(code, codes)
        finding = [f for f in self.report["findings"] if f["code"] == "ROBOTS_UNREACHABLE"][0]
        self.assertEqual((finding["lane"], finding["severity"]), ("SEO", "warn"))
        self.assertIn("미확인", finding["message"])
        self.assertEqual(finding["data"]["status"], 503)

    def test_policy_lanes_are_not_reported_ok(self):
        board = self.report["scorecard"]
        for lane in crawl.ROBOTS_POLICY_LANES:
            self.assertNotEqual(board[lane]["status"], "ok", lane)
            self.assertEqual(board[lane]["unassessed"], ["crawler_policy"])
        reach = crawl.lane_coverage(board, self.report["coverage"])
        self.assertFalse(reach["conclusive"])

    def test_console_and_html_render_unknown_without_allow_wording(self):
        out = io.StringIO()
        with redirect_stdout(out):
            crawl.print_summary(self.report)
        text = out.getvalue()
        self.assertIn("미확인", text)
        self.assertNotIn("기본 허용", text)
        self.assertIn("평가 범위", text)
        self.assertIn("종합 판정 없음", text)
        for lang in ("ko", "en"):
            html = report.render(self.report, lang)
            self.assertNotIn("${", html)
        ko = report.render(self.report, "ko")
        self.assertIn("robots.txt를 읽지 못했다", ko)
        self.assertIn("평가 범위", ko)


class RobotsTooManyRequestsServer(_LocalSite):
    status = 429

    def test_429_is_unreachable_like_5xx(self):
        self.assertEqual(self.report["site"]["robots"]["fetch_state"], "unreachable")
        self.assertIn("ROBOTS_UNREACHABLE", self.codes())
        self.assertNotIn("ROBOTS_MISSING", self.codes())


class RobotsUnreachableCleanSite(_LocalSite):
    status = 503
    handler = CleanSiteHandler

    def test_unreachable_robots_still_marks_crawl_incomplete(self):
        self.assertEqual(self.report["coverage"]["reasons"], ["robots_unavailable"])
        self.assertFalse(self.report["coverage"]["complete"])


class RobotsAbsentCleanSite(_LocalSite):
    """404 robots.txt는 RFC 9309상 '제한 없음'이다 — 크롤 범위를 줄이지 않는다."""
    status = 404
    handler = CleanSiteHandler

    def test_404_robots_does_not_make_crawl_incomplete(self):
        self.assertEqual(self.report["site"]["robots"]["fetch_state"], "absent")
        self.assertNotIn("robots_unavailable", self.report["coverage"]["reasons"])
        self.assertEqual(self.report["coverage"]["reasons"], [])
        self.assertTrue(self.report["coverage"]["complete"])
        self.assertNotIn("CRAWL_INCOMPLETE", self.codes())
        self.assertTrue(crawl.lane_coverage(self.report["scorecard"],
                                            self.report["coverage"])["crawl_complete"])
        html = report.render(self.report, "ko")
        self.assertNotIn("크롤 불완전", html)


class PageFindingsWithReadablePolicy(_LocalSite):
    status = 200
    body = b"User-agent: *\nAllow: /\n"

    def test_throttled_pages_are_not_broken_links(self):
        codes = self.codes()
        self.assertIn("HTTP_THROTTLED", codes)
        throttled = [f for f in self.report["findings"] if f["code"] == "HTTP_THROTTLED"][0]
        self.assertEqual(sorted(throttled["urls"]), [self.base + "/busy", self.base + "/down"])
        self.assertEqual(throttled["data"]["statuses"], {"429": 1, "503": 1})
        broken = [f for f in self.report["findings"] if f["code"] == "HTTP_ERROR"][0]
        self.assertEqual(broken["urls"], [self.base + "/gone"])
        # 완전성 의미는 바꾸지 않는다 — 못 잰 페이지는 여전히 불완전 사유다.
        self.assertIn("http_errors", self.report["coverage"]["reasons"])

    def test_broken_only_jsonld_is_invalid_not_missing(self):
        home = [p for p in self.report["pages"] if p["url"] == self.base + "/"][0]
        self.assertEqual((home["jsonld_count"], home["jsonld_invalid"]), (0, 1))
        self.assertIn("JSONLD_INVALID", self.codes())
        missing = [f for f in self.report["findings"] if f["code"] == "JSONLD_MISSING"]
        self.assertFalse(missing and self.base + "/" in missing[0]["urls"])


class NestedStartServer(_LocalSite):
    status = 200
    body = b"User-agent: *\nDisallow: /private\n"

    def test_start_url_uses_origin_policy_and_keeps_path_and_query(self):
        for suffix, allowed in (("/private?id=1", False), ("/public/?id=2", True)):
            with self.subTest(suffix=suffix), patch.object(sys, "stderr", io.StringIO()):
                self.server.requests.clear()
                result = crawl.build_report(self.base + suffix, 1, 0, direct=True)
                self.assertEqual(self.server.requests[0], "/robots.txt")
                self.assertEqual(suffix in self.server.requests, allowed)
                self.assertEqual(result["target"]["base"], self.base)
                self.assertEqual(result["target"]["input"], self.base + suffix)


class TruncatedRobotsServer(_LocalSite):
    status = 200
    body = b"User-agent: *\nAllow: /\n" + b"# padding\n" * 100 + b"Disallow: /private\n"

    def test_incomplete_raw_never_becomes_a_replacement(self):
        with tempfile.TemporaryDirectory() as out, patch.object(sys, "stderr", io.StringIO()):
            generate.run("robots", self.report, {}, out)
            draft = os.path.join(out, "robots.txt")
            with open(draft, encoding="utf-8") as fh:
                self.assertIn("Disallow: /private", fh.read())
            with patch.object(crawl, "MAX_BODY", 256):
                partial = crawl.build_report(self.base, 1, 0, direct=True)
            self.assertIn("robots_truncated", partial["coverage"]["reasons"])
            self.assertNotIn("Disallow: /private", partial["site"]["robots"]["raw"])
            ctx = generate.run("all", partial, {}, out)
            self.assertNotIn("robots.txt", ctx.files)
            self.assertFalse(os.path.exists(draft))
            self.assertTrue(any("잘렸다" in note for note in ctx.todos))


class RobotsAbsentServer(_LocalSite):
    status = 404

    def test_404_keeps_allow_all_semantics(self):
        robots = self.report["site"]["robots"]
        self.assertEqual(robots["fetch_state"], "absent")
        self.assertEqual(set(robots["policies"].values()), {"none"})
        codes = self.codes()
        self.assertIn("ROBOTS_MISSING", codes)
        self.assertIn("AI_CRAWLER_UNDECLARED", codes)
        self.assertNotIn("ROBOTS_UNREACHABLE", codes)


# ─────────────────────────────────────────────────────────── 2. 제품 토큰

class ProductTokenTests(unittest.TestCase):
    def test_version_suffix_matches_product_token(self):
        raw = "User-agent: GPTBot/1.1\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "explicit-block")
        self.assertEqual(crawl.robots_policy(raw, "gptbot"), "explicit-block")

    def test_token_is_not_a_prefix_match(self):
        raw = "User-agent: GPTBot-Image\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "none")
        raw = "User-agent: GPTBot\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "GPTBot-Image"), "none")

    def test_daumoa_and_daum_are_distinct(self):
        raw = "User-agent: DAUM\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "Daumoa"), "none")
        self.assertEqual(crawl.robots_policy(raw, "DAUM"), "explicit-block")
        raw = "User-agent: Daumoa/4.0\nDisallow: /\n"
        self.assertEqual(crawl.robots_policy(raw, "DAUM"), "none")
        self.assertEqual(crawl.robots_policy(raw, "Daumoa"), "explicit-block")

    def test_own_crawl_rules_use_the_same_matching(self):
        raw = "User-agent: *\nDisallow: /\n\nUser-agent: su-presence-audit/2.1\nAllow: /\n"
        rules = crawl.crawl_rules(raw, crawl.UA)
        self.assertTrue(crawl.crawl_allowed(rules, "/anything"))

    def test_nonstandard_values_are_recorded_and_reported(self):
        raw = ("User-agent: Mozilla/5.0 (compatible; GPTBot/1.1; +https://openai.com/gptbot)\n"
               "User-agent: ClaudeBot/1.0\nDisallow: /\n\nUser-agent: Yeti\nAllow: /\n")
        agents = crawl.robots_nonstandard_agents(raw)
        self.assertEqual([a["token"] for a in agents], ["Mozilla", "ClaudeBot"])
        # 전체 브라우저 UA 문자열은 GPTBot이 아니다 — 토큰은 Mozilla다.
        self.assertEqual(crawl.robots_policy(raw, "GPTBot"), "none")
        self.assertEqual(crawl.robots_policy(raw, "ClaudeBot"), "explicit-block")
        site = _site(raw, nonstandard_agents=agents)
        findings, _, _ = crawl.analyze("https://example.com", site, [_page()])
        found = [f for f in findings if f["code"] == "ROBOTS_NONSTANDARD_UA"][0]
        self.assertEqual(found["severity"], "info")
        self.assertIn("Mozilla/5.0", found["message"])


# ─────────────────────────────────────────────────────────── 3. 관리 블록·Content-Signal

CF_ROBOTS = """# BEGIN Cloudflare Managed content

User-Agent: *
Content-signal: search=yes,ai-train=no
Allow: /

User-agent: Amazonbot
Disallow: /

User-agent: GPTBot
Disallow: /

# END Cloudflare Managed Content

User-agent: *
Disallow: /admin/
Sitemap: https://example.com/sitemap.xml
"""


def _page(url="https://example.com/", **kw):
    base = {"url": url, "status": 200, "final_url": url, "title": "합성 제목 %s" % url,
            "meta_description": "설명 " * 30, "meta_robots": None, "x_robots_tag": None,
            "canonical": url, "h1": ["제목"], "jsonld_count": 1,
            "jsonld_types": ["Organization", "FAQPage"], "text_chars": 1200, "og": {},
            "naver_site_verification": True, "lang": "ko", "response_ms": 10, "error": None,
            "internal_links": 3}
    base.update(kw)
    return base


def _site(raw, **robots_extra):
    robots = {"status": 200, "present": bool(raw), "raw": raw,
              "policies": {ua: crawl.robots_policy(raw, ua) for ua in crawl.ALL_UAS},
              "sitemap_declared": ["https://example.com/sitemap.xml"]}
    robots.update(robots_extra)
    return {
        "robots": robots,
        "sitemaps": [{"url": "https://example.com/sitemap.xml", "status": 200,
                      "is_index": False, "url_count": 1}],
        "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": []},
        "llms": {"llms.txt": 200, "llms-full.txt": 404},
        "hygiene": {"probe_404": 404, "redirect_hops": 0, "home_response_ms": 10,
                    "alt_host": {"host": "www.example.com", "result": "redirect",
                                 "status": 301, "location": "https://example.com/"}},
        "_all_pages_status": [],
    }


class CdnManagedTests(unittest.TestCase):
    def test_marker_is_detected_case_insensitively_with_blocked_uas(self):
        info = crawl.cdn_managed_info(CF_ROBOTS)
        self.assertEqual(info, {"provider": "cloudflare", "blocked_uas": ["Amazonbot", "GPTBot"],
                                "end_marker": True})
        lower = CF_ROBOTS.replace("BEGIN Cloudflare Managed content", "begin cloudflare managed CONTENT")
        self.assertIsNotNone(crawl.cdn_managed_info(lower))
        self.assertIsNone(crawl.cdn_managed_info("User-agent: *\nAllow: /\n"))

    def test_finding_is_information_only(self):
        site = _site(CF_ROBOTS, cdn_managed=crawl.cdn_managed_info(CF_ROBOTS),
                     content_signals=crawl.robots_content_signals(CF_ROBOTS))
        findings, _, board = crawl.analyze("https://example.com", site, [_page()])
        managed = [f for f in findings if f["code"] == "ROBOTS_CDN_MANAGED"][0]
        self.assertEqual(managed["severity"], "info")
        self.assertIn("대시보드", managed["message"])
        self.assertIn("GPTBot", managed["message"])

    def test_generate_keeps_block_and_explains_edge_injection(self):
        audit = _audit_for(CF_ROBOTS)
        out = tempfile.mkdtemp(prefix="su-cf-")
        try:
            ctx = generate.run("all", audit, {}, out)
            with open(os.path.join(out, "robots.txt"), encoding="utf-8") as fh:
                draft = fh.read()
            with open(os.path.join(out, "DEPLOY.md"), encoding="utf-8") as fh:
                deploy = fh.read()
        finally:
            shutil.rmtree(out, ignore_errors=True)
        # 기존 제한은 그대로 — 관리 블록이 막은 GPTBot에 Allow를 얹지 않는다.
        self.assertTrue(draft.startswith(CF_ROBOTS.rstrip("\n")))
        self.assertEqual(crawl.robots_policy(draft, "GPTBot"), "explicit-block")
        self.assertIn("Cloudflare 관리 블록", deploy)
        self.assertIn("대시보드", deploy)
        self.assertTrue(any("엣지에서 주입" in t for t in ctx.todos))


class ContentSignalTests(unittest.TestCase):
    def test_parsed_per_group_and_never_changes_policy(self):
        signals = crawl.robots_content_signals(CF_ROBOTS)
        self.assertEqual(signals[0]["agents"], ["*"])
        self.assertEqual(signals[0]["signals"], {"search": "yes", "ai-train": "no"})
        stripped = CF_ROBOTS.replace("Content-signal: search=yes,ai-train=no\n", "")
        for ua in crawl.ALL_UAS:
            self.assertEqual(crawl.robots_policy(CF_ROBOTS, ua), crawl.robots_policy(stripped, ua), ua)

    def test_star_only_signal_notes_named_groups_do_not_inherit(self):
        site = _site(CF_ROBOTS, content_signals=crawl.robots_content_signals(CF_ROBOTS))
        findings, _, _ = crawl.analyze("https://example.com", site, [_page()])
        found = [f for f in findings if f["code"] == "CONTENT_SIGNAL_PRESENT"][0]
        self.assertEqual((found["lane"], found["severity"]), ("GEO", "info"))
        self.assertIn("접근 제어가 아니며", found["message"])
        self.assertEqual(found["data"]["named_groups_without_signal"], ["GPTBot"])
        self.assertIn("물려받지 않으므로", found["message"])

    def test_directive_name_is_case_insensitive(self):
        raw = "User-agent: GPTBot\nCONTENT-SIGNAL: ai-train=no\nAllow: /\n"
        self.assertEqual(crawl.robots_content_signals(raw)[0]["signals"], {"ai-train": "no"})


# ─────────────────────────────────────────────────────────── 4. 생성기·호환

def _audit_for(raw, policies=None, **robots_extra):
    robots = {"status": 200, "present": bool(raw), "raw": raw,
              "policies": policies or {ua: crawl.robots_policy(raw, ua) for ua in crawl.ALL_UAS},
              "sitemap_declared": []}
    robots.update(robots_extra)
    return {
        "schema": crawl.SCHEMA, "generated_at": "2026-11-01T00:00:00+00:00",
        "target": {"input": "example.com", "base": "https://example.com", "host": "example.com"},
        "coverage": {"complete": True, "reasons": []},
        "site": {"robots": robots,
                 "sitemaps": [], "sitemap_vs_crawl": {"only_in_sitemap": [], "only_in_crawl": []},
                 "llms": {"llms.txt": 404}, "hygiene": {"probe_404": 404, "redirect_hops": 0,
                                                       "home_response_ms": 1,
                                                       "alt_host": {"result": "na"}}},
        "pages": [_page()],
        "stats": {"pages_crawled": 1, "unique_titles": 1, "unique_descriptions": 1,
                  "pages_with_jsonld": 1, "pages_noindex": 0},
        "findings": [],
        "scorecard": {},
    }


class GenerateUnknownPolicyTests(unittest.TestCase):
    def test_unknown_policy_adds_no_allow_and_leaves_todo(self):
        """원문을 모르면 robots.txt 초안을 아예 만들지 않는다 — 덮어쓰면 서버의 Disallow가 지워진다."""
        audit = _audit_for("", policies={ua: "unknown" for ua in crawl.ALL_UAS},
                           status=503, fetch_state="unreachable")
        out = tempfile.mkdtemp(prefix="su-unk-")
        try:
            ctx = generate.run("robots", audit, {}, out)
            robots_written = os.path.exists(os.path.join(out, "robots.txt"))
            with open(os.path.join(out, generate.OWNERSHIP_MANIFEST), encoding="utf-8") as fh:
                owned = json.load(fh)["files"]
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertFalse(robots_written)
        self.assertNotIn("robots.txt", ctx.files)
        self.assertNotIn("robots.txt", owned)
        self.assertNotIn("robots_after", ctx.notes)
        self.assertEqual(ctx.notes["robots_unread"]["status"], 503)
        todo = [t for t in ctx.todos if "robots.txt를 읽지 못했다" in t]
        self.assertEqual(len(todo), 1)
        self.assertIn("HTTP 503", todo[0])
        self.assertIn("초안을 만들지 않았다", todo[0])

    def test_unreadable_robots_deploy_doc_explains_and_drops_stale_draft(self):
        out = tempfile.mkdtemp(prefix="su-unk-")
        try:
            # 이전 실행(정상 robots)이 남긴 초안은 새 실행에서 지워져야 한다 — 올라가면 원문을 덮는다
            generate.run("all", _audit_for("User-agent: *\nDisallow: /admin/\n"), {}, out)
            self.assertTrue(os.path.exists(os.path.join(out, "robots.txt")))
            audit = _audit_for("", policies={ua: "unknown" for ua in crawl.ALL_UAS},
                               status=503, fetch_state="unreachable")
            generate.run("all", audit, {}, out)
            robots_written = os.path.exists(os.path.join(out, "robots.txt"))
            with open(os.path.join(out, "DEPLOY.md"), encoding="utf-8") as fh:
                deploy = fh.read()
            pkg = verify.load_package(out)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertFalse(robots_written)
        section = deploy.split("## 2. robots.txt", 1)[1].split("\n## ", 1)[0]
        self.assertIn("robots.txt 초안을 만들지 않았다", section)
        self.assertIn("HTTP 503", section)
        self.assertNotIn("기존 원문은 한 줄도", section)
        self.assertNotIn("```diff", section)

        # verify는 패키지에 robots.txt가 없으면 정책 비교를 건너뛴다 (실패로 세지 않는다)
        class FakeSession:
            def get(self, url):
                return {"status": 200, "body": "User-agent: *\nDisallow: /private/\n",
                        "error": None}

        self.assertIsNone(pkg["robots"])
        checks = []
        verify.check_robots(checks, FakeSession(), "https://example.com", audit, pkg)
        status = {c["id"]: c["status"] for c in checks}
        self.assertEqual(status["robots.policy"], "skip")
        self.assertNotIn("fail", [status["robots.policy"], status["robots.preserved"]])


class LegacyAuditTests(unittest.TestCase):
    def test_old_page_without_invalid_field_is_still_missing(self):
        old = _page(jsonld_count=0, jsonld_types=[])
        old.pop("internal_links")
        findings, _, _ = crawl.analyze("https://example.com", _site("User-agent: *\nAllow: /\n"),
                                       [old])
        codes = {f["code"] for f in findings}
        self.assertIn("JSONLD_MISSING", codes)
        self.assertNotIn("JSONLD_INVALID", codes)

    def test_old_robots_without_new_fields_keeps_previous_findings(self):
        site = _site("")
        site["robots"]["present"] = False
        site["robots"]["policies"] = {ua: "none" for ua in crawl.ALL_UAS}
        findings, _, board = crawl.analyze("https://example.com", site, [_page()])
        codes = {f["code"] for f in findings}
        self.assertIn("ROBOTS_MISSING", codes)
        self.assertIn("AI_CRAWLER_UNDECLARED", codes)
        self.assertNotIn("ROBOTS_UNREACHABLE", codes)
        self.assertEqual(board["GEO"]["status"], "ok")
        self.assertNotIn("unassessed", board["GEO"])


# ─────────────────────────────────────────────────────────── 5. 평가 범위

class LaneCoverageTests(unittest.TestCase):
    def test_counts_and_conclusive_flag(self):
        board = crawl.scorecard([])
        reach = crawl.lane_coverage(board, {"complete": True})
        self.assertEqual((reach["assessed"], reach["total"]), (6, 7))
        self.assertEqual(reach["unassessed"], ["reputation"])
        self.assertTrue(reach["conclusive"])
        self.assertFalse(crawl.lane_coverage(board, {"complete": False})["conclusive"])
        self.assertFalse(crawl.lane_coverage(board, None)["conclusive"])

    def test_unassessed_lane_keeps_bad_but_marks_partial(self):
        findings = [{"lane": "GEO", "severity": "warn", "code": "X", "message": "m",
                     "urls": [], "data": {}}]
        board = crawl.scorecard(findings, {"GEO": ["crawler_policy"], "NEO": ["crawler_policy"]})
        self.assertEqual(board["GEO"]["status"], "warn")
        self.assertEqual(board["NEO"]["status"], "na")
        reach = crawl.lane_coverage(board, {"complete": True})
        self.assertEqual(reach["unassessed"], ["NEO", "reputation"])
        self.assertEqual(reach["partial"], ["GEO"])
        self.assertFalse(reach["conclusive"])

    def test_report_shows_scope_and_never_a_clean_verdict_when_partial(self):
        audit = _audit_for("User-agent: *\nAllow: /\n")
        audit["scorecard"] = crawl.scorecard([])
        audit["coverage"] = {"complete": False, "reasons": ["max_pages_reached"]}
        html = report.render(audit, "ko")
        self.assertIn("평가 범위 6/7 레인", html)
        self.assertIn("전체 양호 판정으로 읽지 않는다", html)
        audit["coverage"] = {"complete": True, "reasons": []}
        html = report.render(audit, "ko")
        self.assertNotIn("전체 양호 판정으로 읽지 않는다", html)
        self.assertIn("미평가: reputation", html)

    def test_unknown_status_value_renders_as_unknown(self):
        audit = _audit_for("")
        audit["scorecard"] = {"GEO": {"status": "something-new", "evidence": []}}
        audit["site"]["robots"]["policies"] = {"GPTBot": "unknown"}
        html = report.render(audit, "en")
        self.assertIn("could not be read", html)

    def test_old_audit_without_coverage_or_new_fields_renders(self):
        audit = copy.deepcopy(_audit_for("User-agent: *\nAllow: /\n"))
        audit.pop("coverage")
        audit["scorecard"] = {"SEO": {"status": "ok", "evidence": []}}
        html = report.render(audit, "ko")
        self.assertIn("평가 범위 1/7 레인", html)

    def test_old_audit_without_coverage_says_unknown_not_incomplete(self):
        audit = copy.deepcopy(_audit_for("User-agent: *\nAllow: /\n"))
        audit.pop("coverage")
        audit["scorecard"] = crawl.scorecard([])
        text, conclusive, why = report.reach_line(audit, report.L["ko"])
        self.assertFalse(conclusive)
        self.assertEqual(why, "범위 정보 없음")
        html = report.render(audit, "ko")
        self.assertIn("범위 정보 없음", html)
        self.assertNotIn("크롤 불완전", html)
        _t, _c, why_en = report.reach_line(audit, report.L["en"])
        self.assertEqual(why_en, "no coverage data")
        audit["coverage"] = {"complete": False, "reasons": ["max_pages_reached"]}
        self.assertEqual(report.reach_line(audit, report.L["ko"])[2], "크롤 불완전")


# ─────────────────────────────────────────────────────────── 6. FAQ 기대치

class FaqExpectationTests(unittest.TestCase):
    def test_deploy_does_not_promise_rich_results_or_citations(self):
        audit = _audit_for("User-agent: *\nAllow: /\n")
        site = {"name": "합성 주식회사", "faqs": [
            {"page_url": "https://example.com/", "q": "합성 질문인가요?", "a": "합성 답변입니다."}]}
        out = tempfile.mkdtemp(prefix="su-faq-")
        try:
            generate.run("all", audit, site, out)
            with open(os.path.join(out, "DEPLOY.md"), encoding="utf-8") as fh:
                deploy = fh.read()
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertIn("2026-05-07부터 FAQ 리치 결과를 보여 주지 않는다", deploy)
        self.assertIn("실제로 보이고 계속 관리될 때만", deploy)
        self.assertIn("인용된다는 보장도 없다", deploy)
        self.assertIn("더 이상 Google 리치 결과 대상이 아니다", deploy)


if __name__ == "__main__":
    unittest.main()
