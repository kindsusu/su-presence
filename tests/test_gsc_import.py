# -*- coding: utf-8 -*-
"""measure.py gsc — Search Console 실적 CSV 내보내기 가져오기. 네트워크를 쓰지 않는다.

모든 CSV는 합성 픽스처다 (tests/fixtures/gsc/ 또는 테스트 안에서 만든 바이트).
실행: python -m unittest tests.test_gsc_import -q
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import measure  # noqa: E402
import seo_geo  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "gsc")
HOST = "www.example.com"
SCOPE = ["--property", "sc-domain:example.com", "--start", "2026-10-01",
         "--end", "2026-10-28", "--search-type", "web"]


def fixture(name):
    return os.path.join(FIXTURES, name)


class GscFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gsc-test-")
        self.root = os.path.join(self.tmp, "out", HOST)
        os.makedirs(self.root)
        self.audit = os.path.join(self.root, "audit.json")
        measure.write_json(self.audit, {
            "schema": crawl.SCHEMA, "generated_at": "2026-10-29T00:00:00+00:00",
            "target": {"input": HOST, "base": "https://" + HOST, "host": HOST},
            "pages": [{"url": "https://%s/" % HOST}]})
        self.mdir = os.path.join(self.root, "measure")
        self.gdir = os.path.join(self.mdir, "gsc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv_file(self, content, name="export.csv"):
        path = os.path.join(self.tmp, name)
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def run_cli(self, argv, through_seo_geo=False):
        buf = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buf
        try:
            if through_seo_geo:
                code = seo_geo.main(["measure"] + argv)
            else:
                code = measure.main(argv)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return code, buf.getvalue()

    def gsc(self, csv_path, scope=None, extra=(), **kw):
        return self.run_cli(["gsc", self.audit, csv_path] + list(scope or SCOPE) + list(extra), **kw)

    def output(self, name):
        with open(os.path.join(self.gdir, name), "rb") as fh:
            raw = fh.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "결과 JSON에 BOM이 있으면 안 된다")
        return json.loads(raw.decode("utf-8"))

    def assertRejected(self, code, out, *needles):
        self.assertEqual(code, 2, out)
        self.assertIn("GSC 내보내기를 거부했다", out)
        for needle in needles:
            self.assertIn(needle, out)
        self.assertFalse(os.path.exists(self.gdir) and os.listdir(self.gdir),
                         "거부된 파일은 아무것도 쓰지 않는다")


class TestGscAccepts(GscFixture):
    def test_english_query_export_totals(self):
        code, out = self.gsc(fixture("queries_en.csv"))
        self.assertEqual(code, 0, out)
        doc = self.output("2026-10-01_2026-10-28_web_query.json")
        self.assertEqual(doc["schema"], "su-presence/gsc/1")
        self.assertEqual(doc["evidence_level"], "export")
        self.assertEqual(doc["evidence_kind"], "search_performance")
        self.assertIs(doc["complete"], False)
        totals = doc["totals"]
        self.assertEqual((totals["rows"], totals["clicks"], totals["impressions"]), (3, 40, 1500))
        # 총클릭/총노출 = 40/1500. 행 CTR 평균(1.67%)이 아니다.
        self.assertAlmostEqual(totals["ctr"], round(40 / 1500, 6))
        self.assertNotAlmostEqual(totals["ctr"], (0.03 + 0.02 + 0.0) / 3, places=4)
        # 노출 가중: (2.5*1000 + 5*500) / 1500. 노출 0 행(순위 0)은 가중에서 빠진다.
        self.assertAlmostEqual(totals["position"], round(5000 / 1500, 4))
        self.assertEqual(totals["position_weight_impressions"], 1500)
        self.assertEqual(totals["zero_impression_rows"], 1)
        self.assertEqual(doc["source"]["header_language"], "en")
        self.assertFalse(doc["source"]["bom"])
        self.assertEqual(doc["source"]["file_name"], "queries_en.csv")
        self.assertEqual(doc["rows"][0], {"key": "example widget price", "clicks": 30,
                                          "impressions": 1000, "ctr_reported": 0.03, "position": 2.5})
        codes = [item["code"] for item in doc["limitations"]]
        self.assertIn("export_truncated", codes)
        self.assertIn("anonymized_queries_omitted", codes)
        self.assertIn("not_ai_citation", codes)

    def test_scope_is_stored_as_given(self):
        code, out = self.gsc(fixture("queries_en.csv"),
                             extra=["--dimension", "query", "--filters", "country=kor; device=mobile"])
        self.assertEqual(code, 0, out)
        scope = self.output("2026-10-01_2026-10-28_web_query.json")["scope"]
        self.assertEqual(scope, {"property": "sc-domain:example.com", "property_type": "domain",
                                 "start": "2026-10-01", "end": "2026-10-28", "days": 28,
                                 "search_type": "web", "dimension": "query",
                                 "filters": "country=kor; device=mobile", "declared_by": "operator"})

    def test_korean_headers_with_bom_and_fractional_ctr(self):
        code, out = self.gsc(fixture("pages_ko_bom.csv"))
        self.assertEqual(code, 0, out)
        doc = self.output("2026-10-01_2026-10-28_web_page.json")
        self.assertTrue(doc["source"]["bom"])
        self.assertEqual(doc["source"]["header_language"], "ko")
        self.assertEqual(doc["source"]["columns"]["page"], "인기 페이지")
        self.assertEqual(doc["totals"]["clicks"], 20)
        self.assertEqual(doc["totals"]["impressions"], 500)
        self.assertAlmostEqual(doc["totals"]["ctr"], 0.04)
        self.assertAlmostEqual(doc["totals"]["position"], round((1.8 * 400 + 4.2 * 100) / 500, 4))
        self.assertIn("page_aggregation", [i["code"] for i in doc["limitations"]])
        self.assertNotIn("anonymized_queries_omitted", [i["code"] for i in doc["limitations"]])

    def test_korean_date_export_with_grouped_numbers_and_percent_ctr(self):
        scope = ["--property", "https://www.example.com/", "--start", "2026-10-01",
                 "--end", "2026-10-03", "--search-type", "web"]
        code, out = self.gsc(fixture("dates_ko.csv"), scope=scope, extra=["--dimension", "date"])
        self.assertEqual(code, 0, out)
        doc = self.output("2026-10-01_2026-10-03_web_date.json")
        self.assertEqual(doc["scope"]["property_type"], "url_prefix")
        self.assertEqual(doc["totals"]["impressions"], 3000)
        self.assertEqual(doc["rows"][0]["ctr_reported"], 0.005)
        self.assertAlmostEqual(doc["totals"]["position"], round((7 * 1000 + 4 * 2000) / 3000, 4))
        self.assertEqual(doc["date_coverage"], {"expected_days": 3, "present_days": 2,
                                                "missing_days": 1, "missing": ["2026-10-03"]})
        self.assertIn("날짜 누락 1일", out)

    def test_query_with_unicode_line_breaks_stays_one_row(self):
        """검색어 안의 U+2028·U+0085·폼피드·따옴표 속 줄바꿈은 행을 나누지 않는다. 빈 줄은 센다."""
        path = self.csv_file("Top queries,Clicks,Impressions,CTR,Position\n"
                             "alpha beta,1,10,10%,2\n"
                             "gamma\x85delta\x0cepsilon,2,20,10%,3\n"
                             "\n"
                             '"quoted\nline",3,30,10%,4\n')
        code, out = self.gsc(path)
        self.assertEqual(code, 0, out)
        doc = self.output("2026-10-01_2026-10-28_web_query.json")
        self.assertEqual([r["key"] for r in doc["rows"]],
                         ["alpha beta", "gamma\x85delta\x0cepsilon", "quoted\nline"])
        self.assertEqual(doc["totals"]["rows"], 3)
        self.assertEqual(doc["quality"]["blank_lines"], 1)

    def test_crlf_and_alias_headers(self):
        path = self.csv_file("Query,Clicks,Impressions,CTR,Average position\r\n"
                             "alpha,1,10,10%,3\r\nbeta,0,5,0%,9\r\n")
        code, out = self.gsc(path)
        self.assertEqual(code, 0, out)
        doc = self.output("2026-10-01_2026-10-28_web_query.json")
        self.assertEqual(doc["totals"]["rows"], 2)
        self.assertAlmostEqual(doc["totals"]["position"], 5.0)

    def test_country_and_device_dimensions(self):
        for header, value, dim in (("Country", "South Korea", "country"), ("기기", "모바일", "device")):
            path = self.csv_file("%s,Clicks,Impressions,CTR,Position\n%s,2,20,10%%,1.5\n" % (header, value),
                                 name=dim + ".csv")
            code, out = self.gsc(path)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.output("2026-10-01_2026-10-28_web_%s.json" % dim)["scope"]["dimension"], dim)

    def test_korean_summary_is_printed(self):
        code, out = self.gsc(fixture("queries_en.csv"))
        self.assertEqual(code, 0, out)
        self.assertIn("검색 성과 근거(export), AI 인용 관측 아님", out)
        self.assertIn("클릭 40 · 노출 1,500 · CTR 2.67% (총클릭/총노출)", out)
        self.assertIn("평균 게재순위 3.3 (노출 가중)", out)
        self.assertIn("익명 검색어", out)
        self.assertIn("완전한 값이 아니다", out)

    def test_citation_log_and_summary_are_untouched(self):
        code, out = self.gsc(fixture("queries_en.csv"))
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(os.listdir(self.mdir)), ["gsc"])
        self.assertFalse(os.path.exists(os.path.join(self.mdir, "log.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.mdir, "summary.json")))
        self.assertEqual(measure.load_log(os.path.join(self.mdir, "log.jsonl")), [])

    def test_reimport_same_file_is_idempotent_and_different_file_is_preserved(self):
        self.assertEqual(self.gsc(fixture("queries_en.csv"))[0], 0)
        self.assertEqual(self.gsc(fixture("queries_en.csv"))[0], 0)
        self.assertEqual(sorted(os.listdir(self.gdir)), ["2026-10-01_2026-10-28_web_query.json"])
        other = self.csv_file("Top queries,Clicks,Impressions,CTR,Position\nalpha,1,2,50%,1\n")
        code, out = self.gsc(other)
        self.assertEqual(code, 0, out)
        self.assertIn("이전 파일 보존", out)
        kept = os.listdir(os.path.join(self.gdir, "observations"))
        self.assertEqual(len(kept), 1)
        with open(os.path.join(self.gdir, "observations", kept[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["totals"]["clicks"], 40)
        self.assertEqual(self.output("2026-10-01_2026-10-28_web_query.json")["totals"]["clicks"], 1)

    def test_cli_wiring_through_seo_geo(self):
        code, out = self.gsc(fixture("queries_en.csv"), through_seo_geo=True)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.gdir, "2026-10-01_2026-10-28_web_query.json")))


class TestGscRejects(GscFixture):
    HEADER = "Top queries,Clicks,Impressions,CTR,Position\n"

    def test_missing_scope_args(self):
        code, out = self.run_cli(["gsc", self.audit, fixture("queries_en.csv")])
        self.assertRejected(code, out, "--property", "--start", "--end", "--search-type")
        code, out = self.run_cli(["gsc", self.audit, fixture("queries_en.csv"),
                                  "--property", "sc-domain:example.com", "--start", "2026-10-01"])
        self.assertRejected(code, out, "--end", "--search-type")

    def test_bad_scope_values(self):
        for scope, needle in (
                (["--property", "example.com"] + SCOPE[2:], "--property"),
                (SCOPE[:2] + ["--start", "2026-10-30", "--end", "2026-10-01", "--search-type", "web"], "늦다"),
                (SCOPE[:2] + ["--start", "10/01/2026", "--end", "2026-10-28", "--search-type", "web"], "YYYY-MM-DD"),
                (SCOPE[:6] + ["--search-type", "discover"], "--search-type"),
                (["--property", "sc-domain:other.org"] + SCOPE[2:], "다른 사이트")):
            code, out = self.gsc(fixture("queries_en.csv"), scope=scope)
            self.assertRejected(code, out, needle)

    def test_unknown_and_missing_columns(self):
        code, out = self.gsc(self.csv_file("Top queries,Clicks,Impressions,CTR,Position,Extra\n"
                                           "a,1,2,50%,1,x\n"))
        self.assertRejected(code, out, "알 수 없는 열", "'Extra'")
        code, out = self.gsc(self.csv_file("Top queries,Clicks,Impressions,CTR\na,1,2,50%\n"))
        self.assertRejected(code, out, "필수 지표 열이 없다: position")
        code, out = self.gsc(self.csv_file("Clicks,Impressions,CTR,Position\n1,2,50%,1\n"))
        self.assertRejected(code, out, "차원 열")
        code, out = self.gsc(self.csv_file("Top queries,Clicks (Last 28 days),Impressions,CTR,Position\n"))
        self.assertRejected(code, out, "알 수 없는 열")

    def test_dimension_flag_must_match_header(self):
        code, out = self.gsc(fixture("queries_en.csv"), extra=["--dimension", "page"])
        self.assertRejected(code, out, "--dimension page", "query")

    def test_duplicate_dimension_key(self):
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha,1,10,10%,2\nbeta,1,10,10%,2\nalpha,2,20,10%,3\n"))
        self.assertRejected(code, out, "4행", "중복 키 'alpha'", "2행과 같음")

    def test_clicks_greater_than_impressions(self):
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha,11,10,10%,2\n"))
        self.assertRejected(code, out, "2행", "클릭수(11)가 노출수(10)보다 크다")

    def test_non_numeric_and_negative_values(self):
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha,many,10,10%,2\n"
                                           "beta,1,-10,10%,2\ngamma,1,10,10%,-2\ndelta,1,10,abc,2\n"))
        self.assertRejected(code, out, "문제 행 4개", "클릭수 정수가 아니다", "노출수 음수",
                            "게재순위 음수", "CTR 형식이 아니다")

    def test_ctr_over_one_without_percent_is_rejected(self):
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha,1,10,12.3,2\n"))
        self.assertRejected(code, out, "100%를 넘는다")

    def test_date_outside_range(self):
        path = self.csv_file("Date,Clicks,Impressions,CTR,Position\n2026-10-01,1,10,10%,2\n"
                             "2026-09-30,1,10,10%,2\n2026-10-29,1,10,10%,2\n")
        code, out = self.gsc(path)
        self.assertRejected(code, out, "3행", "4행", "2026-09-30", "밖이다")

    def test_page_outside_property(self):
        path = self.csv_file("Top pages,Clicks,Impressions,CTR,Position\nhttps://other.org/,1,10,10%,2\n")
        code, out = self.gsc(path)
        self.assertRejected(code, out, "속성 범위 밖")

    def test_error_lists_only_first_rows(self):
        body = "".join("q%d,5,1,10%%,2\n" % i for i in range(25))
        code, out = self.gsc(self.csv_file(self.HEADER + body))
        self.assertRejected(code, out, "문제 행 25개", "외 15건")
        self.assertIn("2행", out)
        self.assertNotIn("26행", out)

    def test_non_utf8_and_empty_files(self):
        code, out = self.gsc(self.csv_file(("인기 검색어,클릭수,노출수,CTR,게재순위\n가,1,2,50%,1\n").encode("cp949")))
        self.assertRejected(code, out, "UTF-8")
        code, out = self.gsc(self.csv_file(b""))
        self.assertRejected(code, out, "빈 파일")
        code, out = self.gsc(self.csv_file(self.HEADER))
        self.assertRejected(code, out, "데이터 행이 없다")

    def test_ragged_row(self):
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha,1,10,10%\n"))
        self.assertRejected(code, out, "열 개수 4")

    def test_unicode_line_separator_in_query_does_not_shift_row_numbers(self):
        """U+2028은 CSV 행 구분이 아니다 — 2행이 쪼개져 오류 행 번호가 밀리면 안 된다."""
        code, out = self.gsc(self.csv_file(self.HEADER + "alpha beta,1,10,10%,2\n"
                                           "gamma,11,10,10%,2\n"))
        self.assertRejected(code, out, "문제 행 1개", "3행", "클릭수(11)가 노출수(10)보다 크다")
        self.assertNotIn("열 개수", out)


class TestGscMath(unittest.TestCase):
    def test_weighted_position_excludes_zero_impression_rows(self):
        rows = [{"key": "a", "clicks": 0, "impressions": 0, "ctr_reported": None, "position": None},
                {"key": "b", "clicks": 1, "impressions": 100, "ctr_reported": 0.01, "position": 2.0},
                {"key": "c", "clicks": 9, "impressions": 300, "ctr_reported": 0.03, "position": 10.0}]
        totals = measure.gsc_totals(rows)
        self.assertEqual(totals["position"], 8.0)   # (2*100 + 10*300) / 400
        self.assertEqual(totals["ctr"], 0.025)      # 10 / 400, not mean(0.01, 0.03)
        self.assertEqual(totals["zero_impression_rows"], 1)

    def test_all_zero_impressions_has_no_ctr_or_position(self):
        totals = measure.gsc_totals([{"key": "a", "clicks": 0, "impressions": 0,
                                      "ctr_reported": None, "position": None}])
        self.assertIsNone(totals["ctr"])
        self.assertIsNone(totals["position"])

    def test_ctr_parsing(self):
        self.assertEqual(measure._gsc_ctr("12.5%"), (0.125, None))
        self.assertEqual(measure._gsc_ctr("0.125"), (0.125, None))
        self.assertEqual(measure._gsc_ctr(""), (None, None))
        self.assertIsNotNone(measure._gsc_ctr("12,5 %")[1])


if __name__ == "__main__":
    unittest.main()
