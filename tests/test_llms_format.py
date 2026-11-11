# -*- coding: utf-8 -*-
"""llms.txt 형식 메모 회귀 테스트 — llmstxt.org 제안 형식 대조.

형식 메모는 순위·인용 요인이 아니다. 그래서 findings·scorecard에 들어가지 않고,
verify의 종료 코드를 바꾸지 않으며, 측정하지 못했으면 "문제 없음"이 아니라 미측정으로 남는다.
전부 합성 픽스처와 로컬 HTTP 서버만 쓴다 — 외부 사이트에 접속하지 않는다.
"""

import copy
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import drift  # noqa: E402
import report  # noqa: E402
import verify  # noqa: E402

CLEAN = (
    "# 예시 주식회사\n"
    "\n"
    "> 합성 시험용 한 줄 소개.\n"
    "\n"
    "자유 형식 소개 문단. [안내](/about) 같은 상대 링크도 소개부에서는 보지 않는다.\n"
    "- 소개부 목록 [자유](/free) 형식\n"
    "\n"
    "## 주요 페이지\n"
    "- [요금 안내](https://example.com/pricing): 합성 요금표\n"
    "- [지점 안내](https://example.com/branches)\n"
    "\n"
    "## 데이터 출처와 이용\n"
    "- 출처: 자체 운영 데이터\n"
    "- 갱신 주기: 매일\n"
)


def codes(notes):
    return [n["code"] for n in notes]


# ─────────────────────────────────────────────────────────── 1. 파서

class LlmsFormatNotesTests(unittest.TestCase):

    def test_clean_file_has_no_notes(self):
        self.assertEqual(crawl.llms_format_notes(CLEAN), [])

    def test_lane_doc_example_and_bom_are_clean(self):
        # lanes/geo.md 1번 예시와 같은 모양(일반 목록 항목은 링크가 아니어도 된다) + BOM
        text = "\ufeff" + CLEAN.replace("\n", "\r\n")
        self.assertEqual(crawl.llms_format_notes(text), [])

    def test_generated_package_shape_is_clean_apart_from_todo(self):
        text = ("# 예시\n\n> <<TODO: 한 문장>>\n\n## 주요 페이지\n"
                "- [홈](https://example.com/): <<TODO: 한 줄 설명>>\n\n"
                "## 데이터 출처와 이용\n- 출처: <<TODO: 원출처>>\n")
        self.assertEqual(crawl.llms_format_notes(text), [])

    def test_missing_h1(self):
        notes = crawl.llms_format_notes("예시 회사\n\n## 페이지\n- [홈](https://example.com/)\n")
        self.assertEqual(codes(notes), ["h1_missing"])
        self.assertEqual(notes[0]["lines"], ["1: 예시 회사"])

    def test_h2_is_not_h1(self):
        notes = crawl.llms_format_notes("## 예시\n- [홈](https://example.com/)\n")
        self.assertIn("h1_missing", codes(notes))

    def test_h1_not_first_line(self):
        text = "안내문\n# 예시\n> 소개\n## 페이지\n- [홈](https://example.com/)\n"
        self.assertEqual(codes(crawl.llms_format_notes(text)), ["h1_not_first"])

    def test_missing_summary_blockquote(self):
        text = "# 예시\n\n본문부터 시작\n\n## 페이지\n- [홈](https://example.com/)\n"
        notes = crawl.llms_format_notes(text)
        self.assertEqual(codes(notes), ["summary_missing"])
        self.assertEqual(notes[0]["lines"], ["3: 본문부터 시작"])

    def test_relative_links_in_sections(self):
        text = CLEAN + "\n## 기타\n- [소개](/about): 상대 경로\n- [문서](docs/a.md)\n"
        notes = crawl.llms_format_notes(text)
        self.assertEqual(codes(notes), ["relative_links"])
        self.assertEqual(len(notes[0]["lines"]), 2)
        self.assertTrue(notes[0]["lines"][0].endswith(": /about"))

    def test_malformed_link_lines(self):
        text = CLEAN + ("\n## 기타\n"
                        "- [띄어쓴 괄호] (https://example.com/a)\n"
                        "- [닫히지 않음](https://example.com/b\n"
                        "- 설명 먼저 [링크](https://example.com/c)\n"
                        "- [콜론 대신 대시](https://example.com/d) - 설명\n"
                        "- 링크 없는 일반 항목\n")
        notes = crawl.llms_format_notes(text)
        self.assertEqual(codes(notes), ["malformed_links"])
        self.assertEqual(len(notes[0]["lines"]), 4)

    def test_code_blocks_are_ignored(self):
        text = CLEAN + "\n## 예제\n```\n- [틀린 줄] (x)\n# 가짜 제목\n```\n"
        self.assertEqual(crawl.llms_format_notes(text), [])

    def test_no_link_list(self):
        notes = crawl.llms_format_notes("# 예시\n> 소개\n\n## 안내\n- 링크 없는 항목\n")
        self.assertEqual(codes(notes), ["no_links"])

    def test_size_over_100_kb(self):
        notes = crawl.llms_format_notes(CLEAN + "- 항목\n" * 20000)
        self.assertIn("too_large", codes(notes))
        self.assertEqual(crawl.llms_format_notes(CLEAN, size_bytes=crawl.LLMS_MAX_BYTES), [])
        self.assertEqual(codes(crawl.llms_format_notes(CLEAN, size_bytes=crawl.LLMS_MAX_BYTES + 1)),
                         ["too_large"])

    def test_html_document_stops_format_checks(self):
        notes = crawl.llms_format_notes("<!DOCTYPE html><html><body>app</body></html>")
        self.assertEqual(codes(notes), ["html_document"])

    def test_empty(self):
        self.assertEqual(codes(crawl.llms_format_notes("  \n\n")), ["empty"])

    def test_record_is_none_when_not_measured(self):
        self.assertIsNone(crawl.llms_format_record({"status": 404, "body": "x", "error": None}))
        self.assertIsNone(crawl.llms_format_record({"status": None, "body": "", "error": "timeout"}))
        self.assertIsNone(crawl.llms_format_record({"status": 200, "body": "x",
                                                    "error": "invalid_gzip"}))
        record = crawl.llms_format_record({"status": 200, "body": CLEAN, "error": "body_truncated",
                                           "truncated": True})
        self.assertTrue(record["truncated"])
        self.assertEqual(record["bytes"], len(CLEAN.encode("utf-8")))

    def test_messages_never_claim_ranking_effects(self):
        text = "안내\n## x\n- [a](/a)\n- [b] (c)\n" + "z" * (crawl.LLMS_MAX_BYTES + 1)
        text += "\n# 둘째\n본문\n"   # summary_missing까지 나오게 한다
        notes = crawl.llms_format_notes(text)
        self.assertGreaterEqual(len(notes), 4)
        for note in notes:
            for claim in ("순위", "랭킹", "인용률", "인용된다", "노출이 늘"):
                self.assertNotIn(claim, note["message"])


# ─────────────────────────────────────────────────────────── 2. crawl (로컬 서버)

class LlmsSiteHandler(BaseHTTPRequestHandler):
    llms_status = 200
    llms_body = b""

    def do_GET(self):
        if self.path == "/":
            status, ctype = 200, "text/html; charset=utf-8"
            body = ('<!doctype html><html lang="ko"><head><title>합성 시험 페이지 제목입니다</title>'
                    '<link rel="canonical" href="/"></head><body><h1>홈</h1>'
                    + "본문 " * 200 + "</body></html>").encode("utf-8")
        elif self.path == "/robots.txt":
            status, body, ctype = 200, b"User-agent: *\nAllow: /\n", "text/plain"
        elif self.path == "/llms.txt":
            status, body, ctype = self.llms_status, self.llms_body, "text/plain; charset=utf-8"
        else:
            status, body, ctype = 404, b"not found", "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _LlmsSite(unittest.TestCase):
    llms_status = 200
    llms_body = b""

    @classmethod
    def setUpClass(cls):
        handler = type("H", (LlmsSiteHandler,),
                       {"llms_status": cls.llms_status, "llms_body": cls.llms_body})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port
        with patch.object(sys, "stderr", io.StringIO()):
            cls.report = crawl.build_report(cls.base, 5, 0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)


class CrawlRecordsLlmsNotes(_LlmsSite):
    llms_body = (CLEAN + "\n## 기타\n- [소개](/about)\n").encode("utf-8")

    def test_notes_are_an_optional_site_field(self):
        record = self.report["site"]["llms_format"]
        self.assertEqual(codes(record["notes"]), ["relative_links"])
        self.assertEqual(record["bytes"], len(self.llms_body))
        self.assertFalse(record["truncated"])

    def test_notes_are_not_findings_or_scores(self):
        self.assertEqual(self.report["site"]["llms"]["llms.txt"], 200)
        for finding in self.report["findings"]:
            self.assertFalse(finding["code"].startswith("LLMS"), finding["code"])
        self.assertNotIn("LLMS_TXT_MISSING", self.report["scorecard"]["GEO"]["evidence"])

    def test_console_summary_shows_notes_as_reference(self):
        out = io.StringIO()
        with redirect_stdout(out):
            crawl.print_summary(self.report)
        text = out.getvalue()
        self.assertIn("llms.txt 형식 메모: 1건", text)
        self.assertIn("순위·인용 요인이 아니다", text)

    def test_report_drift_and_diff_accept_old_and_new_audits(self):
        old = copy.deepcopy(self.report)
        old["site"].pop("llms_format")
        for audit in (old, self.report):
            self.assertIn("<section id=\"p1\">", report.render(audit, "ko"))
            self.assertIsInstance(drift.audit_metrics(audit), dict)
        for before, after in ((old, self.report), (self.report, old)):
            result = verify.verify_diff(before, after)
            self.assertEqual([c for c in result["checks"] if c["id"] == "diff.new"
                              and c["status"] != "pass"], [])


class CrawlLeavesMissingLlmsUnmeasured(_LlmsSite):
    llms_status = 404
    llms_body = b"not found"

    def test_no_field_when_llms_txt_is_not_served(self):
        self.assertNotIn("llms_format", self.report["site"])
        out = io.StringIO()
        with redirect_stdout(out):
            crawl.print_summary(self.report)
        self.assertNotIn("형식 메모", out.getvalue())


class CrawlCleanLlms(_LlmsSite):
    llms_body = CLEAN.encode("utf-8")

    def test_clean_file_records_empty_notes(self):
        self.assertEqual(self.report["site"]["llms_format"]["notes"], [])


if __name__ == "__main__":
    unittest.main()
