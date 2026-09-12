# -*- coding: utf-8 -*-
"""drift.py 회귀 테스트 — 네트워크를 쓰지 않는다.

실행: python -m unittest discover tests
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import drift  # noqa: E402


# ─────────────────────────────────────────────────────────── 픽스처

BASE = "https://example.com"
HOST = "example.com"


def page(url, title="예시 주식회사", jsonld=1, noindex=False):
    return {"url": url, "status": 200, "title": title,
            "meta_description": "설명 " + url, "jsonld_count": jsonld,
            "meta_robots": "noindex" if noindex else "", "x_robots_tag": ""}


def audit(pages=None, findings=None, scorecard=None, sitemap_urls=2,
          generated_at="2026-09-01T00:00:00+00:00"):
    pages = pages if pages is not None else [page(BASE + "/"), page(BASE + "/faq", title="FAQ")]
    ok = [p for p in pages if p["status"] == 200]
    stats = {
        "pages_crawled": len(pages),
        "unique_titles": len({p["title"] for p in ok if p["title"]}),
        "unique_descriptions": len({p["meta_description"] for p in ok if p["meta_description"]}),
        "pages_with_jsonld": sum(1 for p in ok if p["jsonld_count"]),
        "pages_noindex": sum(1 for p in ok
                             if "noindex" in (p.get("meta_robots") or "").lower()),
    }
    board = {lane: {"status": "ok", "note": ""} for lane in
             ("SEO", "AEO", "GEO", "LLMO", "NEO", "reputation")}
    board.update(scorecard or {})
    return {
        "schema": "su-multi-geo/audit/1",
        "generated_at": generated_at,
        "target": {"input": HOST, "base": BASE, "host": HOST},
        "site": {"sitemaps": [{"url": BASE + "/sitemap.xml", "status": 200,
                               "is_index": False, "url_count": sitemap_urls}]},
        "pages": pages,
        "stats": stats,
        "findings": findings or [],
        "scorecard": board,
    }


def finding(code, lane="SEO", severity="warn", pages=None, urls=None):
    data = {"pages": pages} if pages is not None else {}
    return {"lane": lane, "severity": severity, "code": code,
            "message": "%s 메시지" % code, "urls": urls or [], "data": data}


def summary(brand=(4, 10), nonbrand=(3, 10), ours=None, competitors=None):
    return {
        "schema": "su-multi-geo/measure/1",
        "generated_at": "2026-09-01T00:00:00+00:00",
        "target": {"base": BASE, "host": HOST},
        "engines": [{
            "engine": "chatgpt", "label": "ChatGPT",
            "brand": {"runs": brand[1], "cited": brand[0], "mentioned": brand[0],
                      "queries": 2, "queries_cited": 2,
                      "rate": round(brand[0] / brand[1], 4) if brand[1] else None,
                      "mentioned_rate": None},
            "nonbrand": {"runs": nonbrand[1], "cited": nonbrand[0], "mentioned": nonbrand[0],
                         "queries": 2, "queries_cited": 1,
                         "rate": round(nonbrand[0] / nonbrand[1], 4) if nonbrand[1] else None,
                         "mentioned_rate": None},
        }],
        "urls": {"ours": [{"url": u, "count": n} for u, n in (ours or [(BASE + "/faq", 3)])],
                 "competitors": [{"domain": d, "count": n}
                                 for d, n in (competitors or [("rival.example", 5)])]},
        "by_query": [], "trend": [], "next_measure": None, "headline": [],
    }


class DriftCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-test-")
        self.dir = os.path.join(self.tmp, "out", HOST)
        os.makedirs(self.dir)
        self.audit_path = os.path.join(self.dir, "audit.json")
        self.out = io.StringIO()          # 콘솔 출력은 삼킨다 — 검사할 때만 읽는다
        self._stdout, sys.stdout = sys.stdout, self.out

    def tearDown(self):
        sys.stdout = self._stdout
        shutil.rmtree(self.tmp, ignore_errors=True)

    def console(self) -> str:
        return self.out.getvalue()

    # ─── 헬퍼

    def write_audit(self, report):
        with open(self.audit_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False)
        return self.audit_path

    def write_tmp(self, name, obj):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        return path

    def snap(self, report, date_str, measure=None, extra=None):
        self.write_audit(report)
        argv = ["snapshot", self.audit_path, "--date", date_str]
        if measure:
            argv += ["--measure", self.write_tmp("summary-%s.json" % date_str, measure)]
        return drift.main(argv + (extra or []))

    def compare(self, extra=None):
        return drift.main(["compare", self.audit_path] + (extra or []))

    def index(self):
        return drift.load_json(os.path.join(self.dir, "history", "index.json"))

    def result(self):
        return drift.load_json(os.path.join(self.dir, "drift.json"))

    def two(self, before, after, b_measure=None, a_measure=None):
        self.snap(before, "2026-09-01", measure=b_measure)
        self.snap(after, "2026-09-15", measure=a_measure)

    def codes(self, key):
        return {item["code"] for item in self.result()[key]}


# ─────────────────────────────────────────────────────────── snapshot

class TestSnapshot(DriftCase):

    def test_first_snapshot_becomes_baseline(self):
        self.snap(audit(), "2026-09-01")
        index = self.index()
        self.assertEqual(index["schema"], drift.SCHEMA_HISTORY)
        self.assertEqual(index["baseline_date"], "2026-09-01")
        self.assertEqual(len(index["snapshots"]), 1)
        self.assertEqual(index["snapshots"][0]["kind"], "audit")
        self.assertEqual(index["snapshots"][0]["file"], "audit-2026-09-01.json")

    def test_second_snapshot_does_not_move_baseline(self):
        self.two(audit(), audit())
        self.assertEqual(self.index()["baseline_date"], "2026-09-01")

    def test_baseline_flag_moves_baseline(self):
        self.two(audit(), audit())
        self.snap(audit(), "2026-09-20", extra=["--baseline"])
        self.assertEqual(self.index()["baseline_date"], "2026-09-20")

    def test_next_due_is_last_snapshot_plus_14(self):
        self.snap(audit(), "2026-09-01")
        self.assertEqual(self.index()["next_due"], "2026-09-15")
        self.snap(audit(), "2026-09-15")
        self.assertEqual(self.index()["next_due"], "2026-09-29")

    def test_sha256_matches_stored_copy(self):
        self.snap(audit(), "2026-09-01")
        snap = self.index()["snapshots"][0]
        stored = os.path.join(self.dir, "history", snap["file"])
        self.assertEqual(snap["sha256"], drift.sha256_of(stored))
        self.assertEqual(snap["sha256"], drift.sha256_of(self.audit_path))

    def test_same_date_same_kind_refused(self):
        self.snap(audit(), "2026-09-01")
        with self.assertRaises(SystemExit):
            self.snap(audit(pages=[page(BASE + "/")]), "2026-09-01")
        # 거부됐으면 기존 스냅샷은 그대로다 (기준선 보호)
        self.assertEqual(self.index()["snapshots"][0]["line"],
                         drift.audit_line(audit()))

    def test_force_overwrites(self):
        self.snap(audit(), "2026-09-01")
        self.snap(audit(pages=[page(BASE + "/")]), "2026-09-01", extra=["--force"])
        index = self.index()
        self.assertEqual(len(index["snapshots"]), 1)
        self.assertIn("페이지 1", index["snapshots"][0]["line"])

    def test_measure_snapshot_stored_alongside(self):
        self.snap(audit(), "2026-09-01", measure=summary())
        kinds = [s["kind"] for s in self.index()["snapshots"]]
        self.assertEqual(kinds, ["audit", "measure"])

    def test_measure_conflict_leaves_audit_untouched(self):
        self.snap(audit(), "2026-09-01", measure=summary())
        with self.assertRaises(SystemExit):
            self.snap(audit(pages=[page(BASE + "/")]), "2026-09-01", measure=summary())
        self.assertIn("페이지 2", self.index()["snapshots"][0]["line"])

    def test_rejects_non_audit_json(self):
        path = self.write_tmp("summary.json", summary())
        with self.assertRaises(SystemExit):
            drift.main(["snapshot", path])


# ─────────────────────────────────────────────────────────── compare — 회귀 6종

class TestRegressions(DriftCase):

    def test_needs_two_audit_snapshots(self):
        self.snap(audit(), "2026-09-01")
        with self.assertRaises(SystemExit):
            self.compare()

    def test_clean_compare_passes(self):
        self.two(audit(), audit())
        self.assertEqual(self.compare(), 0)
        self.assertEqual(self.result()["regressions"], [])
        self.assertEqual(self.result()["next_due"], "2026-09-29")

    def test_regression_new_noindex(self):
        after = audit(pages=[page(BASE + "/"), page(BASE + "/faq", title="FAQ", noindex=True)])
        self.two(audit(), after)
        self.assertEqual(self.compare(), 1)
        self.assertIn("noindex", self.codes("regressions"))

    def test_regression_jsonld_pages_drop(self):
        after = audit(pages=[page(BASE + "/"), page(BASE + "/faq", title="FAQ", jsonld=0)])
        self.two(audit(), after)
        self.assertEqual(self.compare(), 1)
        self.assertIn("jsonld_pages", self.codes("regressions"))

    def test_regression_duplicate_titles_up(self):
        before = audit(findings=[finding("TITLE_DUPLICATE", pages=2)])
        after = audit(findings=[finding("TITLE_DUPLICATE", pages=5)])
        self.two(before, after)
        self.assertEqual(self.compare(), 1)
        self.assertIn("dup_titles", self.codes("regressions"))

    def test_regression_sitemap_shrinks_over_20pct(self):
        self.two(audit(sitemap_urls=100), audit(sitemap_urls=70))
        self.assertEqual(self.compare(), 1)
        self.assertIn("sitemap_urls", self.codes("regressions"))

    def test_sitemap_small_shrink_is_not_regression(self):
        self.two(audit(sitemap_urls=100), audit(sitemap_urls=90))
        self.assertEqual(self.compare(), 0)
        self.assertIn("sitemap_urls", self.codes("unchanged"))

    def test_regression_scorecard_worse(self):
        self.two(audit(), audit(scorecard={"GEO": {"status": "bad", "note": ""}}))
        self.assertEqual(self.compare(), 1)
        self.assertIn("scorecard", self.codes("regressions"))

    def test_regression_nonbrand_citation_rate_down(self):
        self.two(audit(), audit(),
                 b_measure=summary(nonbrand=(6, 10)), a_measure=summary(nonbrand=(2, 10)))
        self.assertEqual(self.compare(), 1)
        self.assertIn("nonbrand_rate", self.codes("regressions"))

    def test_brand_only_drop_is_not_a_regression(self):
        self.two(audit(), audit(),
                 b_measure=summary(brand=(9, 10)), a_measure=summary(brand=(4, 10)))
        self.assertEqual(self.compare(), 0)


class TestImprovements(DriftCase):

    def test_noindex_cleared_is_improvement(self):
        before = audit(pages=[page(BASE + "/", noindex=True), page(BASE + "/faq", title="FAQ")])
        self.two(before, audit())
        self.assertEqual(self.compare(), 0)
        self.assertIn("noindex", self.codes("improvements"))

    def test_scorecard_better_is_improvement(self):
        self.two(audit(scorecard={"GEO": {"status": "bad", "note": ""}}), audit())
        self.assertEqual(self.compare(), 0)
        self.assertIn("scorecard", self.codes("improvements"))

    def test_nonbrand_rate_up_is_improvement(self):
        self.two(audit(), audit(),
                 b_measure=summary(nonbrand=(1, 10)), a_measure=summary(nonbrand=(6, 10)))
        self.assertEqual(self.compare(), 0)
        self.assertIn("nonbrand_rate", self.codes("improvements"))

    def test_resolved_findings_are_reported(self):
        self.two(audit(findings=[finding("TITLE_MISSING", severity="critical")]), audit())
        self.compare()
        self.assertEqual([f["code"] for f in self.result()["audit_diff"]["resolved"]],
                         ["TITLE_MISSING"])


# ─────────────────────────────────────────────────────────── 낡은 기준선

class TestStale(DriftCase):

    def test_old_baseline_warns(self):
        old = (date.today() - timedelta(days=120)).isoformat()
        recent = (date.today() - timedelta(days=1)).isoformat()
        self.snap(audit(), old)
        self.snap(audit(), recent)
        self.compare()
        warnings = " ".join(self.result()["warnings"])
        self.assertIn("기준선이 낡았다", warnings)
        self.assertEqual(self.result()["baseline_age_days"], 120)

    def test_fresh_baseline_does_not_warn(self):
        old = (date.today() - timedelta(days=14)).isoformat()
        recent = date.today().isoformat()
        self.snap(audit(), old)
        self.snap(audit(), recent)
        self.compare()
        self.assertNotIn("기준선이 낡았다", " ".join(self.result()["warnings"]))

    def test_stale_days_threshold_is_configurable(self):
        old = (date.today() - timedelta(days=20)).isoformat()
        recent = date.today().isoformat()
        self.snap(audit(), old)
        self.snap(audit(), recent)
        self.compare(["--stale-days", "10"])
        self.assertIn("기준선이 낡았다", " ".join(self.result()["warnings"]))


# ─────────────────────────────────────────────────────────── 측정 드리프트

class TestMeasureDiff(DriftCase):

    def test_missing_measure_snapshots_warn_not_crash(self):
        self.two(audit(), audit())
        self.compare()
        self.assertIsNone(self.result()["measure_diff"])
        self.assertIn("측정 스냅샷", " ".join(self.result()["warnings"]))

    def test_new_and_lost_our_urls(self):
        self.two(audit(), audit(),
                 b_measure=summary(ours=[(BASE + "/faq", 3), (BASE + "/old", 1)]),
                 a_measure=summary(ours=[(BASE + "/faq", 5), (BASE + "/new", 2)]))
        self.compare()
        md = self.result()["measure_diff"]
        self.assertEqual(md["ours_new"], [BASE + "/new"])
        self.assertEqual(md["ours_lost"], [BASE + "/old"])

    def test_engine_points_delta(self):
        self.two(audit(), audit(),
                 b_measure=summary(nonbrand=(2, 10)), a_measure=summary(nonbrand=(5, 10)))
        self.compare()
        row = self.result()["measure_diff"]["engines"][0]
        self.assertEqual(row["engine"], "chatgpt")
        self.assertEqual(row["nonbrand"]["points"], 30.0)

    def test_competitor_domains_before_after(self):
        self.two(audit(), audit(),
                 b_measure=summary(competitors=[("rival.example", 5)]),
                 a_measure=summary(competitors=[("rival.example", 2), ("other.example", 7)]))
        self.compare()
        comps = {c["domain"]: (c["before"], c["after"])
                 for c in self.result()["measure_diff"]["competitors"]}
        self.assertEqual(comps["rival.example"], (5, 2))
        self.assertEqual(comps["other.example"], (0, 7))


# ─────────────────────────────────────────────────────────── status

class TestStatus(DriftCase):

    def run_status(self):
        self.out.truncate(0)
        self.out.seek(0)
        drift.main(["status", self.audit_path])
        return self.console()

    def test_days_left(self):
        self.snap(audit(), (date.today() - timedelta(days=4)).isoformat())
        out = self.run_status()
        self.assertIn("10일 남음", out)
        self.assertIn("←기준선", out)

    def test_overdue(self):
        self.snap(audit(), (date.today() - timedelta(days=20)).isoformat())
        out = self.run_status()
        self.assertIn("6일 초과", out)

    def test_counts_and_last_measure(self):
        self.snap(audit(), "2026-09-01", measure=summary())
        self.snap(audit(), "2026-09-15")
        out = self.run_status()
        self.assertIn("스냅샷 수     : 3", out)
        self.assertIn("마지막 측정일 : 2026-09-01", out)

    def test_empty_history_is_not_an_error(self):
        self.write_audit(audit())
        self.assertEqual(drift.main(["status", self.audit_path]), 0)

    def test_host_flag_resolves_same_dir(self):
        self.snap(audit(), "2026-09-01")
        self.assertEqual(drift.main(["status", "--host", HOST,
                                     "--out", os.path.join(self.tmp, "out")]), 0)


# ─────────────────────────────────────────────────────────── timeline

class TestTimeline(DriftCase):

    def read_timeline(self):
        with open(os.path.join(self.dir, "TIMELINE.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_one_row_per_date(self):
        self.snap(audit(), "2026-09-01", measure=summary())
        self.snap(audit(), "2026-09-15")
        self.snap(audit(), "2026-09-29", measure=summary(nonbrand=(8, 10)))
        drift.main(["timeline", self.audit_path])
        body = self.read_timeline()
        rows = [ln for ln in body.splitlines() if ln.startswith("| 2026-")]
        self.assertEqual(len(rows), 3)
        self.assertIn("2026-09-01", rows[0])
        self.assertIn("2026-09-29", rows[2])

    def test_missing_measure_shows_dash_not_zero(self):
        self.snap(audit(), "2026-09-01")
        drift.main(["timeline", self.audit_path])
        row = [ln for ln in self.read_timeline().splitlines() if ln.startswith("| 2026-")][0]
        self.assertIn("—", row)

    def test_citation_rate_rendered(self):
        self.snap(audit(), "2026-09-01", measure=summary(nonbrand=(3, 10)))
        drift.main(["timeline", self.audit_path])
        row = [ln for ln in self.read_timeline().splitlines() if ln.startswith("| 2026-")][0]
        self.assertIn("30% (3/10)", row)

    def test_empty_history_errors(self):
        self.write_audit(audit())
        with self.assertRaises(SystemExit):
            drift.main(["timeline", self.audit_path])


# ─────────────────────────────────────────────────────────── 산출물

class TestOutputs(DriftCase):

    def test_drift_md_orders_regressions_first(self):
        after = audit(pages=[page(BASE + "/"), page(BASE + "/faq", title="FAQ", noindex=True)])
        self.two(audit(), after)
        self.compare()
        with open(os.path.join(self.dir, "DRIFT.md"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertLess(body.index("## ❌ 회귀"), body.index("## ✅ 개선"))
        self.assertLess(body.index("## ✅ 개선"), body.index("## — 변화 없음"))
        self.assertIn("## 다음 재측정일 — 2026-09-29", body)
        self.assertIn("python tools/drift.py compare", body)

    def test_drift_json_schema_and_next_due(self):
        self.two(audit(), audit())
        self.compare()
        result = self.result()
        self.assertEqual(result["schema"], drift.SCHEMA_DRIFT)
        self.assertEqual(result["from"], "2026-09-01")
        self.assertEqual(result["to"], "2026-09-15")
        self.assertEqual(result["baseline"], "2026-09-01")
        self.assertTrue(result["next_due"])

    def test_explicit_from_to(self):
        self.snap(audit(), "2026-09-01")
        self.snap(audit(sitemap_urls=9), "2026-09-15")
        self.snap(audit(), "2026-09-29")
        self.compare(["--from", "2026-09-15", "--to", "2026-09-29"])
        self.assertEqual(self.result()["from"], "2026-09-15")

    def test_unknown_date_errors(self):
        self.two(audit(), audit())
        with self.assertRaises(SystemExit):
            self.compare(["--from", "2020-01-01"])


if __name__ == "__main__":
    unittest.main()
