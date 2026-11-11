# -*- coding: utf-8 -*-
"""사이트맵 위생 지표와 llms.txt 메모 등급 회귀 테스트.

사이트맵 위생(lastmod 누락·형식·일괄값, 다른 호스트, 중복, 파일 한도)은 순위·인용 요인이 아니라
프로토콜 준수·관리 상태다. 선택 필드(`site.sitemaps[].quality`)와 info/warn findings로만 남고
크롤 완전성 판정은 바꾸지 않는다. 옛 audit(필드 없음)도 report·drift·verify가 그대로 읽어야 한다.
전부 합성 픽스처와 로컬 HTTP 서버만 쓴다 — 외부 사이트에 접속하지 않는다.
"""

import copy
import io
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import drift  # noqa: E402
import report  # noqa: E402
import verify  # noqa: E402

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
QUALITY_CODES = {"SITEMAP_LIMIT_EXCEEDED", "SITEMAP_FOREIGN_HOST", "SITEMAP_DUPLICATE_URLS",
                 "SITEMAP_LASTMOD_INVALID", "SITEMAP_LASTMOD_MISSING", "SITEMAP_LASTMOD_UNIFORM"}


def urlset(entries):
    """entries: [(loc, lastmod 또는 None)] → 합성 urlset XML."""
    rows = []
    for loc, lastmod in entries:
        mod = "<lastmod>%s</lastmod>" % lastmod if lastmod is not None else ""
        rows.append("<url><loc>%s</loc>%s</url>" % (loc, mod))
    return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="%s">%s</urlset>' % (NS, "".join(rows))


def response(url, body="", status=200, headers=None, error=None, truncated=False):
    return {"status": status, "final_url": url, "headers": headers or {}, "body": body,
            "ms": 1, "redirects": 0, "error": error, "content_type": "application/xml",
            "truncated": truncated}


def read_one(body, headers=None, error=None, truncated=False, host="example.com"):
    """sitemap.xml 하나만 응답하는 가짜 fetch로 read_sitemaps를 돌린다."""
    base = "https://%s" % host

    def fake(url):
        if url == base + "/sitemap.xml":
            return response(url, body, headers=headers, error=error, truncated=truncated)
        return response(url, "", status=404)

    with patch.object(crawl, "fetch", side_effect=fake):
        results, urls = crawl.read_sitemaps(base, host, [])
    return results[0], urls


def quality_findings(sitemaps):
    findings = []
    crawl._check_sitemap_quality(findings, {"sitemaps": sitemaps})
    return {f["code"]: f for f in findings}


# ─────────────────────────────────────────────────────────── 1. W3C Datetime

class W3CDatetimeTests(unittest.TestCase):

    def test_accepted_forms(self):
        for value in ("2026", "2026-11", "2026-11-12", "2026-11-12T09:00Z",
                      "2026-11-12T09:00:00+09:00", "2026-11-12T09:00:00.5-05:00"):
            with self.subTest(value):
                self.assertTrue(crawl.w3c_datetime(value))

    def test_rejected_forms(self):
        for value in ("11/12/2026", "2026-11-12 09:00:00", "2026-11-12T09:00:00",
                      "2026-13-01", "2026-02-30", "2026-11-12T24:00Z", "Thu, 12 Nov 2026", ""):
            with self.subTest(value):
                self.assertFalse(crawl.w3c_datetime(value))


# ─────────────────────────────────────────────────────────── 2. 파일 단위 지표

class SitemapQualityTests(unittest.TestCase):

    def test_metrics_for_mixed_sitemap(self):
        entries = [("https://example.com/p%d" % i, "2026-11-01") for i in range(10)]
        entries += [("https://example.com/odd", "11/01/2026"),
                    ("https://example.com/nomod", None),
                    ("https://www.example.com/www", "2026-10-01"),
                    ("https://example.com/p1", "2026-11-01")]   # 중복
        result, urls = read_one(urlset(entries))
        q = result["quality"]
        self.assertTrue(result["parsed"])
        self.assertFalse(result["truncated"])
        self.assertEqual(q["entries"], 14)
        self.assertEqual(q["lastmod_missing"], 1)
        self.assertEqual(q["lastmod_invalid"], 1)
        self.assertEqual(q["lastmod_invalid_examples"], ["11/01/2026"])
        self.assertEqual(q["lastmod_top_value"], "2026-11-01")
        self.assertEqual(q["lastmod_top_count"], 11)
        self.assertEqual(q["lastmod_top_share"], round(11 / 14, 3))
        self.assertEqual(q["foreign_host"], 1)
        self.assertEqual(q["foreign_host_examples"], ["https://www.example.com/www"])
        self.assertEqual(q["duplicate_urls"], 1)
        self.assertEqual(q["duplicate_examples"], ["https://example.com/p1"])
        self.assertFalse(q["over_url_limit"])
        self.assertFalse(q["over_size_limit"])
        # 기존 계약은 그대로다 — URL 목록·개수는 위생 지표와 무관하게 이전과 같다
        self.assertEqual(result["url_count"], 14)
        self.assertEqual(len(urls), 14)

    def test_extension_locs_are_not_entries(self):
        body = ('<urlset xmlns="%s" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
                '<url><loc>https://example.com/a</loc><lastmod>2026-11-01</lastmod>'
                '<image:image><image:loc>https://cdn.example.net/a.png</image:loc></image:image>'
                '</url></urlset>' % NS)
        q = read_one(body)[0]["quality"]
        self.assertEqual(q["entries"], 1)
        self.assertEqual(q["foreign_host"], 0)

    def test_sitemap_index_entries_are_measured(self):
        body = ('<sitemapindex xmlns="%s"><sitemap><loc>https://example.com/a.xml</loc></sitemap>'
                '<sitemap><loc>https://other.example.net/b.xml</loc>'
                '<lastmod>2026-11-01</lastmod></sitemap></sitemapindex>' % NS)

        def fake(url):
            if url == "https://example.com/sitemap.xml":
                return response(url, body)
            if url == "https://example.com/a.xml":
                return response(url, urlset([("https://example.com/x", "2026-11-02")]))
            return response(url, "", status=404)

        with patch.object(crawl, "fetch", side_effect=fake):
            results, _ = crawl.read_sitemaps("https://example.com", "example.com", [])
        index = results[0]
        self.assertTrue(index["is_index"])
        self.assertEqual(index["quality"]["entries"], 2)
        self.assertEqual(index["quality"]["lastmod_missing"], 1)
        self.assertEqual(index["quality"]["foreign_host"], 1)
        child = [r for r in results if r["url"] == "https://example.com/a.xml"][0]
        self.assertEqual(child["quality"]["lastmod_missing"], 0)

    def test_url_limit_does_not_change_completeness(self):
        body = urlset([("https://example.com/%d" % i, None)
                       for i in range(crawl.SITEMAP_MAX_URLS + 1)])
        self.assertLess(len(body), crawl.MAX_BODY)
        result, urls = read_one(body)
        self.assertTrue(result["quality"]["over_url_limit"])
        self.assertTrue(result["parsed"])
        self.assertFalse(result["truncated"])
        self.assertIsNone(result["error"])
        self.assertEqual(len(urls), crawl.SITEMAP_MAX_URLS + 1)
        got = quality_findings([result])
        self.assertEqual(got["SITEMAP_LIMIT_EXCEEDED"]["severity"], "info")
        self.assertTrue(got["SITEMAP_LIMIT_EXCEEDED"]["data"]["sitemaps"][0]["over_url_limit"])

    def test_declared_size_over_limit_is_recorded_for_truncated_body(self):
        size = crawl.SITEMAP_MAX_BYTES + 1
        result, _ = read_one("<urlset", headers={"content-length": str(size)},
                             error="body_truncated", truncated=True)
        # 완전성 판정은 기존 그대로 — 받지 못한 사이트맵은 여전히 파싱 실패·잘림이다
        self.assertFalse(result["parsed"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["quality"], {"bytes": size, "over_size_limit": True, "partial": True})
        got = quality_findings([result])
        self.assertEqual(set(got), {"SITEMAP_LIMIT_EXCEEDED"})

    def test_compressed_length_is_not_taken_as_uncompressed_size(self):
        size = crawl.SITEMAP_MAX_BYTES + 1
        result, _ = read_one("<urlset", headers={"content-length": str(size),
                                                 "content-encoding": "gzip"},
                             error="body_truncated", truncated=True)
        self.assertNotIn("quality", result)

    def test_unparsed_or_failed_sitemaps_have_no_quality(self):
        self.assertNotIn("quality", read_one("not xml")[0])
        self.assertNotIn("quality", read_one("", error="timeout")[0])


# ─────────────────────────────────────────────────────────── 3. findings

class SitemapQualityFindingTests(unittest.TestCase):

    def test_clean_sitemap_has_no_quality_findings(self):
        entries = [("https://example.com/p%d" % i, "2026-10-%02d" % (i + 1)) for i in range(12)]
        result, _ = read_one(urlset(entries))
        self.assertEqual(quality_findings([result]), {})

    def test_codes_severities_and_wording(self):
        entries = [("https://example.com/p%d" % i, "2026-11-01") for i in range(40)]
        entries += [("https://example.com/odd", "2026/11/01"), ("https://example.com/nomod", None),
                    ("https://www.example.com/www", "2026-10-01"), ("https://example.com/p0", "2026-11-01")]
        result, _ = read_one(urlset(entries))
        got = quality_findings([result])
        self.assertEqual(set(got), QUALITY_CODES - {"SITEMAP_LIMIT_EXCEEDED"})
        self.assertEqual(got["SITEMAP_FOREIGN_HOST"]["severity"], "info")
        for code in ("SITEMAP_DUPLICATE_URLS", "SITEMAP_LASTMOD_INVALID",
                     "SITEMAP_LASTMOD_MISSING", "SITEMAP_LASTMOD_UNIFORM"):
            self.assertEqual(got[code]["severity"], "info", code)
        for finding in got.values():
            self.assertEqual(finding["lane"], "SEO")
            self.assertIn("순위·인용 요인이 아니라", finding["message"])
        self.assertEqual(got["SITEMAP_LASTMOD_MISSING"]["data"]["entries"], 44)
        self.assertEqual(got["SITEMAP_LASTMOD_UNIFORM"]["data"]["sitemaps"][0]["share"],
                         round(41 / 44, 3))
        self.assertEqual(got["SITEMAP_FOREIGN_HOST"]["urls"], ["https://www.example.com/www"])

    def test_uniform_needs_minimum_sample(self):
        small = [("https://example.com/p%d" % i, "2026-11-01")
                 for i in range(crawl.SITEMAP_UNIFORM_MIN - 1)]
        self.assertNotIn("SITEMAP_LASTMOD_UNIFORM", quality_findings([read_one(urlset(small))[0]]))
        mixed = [("https://example.com/p%d" % i, "2026-11-01" if i < 10 else "2026-10-%02d" % i)
                 for i in range(20)]
        self.assertNotIn("SITEMAP_LASTMOD_UNIFORM", quality_findings([read_one(urlset(mixed))[0]]))

    def test_old_audit_without_quality_yields_nothing(self):
        old = {"url": "https://example.com/sitemap.xml", "status": 200, "is_index": False,
               "url_count": 3, "parsed": True, "truncated": False, "error": None}
        self.assertEqual(quality_findings([old]), {})
        findings = []
        crawl._check_sitemap_quality(findings, {})
        self.assertEqual(findings, [])

    def test_english_report_templates_format(self):
        entries = [("https://example.com/p%d" % i, "2026-11-01") for i in range(10)]
        entries += [("https://example.com/odd", "x"), ("https://example.com/nomod", None),
                    ("https://www.example.com/www", None), ("https://example.com/p0", None)]
        result, _ = read_one(urlset(entries))
        over = dict(result, quality=dict(result["quality"], over_url_limit=True))
        for finding in quality_findings([over]).values():
            with self.subTest(finding["code"]):
                self.assertIn(finding["code"], report.MSG_EN)
                text = report.finding_message(finding, "en")
                self.assertNotEqual(text, finding["message"])   # 국문 폴백이 아니다
                self.assertIn("Not a ranking or citation factor", text)


# ─────────────────────────────────────────────────────────── 4. 로컬 서버 E2E + 옛 audit 호환

class _Handler(BaseHTTPRequestHandler):
    sitemap = b""

    def do_GET(self):
        port = self.server.server_port
        if self.path == "/":
            body = ('<!doctype html><html lang="ko"><head><title>합성 시험 페이지 제목입니다</title>'
                    '<link rel="canonical" href="/"></head><body><h1>홈</h1>'
                    '<a href="/a">a</a>' + "본문 " * 200 + "</body></html>").encode("utf-8")
            status, ctype = 200, "text/html; charset=utf-8"
        elif self.path == "/a":
            body = ('<!doctype html><html lang="ko"><head><title>합성 시험 하위 페이지 제목</title>'
                    '<link rel="canonical" href="/a"></head><body><h1>a</h1><a href="/">홈</a>'
                    + "본문 " * 200 + "</body></html>").encode("utf-8")
            status, ctype = 200, "text/html; charset=utf-8"
        elif self.path == "/robots.txt":
            body = ("User-agent: *\nAllow: /\nSitemap: http://127.0.0.1:%d/sitemap.xml\n" % port).encode()
            status, ctype = 200, "text/plain"
        elif self.path == "/sitemap.xml":
            body = self.sitemap.replace(b"{PORT}", str(port).encode())
            status, ctype = 200, "application/xml"
        else:
            body, status, ctype = b"not found", 404, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class LocalSiteSitemapQuality(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        sitemap = urlset([("http://127.0.0.1:{PORT}/", "2026-11-01"),
                          ("http://127.0.0.1:{PORT}/a", "2026-11-01"),
                          ("http://127.0.0.1:{PORT}/a", None)]).encode()
        handler = type("H", (_Handler,), {"sitemap": sitemap})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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

    def sitemap(self, audit):
        return [s for s in audit["site"]["sitemaps"] if s["url"].endswith("/sitemap.xml")][0]

    def test_quality_is_recorded_and_completeness_unchanged(self):
        sm = self.sitemap(self.report)
        self.assertEqual(sm["quality"]["duplicate_urls"], 1)
        self.assertEqual(sm["quality"]["lastmod_missing"], 1)
        # 위생 findings가 있어도 크롤 범위 판정은 그대로다
        self.assertTrue(self.report["coverage"]["complete"], self.report["coverage"])
        codes = {f["code"] for f in self.report["findings"]}
        self.assertIn("SITEMAP_DUPLICATE_URLS", codes)
        self.assertIn("SITEMAP_LASTMOD_MISSING", codes)
        self.assertNotIn("CRAWL_INCOMPLETE", codes)

    def test_old_audit_still_loads_in_report_drift_and_verify(self):
        old = copy.deepcopy(self.report)
        for sm in old["site"]["sitemaps"]:
            sm.pop("quality", None)
        old["findings"] = [f for f in old["findings"] if f["code"] not in QUALITY_CODES]
        for audit in (old, self.report):
            for lang in ("ko", "en"):
                self.assertIn("<section", report.render(audit, lang))
            self.assertIsInstance(drift.audit_metrics(audit), dict)
        result = verify.verify_diff(old, self.report)
        new = [c for c in result["checks"] if c["id"] == "diff.new"][0]
        self.assertNotEqual(new["status"], "fail")   # 위생 findings는 critical이 아니다
        back = verify.verify_diff(self.report, old)
        self.assertIn(back["exit_code"], (0, 1, 2))


# ─────────────────────────────────────────────────────────── 5. llms.txt 메모 등급

LLMS_CLEAN = ("# 예시\n\n> 합성 소개.\n\n## 주요 페이지\n"
              "- [요금](https://example.com/pricing): 합성 요금표\n")


class LlmsNoteLevelTests(unittest.TestCase):

    def test_levels(self):
        notes = crawl.llms_format_notes("# 예시\n\n본문\n\n## 페이지\n- [홈](/)\n")
        levels = {n["code"]: n["level"] for n in notes}
        self.assertEqual(levels, {"summary_missing": "info", "relative_links": "warn"})
        for note in crawl.llms_format_notes("안내\n" + "x" * (crawl.LLMS_MAX_BYTES + 1)):
            self.assertIn(note["level"], ("info", "warn"))

    def test_non_http_links_are_separate_from_relative(self):
        text = LLMS_CLEAN + "- [메일](mailto:help@example.com)\n- [파일](ftp://example.com/a)\n"
        notes = crawl.llms_format_notes(text)
        self.assertEqual([n["code"] for n in notes], ["non_http_links"])
        self.assertEqual(len(notes[0]["lines"]), 2)
        self.assertEqual(notes[0]["level"], "warn")

    def run_check(self, body):
        def fake(url):
            return {"status": 200, "final_url": url, "headers": {}, "body": body, "ms": 1,
                    "redirects": 0, "error": None, "content_type": "text/plain"}
        checks = []
        session = verify.Session("example.com", fetch=fake, delay=0)
        verify.check_llms(checks, session, "https://example.com", {"llms": body})
        return [c for c in checks if c["id"] == "llms.format"][0]

    def test_verify_info_only_notes_pass(self):
        check = self.run_check("# 예시\n\n## 페이지\n- [홈](https://example.com/)\n")
        self.assertEqual(check["status"], "pass")
        self.assertEqual([n["code"] for n in check["evidence"]["notes"]], ["summary_missing"])
        self.assertIn("순위·인용 요인이 아니다", check["message"])

    def test_verify_warn_notes_never_fail(self):
        check = self.run_check("소개 없음\n## 페이지\n- [메일](mailto:a@example.com)\n- [x] (y)\n")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(verify.verification_incomplete([check]), [])


if __name__ == "__main__":
    unittest.main()
