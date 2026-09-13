# -*- coding: utf-8 -*-
"""collect.py 데이터 판정 회귀 테스트. 네트워크는 사용하지 않는다."""

import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import collect  # noqa: E402
import measure  # noqa: E402


HOST = "example.com"
QUERY = {"id": "Q1", "text": "가격 비교", "type": "nonbrand", "note": ""}


def padded(engine, body):
    return body + " " * (collect.SANE_MIN[engine] + 1)


class DomainBoundaryTest(unittest.TestCase):
    def test_url_owner_uses_hostname_boundary(self):
        urls = ["https://notexample.com/a", "https://blog.example.com/b"]
        self.assertEqual(collect.cited_urls_in(urls, HOST), [urls[1]])

    def test_visible_host_rejects_suffix_collision_and_query_echo(self):
        self.assertFalse(collect.seen("<p>notexample.com</p>", HOST))
        self.assertFalse(collect.seen(
            "<title>example.com</title><form><textarea>example.com</textarea></form>",
            HOST, "example.com"))
        self.assertTrue(collect.seen(
            "<title>example.com</title><p>공식 사이트 example.com</p>", HOST, "example.com"))
        self.assertTrue(collect.seen(
            '<form><input value="example.com"></form><p>공식 사이트 example.com</p>',
            HOST, "example.com"))
        self.assertTrue(collect.seen("<p>공식 사이트: blog.example.com</p>", HOST))

    def test_tag_attribute_is_not_visible_exposure(self):
        self.assertFalse(collect.seen('<a href="https://example.com/a">결과</a>', HOST))

    def test_external_url_path_does_not_count_as_visible_host(self):
        self.assertFalse(collect.seen(
            '<p>https://competitor.test/redirect?target=example.com</p>', HOST))


class GoogleAioTest(unittest.TestCase):
    def setUp(self):
        self.old_render = collect._render
        self.addCleanup(setattr, collect, "_render", self.old_render)

    def test_only_explicit_aio_container_links_are_sources(self):
        html = padded("google", """
          <div>AI 개요</div>
          <div class="organic"><a href="https://example.com/organic">">example.com</a></div>
          <section data-attrid="SGEAnswer">
            <a href="https://source.example/article">출처</a>
          </section>
        """)
        collect._render = lambda _url: html
        organic, fired, sources = collect.google("다른 질의", HOST)
        self.assertTrue(organic)
        self.assertTrue(fired)
        self.assertEqual(sources, ["https://source.example/article"])

    def test_fired_without_source_boundary_is_unmeasured(self):
        collect._render = lambda _url: padded(
            "google", '<div>AI 개요</div><a href="https://example.com/organic">결과</a>')
        with self.assertRaises(collect.Unmeasured):
            collect.google("질의", HOST)

    def test_empty_or_unclosed_source_container_is_unmeasured(self):
        samples = [
            '<div>AI 개요</div><div data-attrid="SGEAnswer"></div>',
            '<div>AI 개요</div><div data-attrid="SGEAnswer"><span>',
        ]
        for sample in samples:
            collect._render = lambda _url, sample=sample: padded("google", sample)
            with self.subTest(sample=sample), self.assertRaises(collect.Unmeasured):
                collect.google("질의", HOST)

    def test_void_elements_do_not_leak_into_following_organic_links(self):
        collect._render = lambda _url: padded(
            "google", '<div>AI 개요</div><div data-attrid="SGEAnswer">'
            '<img><a href="https://source.test/a">출처</a><br></div>'
            '<a href="https://example.com/organic">일반 검색</a>')
        self.assertEqual(collect.google("질의", HOST)[2], ["https://source.test/a"])

    def test_google_text_in_path_does_not_remove_external_source(self):
        collect._render = lambda _url: padded(
            "google", '<div>AI 개요</div><div data-attrid="SGEAnswer">'
            '<a href="https://source.test/google.com-review">출처</a></div>')
        self.assertEqual(collect.google("질의", HOST)[2],
                         ["https://source.test/google.com-review"])


class NaverSourcesTest(unittest.TestCase):
    def setUp(self):
        self.old_get = collect._get
        self.addCleanup(setattr, collect, "_get", self.old_get)

    @staticmethod
    def serp():
        return padded("naver", '<div data-block-id="ai-briefing/x"></div>'
                       '<script>{"apiURL":"https://api.example/sources"}</script>')

    def responses(self, source_body=None, error=None):
        calls = []

        def fake(url, *args, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return self.serp()
            if error:
                raise error
            return source_body

        collect._get = fake

    def test_source_api_failure_is_not_zero_citations(self):
        self.responses(error=TimeoutError("late"))
        with self.assertRaises(collect.Unmeasured):
            collect.naver("질의", HOST)

    def test_invalid_or_missing_sources_event_is_unmeasured(self):
        for body in ("event: done\ndata: {}\n", "event: sources\ndata: not-json\n",
                     'event: sources\ndata: [{}]\n',
                     'event: sources\ndata: ["bad"]\n',
                     'event: sources\ndata: [{"url":"javascript:bad"}]\n'):
            with self.subTest(body=body):
                self.responses(source_body=body)
                with self.assertRaises(collect.Unmeasured):
                    collect.naver("질의", HOST)

    def test_explicit_empty_sources_event_is_measured_empty(self):
        self.responses(source_body="event: sources\ndata: []\n")
        self.assertEqual(collect.naver("질의", HOST), (False, True, []))


class RecordContractTest(unittest.TestCase):
    @staticmethod
    def args(**kw):
        base = dict(record="chatgpt", query="Q1", run=1, cited="", competitors="",
                    brand="no", note="", login="in", search="on")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_unknown_query_and_foreign_cited_url_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(collect.record_one(
                self.args(query="TYPO"), tmp, HOST, "2026-09-13", [QUERY]), 2)
            self.assertEqual(collect.record_one(
                self.args(cited="https://notexample.com/a"), tmp, HOST,
                "2026-09-13", [QUERY]), 2)
            self.assertFalse(os.path.exists(os.path.join(tmp, "log.jsonl")))

    def test_record_stores_query_and_campaign_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = collect.record_one(
                self.args(cited="https://www.example.com/a"), tmp, HOST,
                "2026-09-13", [QUERY])
            self.assertEqual(code, 0)
            row = measure.load_log(os.path.join(tmp, "log.jsonl"))[0]
            self.assertEqual(row["query_fingerprint"], measure.query_fingerprint(QUERY))
            self.assertEqual(row["campaign_id"], measure.query_set_fingerprint([QUERY])[:16])


class CollectMainIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.audit = os.path.join(self.root, "audit.json")
        measure.write_json(self.audit, {"schema": "su-presence/audit/1",
                                        "target": {"base": "https://example.com",
                                                   "host": HOST}})
        mdir = measure.measure_dir(self.audit)
        measure.write_json(os.path.join(mdir, "queries.json"),
                           {"schema": measure.SCHEMA_QUERIES, "queries": [QUERY]})
        self.old_unattended = collect.UNATTENDED
        self.old_today = collect.measure.today_str
        collect.measure.today_str = lambda: "2026-09-13"
        self.addCleanup(setattr, collect, "UNATTENDED", self.old_unattended)
        self.addCleanup(setattr, collect.measure, "today_str", self.old_today)

    def run_main(self, argv):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return collect.main(argv)

    def test_unmeasured_keeps_fingerprint_out_of_denominator_and_reservation_is_fulfilled(self):
        def cannot_measure(_query, _host):
            raise collect.Unmeasured("source boundary missing")

        collect.UNATTENDED = (("google_aio", "구글", cannot_measure),)
        self.assertEqual(self.run_main([self.audit, "--runs", "1", "--pause", "0",
                                        "--browser"]), 0)
        log = os.path.join(measure.measure_dir(self.audit), "log.jsonl")
        rows = measure.load_log(log)
        fingerprint = measure.query_fingerprint(QUERY)
        self.assertTrue(all(row["query_fingerprint"] == fingerprint for row in rows))
        google = next(row for row in rows if row["engine"] == "google_aio")
        self.assertEqual(google["outcome"], "unmeasured")
        summary = measure.aggregate(rows, [QUERY], HOST, "https://example.com")
        self.assertEqual(summary["rows"], 0)
        self.assertEqual(summary["quality"]["unmeasured"], len(rows))
        self.assertFalse(summary["quality"]["regression_eligible"])

        self.assertEqual(self.run_main([self.audit, "--record", "chatgpt", "--query", "Q1",
                                        "--run", "1", "--login", "in"]), 0)
        loaded = measure.load_log(log)
        chatgpt = [row for row in loaded if row["engine"] == "chatgpt"]
        self.assertEqual([(row["outcome"], row["reservation"]) for row in chatgpt],
                         [("observed", False)])
        # 다른 엔진 예약과 실제 수집 실패는 그대로 남는다.
        self.assertTrue(any(row.get("reservation") for row in loaded))
        self.assertTrue(any(row["engine"] == "google_aio" and
                            row["outcome"] == "unmeasured" for row in loaded))


if __name__ == "__main__":
    unittest.main()
