# -*- coding: utf-8 -*-
"""measure.py 회귀 테스트 — 네트워크를 쓰지 않는다 (전송 함수를 가짜로 주입).

실행: python -m unittest discover tests
"""

import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import measure  # noqa: E402


# ─────────────────────────────────────────────────────────── 픽스처

HOST = "example.com"
BASE = "https://example.com"
SECRET = "sk-test-DO-NOT-LEAK-0123456789"

QUERIES = [
    {"id": "Q01", "text": "예시 브랜드 요금", "type": "brand", "note": ""},
    {"id": "Q02", "text": "예시 브랜드 어떤 회사", "type": "brand", "note": ""},
    {"id": "Q03", "text": "장기 이용 요금 비교", "type": "nonbrand", "note": ""},
]


def audit_fixture():
    return {
        "schema": "su-multi-geo/audit/1",
        "generated_at": "2026-09-01T00:00:00+00:00",
        "target": {"input": HOST, "base": BASE, "host": HOST},
        "pages": [{"url": BASE + "/"}, {"url": BASE + "/pricing"},
                  {"url": BASE + "/pricing/long-term"}, {"url": BASE + "/faq"}],
    }


def queries_doc(queries=None):
    return {"schema": measure.SCHEMA_QUERIES, "queries": queries or QUERIES}


class Fixture(unittest.TestCase):
    """out/<host>/audit.json + measure/queries.json 이 있는 임시 트리."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="measure-test-")
        self.root = os.path.join(self.tmp, "out", HOST)
        os.makedirs(self.root)
        self.audit = os.path.join(self.root, "audit.json")
        measure.write_json(self.audit, audit_fixture())
        self.mdir = os.path.join(self.root, "measure")
        os.makedirs(self.mdir)
        measure.write_json(os.path.join(self.mdir, "queries.json"), queries_doc())
        self.log = os.path.join(self.mdir, "log.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, argv):
        buf = io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buf
        try:
            code = measure.main(argv)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return code, buf.getvalue()

    def read(self, name):
        with open(os.path.join(self.mdir, name), encoding="utf-8") as fh:
            return fh.read()


# ─────────────────────────────────────────────────────────── 작은 도구들

class TestHelpers(unittest.TestCase):

    def test_yn_variants(self):
        for token in ("Y", "y", "yes", "1", "O", "예", True):
            self.assertIs(measure.yn(token), True, token)
        for token in ("N", "no", "0", "x", "아니오", False):
            self.assertIs(measure.yn(token), False, token)
        for token in ("", "   ", None, "maybe"):
            self.assertIsNone(measure.yn(token))

    def test_norm_url(self):
        self.assertEqual(measure.norm_url("EXAMPLE.com/A"), "https://example.com/A")
        self.assertEqual(measure.norm_url("https://example.com/a#frag"),
                         "https://example.com/a")
        self.assertEqual(measure.norm_url("https://example.com/a?b=1"),
                         "https://example.com/a?b=1")
        self.assertEqual(measure.norm_url("https://example.com"), "https://example.com/")
        self.assertIsNone(measure.norm_url("—"))
        self.assertIsNone(measure.norm_url(""))
        self.assertIsNone(measure.norm_url("ftp://example.com/x"))

    def test_is_ours(self):
        self.assertTrue(measure.is_ours("https://example.com/a", HOST))
        self.assertTrue(measure.is_ours("https://www.example.com/a", HOST))
        self.assertTrue(measure.is_ours("https://blog.example.com/a", HOST))
        self.assertFalse(measure.is_ours("https://competitor.com/a", HOST))
        self.assertFalse(measure.is_ours("https://notexample.com/a", HOST))

    def test_web_search_variant_follows_the_model(self):
        """2026-02 변형은 현세대 모델 전용이다. 옛 모델에 보내면 400 이 난다."""
        self.assertEqual(measure.anthropic_web_search("claude-sonnet-5")["type"],
                         measure.WEB_SEARCH_MODERN)
        self.assertEqual(measure.anthropic_web_search("claude-3-5-sonnet")["type"],
                         measure.WEB_SEARCH_BASIC)

    def test_default_models_are_priced(self):
        """기본 모델이 가격표에 없으면 비용 추정이 조용히 사라진다."""
        for model in (measure.OPENAI_MODEL_DEFAULT, measure.ANTHROPIC_MODEL_DEFAULT):
            self.assertIn(model, measure.PRICES, "%s 가 PRICES 에 없다" % model)

    def test_cost_estimate_is_a_range_and_scales(self):
        lo, hi = measure.estimate_cost("claude", "claude-sonnet-5", 40)
        self.assertLess(lo, hi)
        lo2, hi2 = measure.estimate_cost("claude", "claude-sonnet-5", 80)
        self.assertAlmostEqual(lo2, lo * 2, places=6)
        self.assertAlmostEqual(hi2, hi * 2, places=6)

    def test_unknown_model_estimates_nothing(self):
        """모르는 가격을 지어내면 추정이 없는 것보다 나쁘다."""
        self.assertIsNone(measure.estimate_cost("claude", "claude-sonnet-4-5", 40))
        self.assertIsNone(measure.estimate_cost("claude", "claude-sonnet-5", 0))

    def test_pick_engines(self):
        self.assertEqual(measure.pick_engines(None), (measure.DEFAULT_ENGINES, []))
        self.assertEqual(measure.pick_engines("chatgpt,gemini"), (["chatgpt", "gemini"], []))
        engines, bad = measure.pick_engines("chatgpt,bing,chatgpt")
        self.assertEqual(engines, ["chatgpt"])
        self.assertEqual(bad, ["bing"])


# ─────────────────────────────────────────────────────────── init

class TestInit(Fixture):

    def test_init_does_not_overwrite(self):
        code, out = self.run_cli(["init", self.audit])
        self.assertEqual(code, 0)
        self.assertIn("이미 있다", out)
        self.assertEqual(measure.load_queries(self.mdir)[0]["id"], "Q01")

    def test_init_copies_template_and_prints_hints(self):
        os.remove(os.path.join(self.mdir, "queries.json"))
        code, out = self.run_cli(["init", self.audit])
        self.assertEqual(code, 0)
        queries = measure.load_queries(self.mdir)
        self.assertEqual(len(queries), 8)
        self.assertEqual(sum(1 for q in queries if q["type"] == "brand"), 3)
        self.assertEqual(sum(1 for q in queries if q["type"] == "nonbrand"), 5)
        # 질의 문장은 비어 있어야 한다 — 도구가 지어내지 않는다
        self.assertEqual(len(measure.queries_todo(queries)), 8)
        self.assertIn("/pricing", out)         # audit.json 섹션 힌트

    def test_rejects_non_audit_json(self):
        measure.write_json(self.audit, {"schema": "something/else"})
        code, out = self.run_cli(["init", self.audit])
        self.assertEqual(code, 2)


# ─────────────────────────────────────────────────────────── form

class TestForm(Fixture):

    def test_row_count_is_queries_times_engines_times_runs(self):
        code, out = self.run_cli(["form", self.audit, "--engines", "chatgpt,gemini",
                                  "--runs", "5", "--date", "2026-09-15"])
        self.assertEqual(code, 0)
        raw = self.read("form-2026-09-15.csv")
        rows = list(csv.DictReader(io.StringIO(raw.lstrip("﻿"))))
        self.assertEqual(len(rows), 3 * 2 * 5)
        self.assertEqual(len({(r["query_id"], r["engine"], r["run_no"]) for r in rows}), 30)

    def test_csv_has_bom_and_header_and_empty_input_columns(self):
        self.run_cli(["form", self.audit, "--date", "2026-09-15"])
        path = os.path.join(self.mdir, "form-2026-09-15.csv")
        with open(path, "rb") as fh:
            head = fh.read(3)
        self.assertEqual(head, b"\xef\xbb\xbf")          # 엑셀 한글 깨짐 방지
        rows = list(csv.DictReader(io.StringIO(self.read("form-2026-09-15.csv").lstrip("﻿"))))
        self.assertEqual(list(rows[0]), measure.CSV_FIELDS)
        for column in ("cited", "cited_urls", "brand_mentioned", "competitor_domains", "note"):
            self.assertEqual(rows[0][column], "", column)
        self.assertEqual(rows[0]["date"], "2026-09-15")
        self.assertEqual(rows[0]["query_text"], "예시 브랜드 요금")

    def test_form_defaults_to_engines_a_human_must_drive(self):
        """무인 수집되는 엔진을 폼 기본값에 넣으면 이미 자동화된 일을 사람에게 시킨다."""
        self.run_cli(["form", self.audit, "--date", "2026-09-15"])
        rows = list(csv.DictReader(io.StringIO(self.read("form-2026-09-15.csv").lstrip("﻿"))))
        engines = sorted({r["engine"] for r in rows})
        self.assertEqual(engines, sorted(measure.DEFAULT_ENGINES))
        overlap = sorted(set(engines) & set(measure.COLLECTED_ENGINES))
        self.assertEqual(overlap, [],
                         "collect.py 가 무인으로 재는 엔진이 폼 기본값에 있다: %s" % overlap)

    def test_explicitly_named_collected_engine_is_kept(self):
        """사람이 일부러 적은 엔진을 말없이 버리지 않는다 — 안내만 덧붙인다."""
        self.run_cli(["form", self.audit, "--engines", "google_aio", "--date", "2026-09-15"])
        rows = list(csv.DictReader(io.StringIO(self.read("form-2026-09-15.csv").lstrip("﻿"))))
        self.assertEqual(sorted({r["engine"] for r in rows}), ["google_aio"])

    def test_html_form_is_offline_and_has_rules(self):
        self.run_cli(["form", self.audit, "--engines", "chatgpt", "--runs", "5",
                      "--date", "2026-09-15"])
        html = self.read("form-2026-09-15.html")
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        for external in ("<script src=", "<link rel=\"stylesheet\"", "@import"):
            self.assertNotIn(external, html)
        self.assertEqual(html.count("<li><label><input type=\"checkbox\">"), len(measure.RULES))
        self.assertIn("localStorage", html)
        self.assertIn("word-break:keep-all", html)
        self.assertIn("overflow-x:auto", html)
        self.assertIn("prefers-color-scheme", html)
        self.assertIn('data-theme="dark"', html)
        self.assertIn("CSV로 내보내기", html)
        data = json.loads(html.split("var DATA = ", 1)[1].split(";\nvar FIELDS", 1)[0]
                          .replace("<\\/", "</"))
        self.assertEqual(data["runs"], 5)
        self.assertEqual(len(data["queries"]), 3)
        self.assertEqual(data["engines"], [["chatgpt", "ChatGPT"]])

    def test_bad_engine_and_todo_are_warned(self):
        measure.write_json(os.path.join(self.mdir, "queries.json"), queries_doc(
            [{"id": "Q01", "text": "<<TODO: 브랜드명>> 요금", "type": "brand"}]))
        code, out = self.run_cli(["form", self.audit, "--engines", "chatgpt,bing",
                                  "--runs", "2", "--date", "2026-09-15"])
        self.assertEqual(code, 0)
        self.assertIn("bing", out)
        self.assertIn("빈 칸", out)
        self.assertIn("표본이 아니다", out)      # runs < 5

    def test_form_without_queries_fails(self):
        os.remove(os.path.join(self.mdir, "queries.json"))
        code, out = self.run_cli(["form", self.audit])
        self.assertEqual(code, 2)


# ─────────────────────────────────────────────────────────── import

def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=measure.CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            out = {k: "" for k in measure.CSV_FIELDS}
            out.update(row)
            writer.writerow(out)


def row(**kw):
    base = {"date": "2026-09-15", "query_id": "Q01", "type": "brand",
            "engine": "chatgpt", "run_no": 1, "cited": "Y"}
    base.update(kw)
    return base


class TestImport(Fixture):

    def csv_path(self, rows, name="filled.csv"):
        path = os.path.join(self.tmp, name)
        write_csv(path, rows)
        return path

    def test_accepts_good_rows_and_normalizes_urls(self):
        path = self.csv_path([row(cited_urls="EXAMPLE.com/pricing#a  competitor.com/x",
                                  brand_mentioned="Y", note="검색모드")])
        code, out = self.run_cli(["import", self.audit, path])
        self.assertEqual(code, 0)
        rows = measure.load_log(self.log)
        self.assertEqual(len(rows), 1)
        got = rows[0]
        self.assertEqual(got["schema"], measure.SCHEMA_ROW)
        self.assertEqual(got["cited_urls"],
                         ["https://example.com/pricing", "https://competitor.com/x"])
        self.assertEqual(got["competitor_domains"], ["competitor.com"])
        self.assertEqual(got["mode"], "manual")
        self.assertIs(got["signed_out"], True)
        self.assertIs(got["cited"], True)

    def test_rejects_bad_engine_bad_yn_bad_date_unknown_query(self):
        path = self.csv_path([
            row(engine="bing"),
            row(run_no=2, cited="maybe"),
            row(run_no=3, date="2026/09/15"),
            row(run_no=4, query_id="Q99"),
            row(run_no=5),                      # 정상 행 하나
        ])
        code, out = self.run_cli(["import", self.audit, path])
        self.assertEqual(code, 0)
        self.assertEqual(len(measure.load_log(self.log)), 1)
        self.assertIn("모르는 engine", out)
        self.assertIn("Y/N", out)
        self.assertIn("YYYY-MM-DD", out)
        self.assertIn("query_id", out)
        self.assertIn("건너뛴 행: 4", out)

    def test_blank_run_no_and_blank_cited_are_skipped(self):
        path = self.csv_path([row(run_no=""), row(run_no=2, cited="")])
        code, out = self.run_cli(["import", self.audit, path])
        self.assertEqual(measure.load_log(self.log), [])
        self.assertIn("건너뛴 행: 2", out)

    def test_duplicate_key_keeps_the_last_one(self):
        path = self.csv_path([row(cited="N"), row(cited="Y", cited_urls="example.com/a")])
        self.run_cli(["import", self.audit, path])
        rows = measure.load_log(self.log)
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["cited"], True)

    def test_duplicate_across_imports_keeps_the_last_one(self):
        first = self.csv_path([row(cited="N")], "a.csv")
        second = self.csv_path([row(cited="Y", cited_urls="example.com/a")], "b.csv")
        self.run_cli(["import", self.audit, first])
        self.run_cli(["import", self.audit, second])
        with open(self.log, encoding="utf-8") as fh:
            self.assertEqual(len([l for l in fh if l.strip()]), 2)   # append-only
        rows = measure.load_log(self.log)                            # 읽을 때 최신만
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["cited"], True)

    def test_blank_brand_mentioned_follows_cited(self):
        path = self.csv_path([row(cited="Y", cited_urls="example.com/a"),
                              row(run_no=2, cited="N")])
        self.run_cli(["import", self.audit, path])
        rows = sorted(measure.load_log(self.log), key=lambda r: r["run_no"])
        self.assertIs(rows[0]["brand_mentioned"], True)
        self.assertIs(rows[1]["brand_mentioned"], False)

    def test_cited_without_url_is_noted(self):
        path = self.csv_path([row(cited="Y", cited_urls="")])
        self.run_cli(["import", self.audit, path])
        self.assertIn("인용 URL 미기록", measure.load_log(self.log)[0]["note"])

    def test_non_url_token_is_kept_in_note(self):
        path = self.csv_path([row(cited="Y", cited_urls="example.com/a 없음")])
        self.run_cli(["import", self.audit, path])
        got = measure.load_log(self.log)[0]
        self.assertEqual(got["cited_urls"], ["https://example.com/a"])
        self.assertIn("URL 아님", got["note"])


# ─────────────────────────────────────────────────────────── report

def log_row(date, qid, engine, run_no, cited, urls=(), mentioned=None, comps=(), mode="manual"):
    return measure.make_row(date, qid, engine, run_no, mode,
                            True if mode == "manual" else None,
                            cited, list(urls),
                            cited if mentioned is None else mentioned, list(comps))


class TestReport(Fixture):

    def seed(self, rows):
        measure.append_rows(self.log, rows)

    def test_engine_type_rates_are_run_sums(self):
        rows = []
        # 기준선: ChatGPT × Q01(브랜드) 5회 중 2회 인용
        for n in range(1, 6):
            rows.append(log_row("2026-09-01", "Q01", "chatgpt", n, n <= 2,
                                ["https://example.com/pricing"] if n <= 2 else []))
        # ChatGPT × Q03(비브랜드) 5회 중 0회
        for n in range(1, 6):
            rows.append(log_row("2026-09-01", "Q03", "chatgpt", n, False, [],
                                comps=["competitor.com"]))
        self.seed(rows)
        code, out = self.run_cli(["report", self.audit])
        self.assertEqual(code, 0)
        summary = json.loads(self.read("summary.json"))
        self.assertEqual(summary["schema"], measure.SCHEMA_SUMMARY)
        chatgpt = summary["engines"][0]
        self.assertEqual(chatgpt["engine"], "chatgpt")
        self.assertEqual((chatgpt["brand"]["cited"], chatgpt["brand"]["runs"]), (2, 5))
        self.assertEqual(chatgpt["brand"]["rate"], 0.4)
        self.assertEqual((chatgpt["nonbrand"]["cited"], chatgpt["nonbrand"]["runs"]), (0, 5))
        self.assertEqual(chatgpt["nonbrand"]["rate"], 0.0)
        self.assertEqual(summary["rows"], 10)
        self.assertIn("2/5", self.read("MEASURE.md"))

    def test_url_frequency_splits_ours_from_competitors(self):
        rows = [
            log_row("2026-09-01", "Q01", "chatgpt", 1, True,
                    ["https://example.com/pricing", "https://competitor.com/x"],
                    comps=["competitor.com"]),
            log_row("2026-09-01", "Q01", "chatgpt", 2, True,
                    ["https://example.com/pricing"]),
            log_row("2026-09-01", "Q03", "chatgpt", 1, False,
                    ["https://rival.co.kr/y"], comps=["rival.co.kr", "competitor.com"]),
        ]
        self.seed(rows)
        self.run_cli(["report", self.audit])
        summary = json.loads(self.read("summary.json"))
        self.assertEqual(summary["urls"]["ours"],
                         [{"url": "https://example.com/pricing", "count": 2}])
        comps = {c["domain"]: c["count"] for c in summary["urls"]["competitors"]}
        self.assertEqual(comps, {"competitor.com": 2, "rival.co.kr": 1})
        by_q = {q["id"]: q for q in summary["by_query"]}
        self.assertEqual(by_q["Q01"]["urls"][0]["url"], "https://example.com/pricing")
        self.assertEqual(by_q["Q02"]["runs"], 0)

    def test_trend_and_next_measure_date(self):
        rows = []
        for n in (1, 2):
            rows.append(log_row("2026-09-01", "Q03", "chatgpt", n, False))
            rows.append(log_row("2026-09-15", "Q03", "chatgpt", n, n == 1,
                                ["https://example.com/long-term"]))
        self.seed(rows)
        self.run_cli(["report", self.audit])
        summary = json.loads(self.read("summary.json"))
        self.assertEqual([t["date"] for t in summary["trend"]], ["2026-09-01", "2026-09-15"])
        self.assertEqual(summary["trend"][0]["nonbrand"], {"queries": 1, "queries_cited": 0})
        self.assertEqual(summary["trend"][1]["nonbrand"], {"queries": 1, "queries_cited": 1})
        self.assertEqual(summary["window"]["baseline"], "2026-09-01")
        self.assertEqual(summary["next_measure"], "2026-09-29")   # 마지막 +14일
        self.assertIn("2026-09-29", self.read("MEASURE.md"))
        self.assertTrue(summary["headline"][0].startswith("[기준선] 2026-09-01"))
        self.assertIn("비브랜드 +1", " ".join(summary["headline"]))

    def test_since_filters_rows(self):
        self.seed([log_row("2026-09-01", "Q01", "chatgpt", 1, True, ["https://example.com/a"]),
                   log_row("2026-09-15", "Q01", "chatgpt", 1, False)])
        self.run_cli(["report", self.audit, "--since", "2026-09-10"])
        summary = json.loads(self.read("summary.json"))
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["window"]["dates"], ["2026-09-15"])
        self.assertEqual(summary["urls"]["ours"], [])

    def test_empty_log_still_reports(self):
        code, out = self.run_cli(["report", self.audit])
        self.assertEqual(code, 0)
        summary = json.loads(self.read("summary.json"))
        self.assertEqual(summary["rows"], 0)
        self.assertIsNone(summary["next_measure"])
        self.assertIn("측정 없음", summary["headline"][0])

    def test_bad_since_is_rejected(self):
        code, out = self.run_cli(["report", self.audit, "--since", "9/10"])
        self.assertEqual(code, 2)

    def test_rows_for_unknown_query_are_ignored(self):
        self.seed([log_row("2026-09-01", "Q99", "chatgpt", 1, True, ["https://example.com/a"])])
        self.run_cli(["report", self.audit])
        self.assertEqual(json.loads(self.read("summary.json"))["rows"], 0)


# ─────────────────────────────────────────────────────────── auto (가짜 전송)

OPENAI_RESPONSE = {
    "output": [{"type": "message", "content": [{
        "type": "output_text",
        "text": "예시 주식회사의 장기 요금은 공개 페이지에 있다.",
        "annotations": [
            {"type": "url_citation", "url": "https://example.com/pricing?ref=chat"},
            {"type": "url_citation", "url": "https://competitor.com/compare"},
        ]}]}]
}

ANTHROPIC_RESPONSE = {
    "content": [
        {"type": "server_tool_use", "name": "web_search"},
        {"type": "web_search_tool_result", "content": [
            {"type": "web_search_result", "url": "https://never-cited.example.net/x"}]},
        {"type": "text", "text": "example.com 에 요금표가 있다.",
         "citations": [{"type": "web_search_result_location",
                        "url": "https://example.com/faq"}]},
    ]
}


class Sender:
    """가짜 HTTP 전송 — 호출을 기록만 하고 네트워크를 쓰지 않는다."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, url, payload, headers, timeout=None):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if measure.OPENAI_URL in url:
            return self.responses.get("openai", OPENAI_RESPONSE)
        return self.responses.get("anthropic", ANTHROPIC_RESPONSE)


class TestAuto(Fixture):

    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.pop(k, None) for k in
                      ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_MODEL", "ANTHROPIC_MODEL")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_without_keys_it_exits_cleanly_and_points_at_the_form(self):
        code, out = self.run_cli(["auto", self.audit, "--yes"])
        self.assertEqual(code, 0)                      # 에러가 아니다
        self.assertIn("수동 모드", out)
        self.assertIn("measure.py form", out)
        self.assertFalse(os.path.exists(self.log))

    def test_non_automatable_engines_are_sent_to_the_form(self):
        code, out = self.run_cli(["auto", self.audit, "--engines", "gemini,perplexity", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("Gemini", out)
        self.assertIn("Perplexity", out)
        self.assertIn("--engines gemini,perplexity", out)

    def test_openai_response_becomes_a_log_row(self):
        send = Sender()
        rows = measure.run_auto(QUERIES[:1], ["chatgpt"], 2, HOST, {"chatgpt": SECRET},
                                send=send, models={"chatgpt": "test-model"}, delay=0,
                                date_str="2026-09-15", site_name="예시 주식회사")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(send.calls), 2)
        self.assertEqual(send.calls[0]["url"], measure.OPENAI_URL)
        self.assertEqual(send.calls[0]["payload"]["tools"], [{"type": "web_search"}])
        self.assertEqual(send.calls[0]["payload"]["model"], "test-model")
        got = rows[0]
        self.assertEqual(got["mode"], "api")
        self.assertIsNone(got["signed_out"])
        self.assertIs(got["cited"], True)              # 우리 host가 인용 URL에 있다
        self.assertEqual(got["cited_urls"], ["https://example.com/pricing?ref=chat",
                                             "https://competitor.com/compare"])
        self.assertEqual(got["competitor_domains"], ["competitor.com"])
        self.assertIs(got["brand_mentioned"], True)    # 응답 텍스트에 site name

    def test_anthropic_counts_only_cited_urls_not_search_results(self):
        send = Sender()
        rows = measure.run_auto(QUERIES[:1], ["claude"], 1, HOST, {"claude": SECRET},
                                send=send, delay=0, date_str="2026-09-15")
        got = rows[0]
        self.assertEqual(send.calls[0]["url"], measure.ANTHROPIC_URL)
        # 도구 변형은 모델을 따라간다 — 기본 모델이 현세대면 현세대 변형이어야 한다
        self.assertEqual(send.calls[0]["payload"]["tools"][0]["type"],
                         measure.anthropic_web_search(measure.ANTHROPIC_MODEL_DEFAULT)["type"])
        self.assertEqual(send.calls[0]["headers"]["anthropic-version"],
                         measure.ANTHROPIC_VERSION)
        self.assertEqual(got["cited_urls"], ["https://example.com/faq"])   # 검색결과는 제외
        self.assertIs(got["cited"], True)
        self.assertIs(got["brand_mentioned"], True)    # 텍스트에 host

    def test_failed_run_is_recorded_with_a_reason(self):
        send = Sender({"openai": {"_error": "http_429"}})
        rows = measure.run_auto(QUERIES[:1], ["chatgpt"], 1, HOST, {"chatgpt": SECRET},
                                send=send, delay=0, date_str="2026-09-15")
        self.assertIsNone(rows[0]["cited"])
        self.assertEqual(rows[0]["outcome"], "error")
        self.assertEqual(rows[0]["error"], "http_429")
        self.assertIn("실패", rows[0]["note"])
        self.assertIn("http_429", rows[0]["note"])

    def test_no_citation_means_not_cited(self):
        send = Sender({"openai": {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "모르겠다.", "annotations": []}]}]}})
        rows = measure.run_auto(QUERIES[:1], ["chatgpt"], 1, HOST, {"chatgpt": SECRET},
                                send=send, delay=0)
        self.assertIs(rows[0]["cited"], False)
        self.assertEqual(rows[0]["cited_urls"], [])

    def test_api_rows_aggregate_with_manual_rows(self):
        send = Sender()
        rows = measure.run_auto(QUERIES[:1], ["chatgpt"], 1, HOST, {"chatgpt": SECRET},
                                send=send, delay=0, date_str="2026-09-15")
        measure.append_rows(self.log, rows)
        measure.append_rows(self.log, [log_row("2026-09-15", "Q02", "gemini", 1, False)])
        self.run_cli(["report", self.audit])
        summary = json.loads(self.read("summary.json"))
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["modes"], {"api": 1, "manual": 1})

    def test_key_never_reaches_any_file(self):
        os.environ["OPENAI_API_KEY"] = SECRET
        send = Sender()
        rows = measure.run_auto(QUERIES, ["chatgpt"], 2, HOST, {"chatgpt": SECRET},
                                send=send, delay=0, date_str="2026-09-15")
        measure.append_rows(self.log, rows)
        self.run_cli(["report", self.audit])
        self.run_cli(["form", self.audit, "--date", "2026-09-15"])
        # 헤더에는 실렸는지 확인 (전송은 되어야 한다)
        self.assertIn(SECRET, send.calls[0]["headers"]["Authorization"])
        for name in os.listdir(self.mdir):
            with open(os.path.join(self.mdir, name), encoding="utf-8") as fh:
                body = fh.read()
            self.assertNotIn(SECRET, body, name)
            self.assertNotIn("OPENAI_API_KEY=", body, name)
        # 응답 원문도 남기지 않는다
        self.assertNotIn("장기 요금은 공개 페이지에", self.read("log.jsonl"))


if __name__ == "__main__":
    unittest.main()
