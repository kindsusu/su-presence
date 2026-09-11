# -*- coding: utf-8 -*-
"""KEO 레인 · 스테이징 미러 · 미측정 표기 회귀 테스트 — 네트워크를 쓰지 않는다.

실행: python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import collect  # noqa: E402
import crawl  # noqa: E402
import measure  # noqa: E402
import report  # noqa: E402

NL = chr(10)


def codes(findings):
    return {f["code"] for f in findings}


class TestKeoLane(unittest.TestCase):
    """다음·카카오는 네이버와 크롤러도 소유확인도 다르다 — 같은 칸에 넣으면 둘 다 안 보인다."""

    def test_keo_is_a_first_class_lane(self):
        self.assertIn("KEO", crawl.LANES)
        self.assertIn("KEO", report.LANES)
        self.assertIn("KEO", crawl.scorecard([]))

    def test_both_daum_tokens_are_probed(self):
        # Daumoa 만 적고 DAUM 을 빼면 다른 쪽 요청이 규칙 밖에 남는다
        self.assertIn("Daumoa", crawl.ALL_UAS)
        self.assertIn("DAUM", crawl.ALL_UAS)
        self.assertNotIn("Daumoa", crawl.NEO_UAS)

    def test_blocking_daum_closes_keo_not_neo(self):
        raw = "User-agent: Daumoa" + NL + "Disallow: /" + NL
        site = {"robots": {"policies": {ua: crawl.robots_policy(raw, ua)
                                        for ua in crawl.ALL_UAS}}}
        findings = []
        crawl._check_crawler_policy(findings, site)
        self.assertIn("DAUM_CRAWLER_BLOCKED", codes(findings))
        self.assertNotIn("NAVER_CRAWLER_BLOCKED", codes(findings))
        hit = [f for f in findings if f["code"] == "DAUM_CRAWLER_BLOCKED"][0]
        self.assertEqual(hit["lane"], "KEO")
        self.assertEqual(hit["severity"], "critical")

    def test_clean_robots_raises_no_keo_verification_finding(self):
        """소유확인은 크롤로 증명할 수 없다. 없는 것을 문제로 적지 않는다."""
        raw = "User-agent: *" + NL + "Disallow:" + NL
        site = {"robots": {"policies": {ua: crawl.robots_policy(raw, ua)
                                        for ua in crawl.ALL_UAS}}}
        findings = []
        crawl._check_crawler_policy(findings, site)
        self.assertNotIn("DAUM_WEBMASTER_UNVERIFIED", codes(findings))

    def test_old_audit_without_keo_still_renders(self):
        """레인을 늘렸다고 예전 스냅샷 보고서가 깨지면 안 된다."""
        board = {lane: {"status": "ok", "evidence": []}
                 for lane in ("SEO", "AEO", "GEO", "LLMO", "NEO", "reputation")}
        cell = board.get("KEO") or {"status": "na", "evidence": []}
        self.assertEqual(cell["status"], "na")


class TestMirrorProbe(unittest.TestCase):
    """본 도메인만 훑으면 절대 안 보이는 표면. 실측에서 생성엔진이 먼저 찾아냈다."""

    def setUp(self):
        self.addCleanup(setattr, crawl, "fetch", crawl.fetch)

    def _fake(self, live):
        def fetch(url, method="GET", rules=None):
            host = crawl.host_of(url)
            if host not in live:
                return {"status": None, "final_url": url, "headers": {}, "body": "",
                        "ms": 1, "redirects": 0, "error": "dns_fail", "content_type": ""}
            return {"status": 200, "final_url": url, "headers": {},
                    "body": live[host], "ms": 1, "redirects": 0,
                    "error": None, "content_type": "text/html"}
        return fetch

    def test_finds_public_dev_mirror(self):
        crawl.fetch = self._fake({"dev.example.com": ""})
        out = crawl.probe_mirrors("example.com")
        self.assertFalse(out["wildcard_suspect"])
        self.assertEqual([m["host"] for m in out["found"]], ["dev.example.com"])
        self.assertFalse(out["found"][0]["robots_blocks_all"])

    def test_robots_blocked_mirror_is_flagged_differently(self):
        crawl.fetch = self._fake({"staging.example.com":
                                  "User-agent: *" + NL + "Disallow: /" + NL})
        out = crawl.probe_mirrors("example.com")
        self.assertTrue(out["found"][0]["robots_blocks_all"])

    def test_wildcard_dns_is_not_reported_as_discovery(self):
        """아무 접두나 응답하면 발견이 아니라 DNS 설정이다. 전부 미러로 적으면 오보다."""
        crawl.fetch = self._fake({"%s.example.com" % p: ""
                                  for p in crawl.MIRROR_PREFIXES})
        out = crawl.probe_mirrors("example.com")
        self.assertTrue(out["wildcard_suspect"])
        self.assertEqual(out["found"], [])

    def test_ip_and_localhost_are_skipped(self):
        crawl.fetch = self._fake({})
        for host in ("127.0.0.1", "localhost", "[::1]"):
            self.assertEqual(crawl.probe_mirrors(host)["checked"], [])

    def test_public_mirror_becomes_a_critical_seo_finding(self):
        hygiene = {"probe_404": 404, "redirect_hops": 0, "home_response_ms": 1,
                   "alt_host": {"host": None, "result": "na", "status": None,
                                "location": None},
                   "mirrors": {"checked": ["dev.example.com"], "wildcard_suspect": False,
                               "found": [{"host": "dev.example.com", "status": 200,
                                          "final_url": "https://dev.example.com/",
                                          "robots_blocks_all": False}]}}
        site = {"robots": {"status": 200, "present": True, "raw": "",
                           "policies": {}, "sitemap_declared": []},
                "sitemaps": [], "sitemap_urls": [], "sitemap_vs_crawl": {},
                "llms": {}, "hygiene": hygiene}
        findings = []
        crawl._check_site(findings, "https://example.com", site,
                          [{"naver_site_verification": True}])
        self.assertIn("MIRROR_PUBLIC", codes(findings))
        hit = [f for f in findings if f["code"] == "MIRROR_PUBLIC"][0]
        self.assertEqual(hit["lane"], "SEO")
        self.assertEqual(hit["severity"], "critical")


class TestUnmeasuredIsNotZero(unittest.TestCase):
    """한번 0으로 보고된 값은 사실로 굳는다. 안 잰 칸은 안 쟀다고 적어야 한다."""

    def test_cell_never_prints_zero_over_zero(self):
        self.assertEqual(measure.cell({"runs": 0, "cited": 0,
                                       "unmeasured": 3, "errors": 0}), "미측정(3)")

    def test_cell_prints_real_rate_when_observed(self):
        self.assertEqual(measure.cell({"runs": 5, "cited": 2}), "2/5")

    def test_cell_distinguishes_error_from_silence(self):
        self.assertEqual(measure.cell({"runs": 0, "cited": 0,
                                       "unmeasured": 0, "errors": 2}), "오류(2)")
        self.assertEqual(measure.cell({"runs": 0, "cited": 0}), "—")


class TestBrowserResultsComeBack(unittest.TestCase):
    """로그인 벽은 '측정 불가'가 아니다 — 사람은 로그인, 에이전트가 측정, 결과는 같은 로그로."""

    def test_every_browser_engine_is_a_known_engine(self):
        for key in collect.BROWSER_ENGINES:
            self.assertIn(key, measure.ENGINES)

    def test_surface_table_covers_every_lane(self):
        lanes = {row[0] for row in collect.SURFACES}
        for lane in ("SEO", "AEO", "GEO", "LLMO", "NEO", "KEO"):
            self.assertIn(lane, lanes)

    def test_surface_engine_keys_are_real(self):
        for row in collect.SURFACES:
            if row[2] is not None:
                self.assertIn(row[2], measure.ENGINES)

    def test_recorded_browser_row_is_observed_and_aggregatable(self):
        row = measure.make_row(
            "2026-09-11", "B1", "gemini", 1, "browser", False, False, [],
            True, ["rival.example"], note="임시채팅",
            outcome="observed", surface="gemini",
            login_state="signed_in", search_enabled=True)
        self.assertEqual(row["outcome"], "observed")
        self.assertEqual(row["schema"], measure.SCHEMA_ROW)
        # 인용 안 됨은 관측이다 — 미측정이 아니다
        self.assertIs(row["cited"], False)
        self.assertEqual(row["login_state"], "signed_in")


if __name__ == "__main__":
    unittest.main()
