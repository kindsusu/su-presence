# -*- coding: utf-8 -*-
"""verify.py 회귀 테스트 — 네트워크를 쓰지 않는다 (fetch를 가짜로 주입).

실행: python -m unittest discover tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import crawl  # noqa: E402
import verify  # noqa: E402


# ─────────────────────────────────────────────────────────── 픽스처

BASE = "https://example.com"
HOME = BASE + "/"
FAQ = BASE + "/faq"

ROBOTS_BEFORE = "User-agent: *\nDisallow: /admin/\n"
ROBOTS_AFTER = (
    ROBOTS_BEFORE + "\n"
    "User-agent: GPTBot\nAllow: /\n\n"
    "User-agent: ClaudeBot\nAllow: /\n\n"
    "Sitemap: https://example.com/sitemap.xml\n"
)

SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    "  <url><loc>%s</loc></url>\n"
    "  <url><loc>%s</loc></url>\n"
    "</urlset>\n" % (HOME, FAQ)
)

LLMS_CLEAN = "# 예시 주식회사\n\n> 한 문장 설명.\n\n## 주요 페이지\n- [FAQ](%s): 자주 묻는 질문\n" % FAQ
LLMS_TODO = LLMS_CLEAN + "- 출처: <<TODO: 무엇의 원출처인지>>\n"

ORG_LD = {"@context": "https://schema.org", "@type": "Organization",
          "@id": BASE + "#organization", "name": "예시 주식회사"}
FAQ_LD = {"@context": "https://schema.org", "@type": "FAQPage",
          "mainEntity": [{"@type": "Question", "name": "배송은 얼마나 걸리나요?",
                          "acceptedAnswer": {"@type": "Answer",
                                             "text": "주문 다음 영업일에 출고된다."}}]}


def ld_script(obj):
    return '<script type="application/ld+json">%s</script>' % json.dumps(obj, ensure_ascii=False)


def html(title="예시 주식회사", body="회사 소개 본문.", head="", canonical=None):
    link = '<link rel="canonical" href="%s">' % canonical if canonical else ""
    return ("<html lang=\"ko\"><head><title>%s</title>%s%s</head>"
            "<body><h1>%s</h1><p>%s</p></body></html>" % (title, link, head, title, body))


HOME_HTML = html(head=ld_script(ORG_LD), canonical=HOME)
FAQ_HTML = html(
    title="자주 묻는 질문 | 예시 주식회사",
    body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다.",
    head=ld_script(FAQ_LD), canonical=FAQ)


def resp(body, status=200, ctype="text/html; charset=utf-8", headers=None, final_url=None):
    return {"status": status, "final_url": final_url, "headers": headers or {},
            "body": body, "ms": 1, "redirects": 0, "error": None, "content_type": ctype}


def live(**override):
    """기본은 '전부 제대로 배포된 사이트'. 테스트마다 필요한 응답만 갈아 끼운다."""
    pages = {
        BASE + "/robots.txt": resp(ROBOTS_AFTER, ctype="text/plain"),
        BASE + "/sitemap.xml": resp(SITEMAP, ctype="application/xml"),
        BASE + "/llms.txt": resp(LLMS_CLEAN, ctype="text/plain"),
        HOME: resp(HOME_HTML),
        FAQ: resp(FAQ_HTML),
    }
    pages.update(override)
    calls = []

    def fetch(url):
        calls.append(url)
        return pages.get(url, resp("", status=404))

    fetch.calls = calls
    return fetch


def page(url, **kw):
    base = {"url": url, "status": 200, "final_url": url, "title": "제목",
            "meta_description": None, "meta_robots": None, "x_robots_tag": None,
            "canonical": url, "h1": ["제목"], "jsonld_count": 0, "jsonld_types": [],
            "text_chars": 500, "og": {}, "naver_site_verification": False,
            "lang": "ko", "response_ms": 5, "error": None}
    base.update(kw)
    return base


def audit(**kw):
    base = {
        "schema": crawl.SCHEMA,
        "generated_at": "2026-09-01T00:00:00+00:00",
        "target": {"input": "example.com", "base": BASE, "host": "example.com"},
        "site": {"robots": {"status": 200, "present": True, "raw": ROBOTS_BEFORE,
                            "policies": {}, "sitemap_declared": []}},
        "pages": [page(HOME), page(FAQ)],
        "stats": {"pages_crawled": 2, "unique_titles": 2, "unique_descriptions": 0,
                  "pages_with_jsonld": 0, "pages_noindex": 0},
        "findings": [],
        "scorecard": {lane: {"status": "ok", "evidence": []}
                      for lane in ["SEO", "AEO", "GEO", "LLMO", "NEO", "reputation"]},
    }
    base.update(kw)
    return base


class DeployBase(unittest.TestCase):
    """out/<host>/deploy 패키지를 임시 폴더에 만들어 둔다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="su-verify-")
        self.write("robots.txt", ROBOTS_AFTER)
        self.write("sitemap.xml", SITEMAP)
        self.write("llms.txt", LLMS_CLEAN)
        self.write("jsonld/organization.json", json.dumps(ORG_LD, ensure_ascii=False))
        self.write("jsonld/faq.faq.json", json.dumps(FAQ_LD, ensure_ascii=False))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def run_verify(self, fetch=None, audit_report=None, **kw):
        return verify.verify_deploy(audit_report or audit(), self.dir,
                                    fetch=fetch or live(), delay=0, **kw)

    def status_of(self, result, cid):
        for check in result["checks"]:
            if check["id"] == cid:
                return check["status"]
        return None

    def check_of(self, result, cid):
        return [c for c in result["checks"] if c["id"] == cid][0]


# ─────────────────────────────────────────────────────────── 정상 배포

class TestHealthyDeploy(DeployBase):

    def test_all_green(self):
        result = self.run_verify()
        self.assertEqual(result["schema"], verify.SCHEMA)
        self.assertEqual(result["mode"], "deploy")
        self.assertEqual(result["summary"]["fail"], 0, result["checks"])
        self.assertEqual(result["exit_code"], 0)

    def test_noindex_check_is_first(self):
        result = self.run_verify()
        self.assertEqual(result["checks"][0]["id"], "noindex")

    def test_never_requests_other_hosts(self):
        fetch = live()
        self.run_verify(fetch=fetch)
        for url in fetch.calls:
            self.assertEqual(verify.crawl.host_of(url), "example.com", url)

    def test_offsite_redirect_is_not_trusted(self):
        fetch = live(**{HOME: resp(HOME_HTML, final_url="https://evil.example.net/")})
        result = self.run_verify(fetch=fetch)
        self.assertEqual(self.status_of(result, "jsonld.present"), "fail")


# ─────────────────────────────────────────────────────────── robots

class TestRobots(DeployBase):

    def test_preserved(self):
        self.assertEqual(self.status_of(self.run_verify(), "robots.preserved"), "pass")

    def test_lost_original_lines_fail(self):
        # 배포하며 기존 Disallow를 날려 먹은 경우
        served = ROBOTS_AFTER.replace("Disallow: /admin/\n", "")
        result = self.run_verify(live(**{BASE + "/robots.txt": resp(served, ctype="text/plain")}))
        check = self.check_of(result, "robots.preserved")
        self.assertEqual(check["status"], "fail")
        self.assertIn("Disallow: /admin/", check["evidence"]["lost"])
        self.assertEqual(result["exit_code"], 1)

    def test_ua_block_not_served_fails(self):
        result = self.run_verify(
            live(**{BASE + "/robots.txt": resp(ROBOTS_BEFORE, ctype="text/plain")}))
        check = self.check_of(result, "robots.policy")
        self.assertEqual(check["status"], "fail")
        self.assertIn("GPTBot", [m["ua"] for m in check["evidence"]["mismatch"]])

    def test_missing_sitemap_declaration(self):
        served = ROBOTS_AFTER.replace("Sitemap: https://example.com/sitemap.xml\n", "")
        result = self.run_verify(live(**{BASE + "/robots.txt": resp(served, ctype="text/plain")}))
        self.assertEqual(self.status_of(result, "robots.sitemap"), "fail")

    def test_robots_404(self):
        result = self.run_verify(live(**{BASE + "/robots.txt": resp("", status=404)}))
        self.assertEqual(self.status_of(result, "robots.status"), "fail")


# ─────────────────────────────────────────────────────────── sitemap

class TestSitemap(DeployBase):

    def test_loc_404_detected(self):
        result = self.run_verify(live(**{FAQ: resp("", status=404)}))
        check = self.check_of(result, "sitemap.locs")
        self.assertEqual(check["status"], "fail")
        self.assertEqual([d["url"] for d in check["evidence"]["dead"]], [FAQ])

    def test_broken_xml(self):
        result = self.run_verify(
            live(**{BASE + "/sitemap.xml": resp("<urlset><url>", ctype="application/xml")}))
        self.assertEqual(self.status_of(result, "sitemap.reachable"), "fail")

    def test_noindex_page_in_sitemap(self):
        served = FAQ_HTML.replace("<head>", '<head><meta name="robots" content="noindex">')
        report = audit(pages=[page(HOME), page(FAQ, meta_robots="noindex")])
        result = self.run_verify(live(**{FAQ: resp(served)}), audit_report=report)
        self.assertEqual(self.status_of(result, "sitemap.noindex"), "fail")
        self.assertEqual(self.status_of(result, "noindex"), "pass")  # 새로 생긴 건 아니다

    def test_canonical_mismatch(self):
        served = html(title="자주 묻는 질문", head=ld_script(FAQ_LD), canonical=HOME)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        self.assertEqual(self.status_of(result, "sitemap.canonical"), "fail")

    def test_max_urls_cap(self):
        result = self.run_verify(max_urls=1)
        check = self.check_of(result, "sitemap.locs")
        self.assertTrue(check["evidence"]["capped"])
        self.assertEqual(check["evidence"]["checked"], 1)


# ─────────────────────────────────────────────────────────── llms.txt

class TestLlms(DeployBase):

    def test_clean(self):
        self.assertEqual(self.status_of(self.run_verify(), "llms.todo"), "pass")

    def test_todo_marker_is_incomplete_deploy(self):
        result = self.run_verify(live(**{BASE + "/llms.txt": resp(LLMS_TODO, ctype="text/plain")}))
        check = self.check_of(result, "llms.todo")
        self.assertEqual(check["status"], "fail")
        self.assertIn("미완성", check["message"])
        self.assertEqual(result["exit_code"], 1)

    def test_missing(self):
        result = self.run_verify(live(**{BASE + "/llms.txt": resp("", status=404)}))
        self.assertEqual(self.status_of(result, "llms.status"), "fail")


# ─────────────────────────────────────────────────────────── JSON-LD

class TestJsonLd(DeployBase):

    def test_visible_text_match(self):
        self.assertEqual(self.status_of(self.run_verify(), "jsonld.visible"), "pass")

    def test_faq_answer_not_on_screen_fails(self):
        served = html(title="자주 묻는 질문", body="배송은 얼마나 걸리나요?",
                      head=ld_script(FAQ_LD), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        check = self.check_of(result, "jsonld.visible")
        self.assertEqual(check["status"], "fail")
        self.assertIn("스팸", check["message"])
        self.assertTrue(any("answer" in m for m in check["evidence"]["pages"][0]["missing"]))

    def test_ld_not_inserted(self):
        result = self.run_verify(live(**{FAQ: resp(html(title="자주 묻는 질문"))}))
        self.assertEqual(self.status_of(result, "jsonld.present"), "fail")

    def test_wrong_type(self):
        other = dict(FAQ_LD, **{"@type": "WebPage"})
        served = html(title="자주 묻는 질문",
                      body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다.",
                      head=ld_script(other), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        self.assertEqual(self.status_of(result, "jsonld.type"), "fail")

    def test_organization_id_split(self):
        other_org = dict(ORG_LD, **{"@id": FAQ + "#organization"})
        served = html(title="자주 묻는 질문",
                      body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다. 예시 주식회사",
                      head=ld_script(FAQ_LD) + ld_script(other_org), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        self.assertEqual(self.status_of(result, "jsonld.org_id"), "fail")

    def test_product_price_with_thousand_separator_passes(self):
        product = {"@context": "https://schema.org", "@type": "Product",
                   "name": "표준 요금제", "url": FAQ,
                   "offers": {"@type": "Offer", "price": "89000", "priceCurrency": "KRW"}}
        os.remove(os.path.join(self.dir, "jsonld", "faq.faq.json"))
        self.write("jsonld/faq.product.json", json.dumps(product, ensure_ascii=False))
        served = html(title="요금", body="표준 요금제 89,000원",
                      head=ld_script(product), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        self.assertEqual(self.status_of(result, "jsonld.visible"), "pass")


# ─────────────────────────────────────────────────────────── noindex 사고

class TestNoindexRegression(DeployBase):

    def test_new_noindex_fails_everything(self):
        served = FAQ_HTML.replace("<head>", '<head><meta name="robots" content="noindex">')
        result = self.run_verify(live(**{FAQ: resp(served)}))
        check = result["checks"][0]
        self.assertEqual(check["id"], "noindex")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["evidence"]["new"], [FAQ])
        self.assertEqual(result["exit_code"], 1)

    def test_x_robots_tag_header_counts(self):
        result = self.run_verify(
            live(**{HOME: resp(HOME_HTML, headers={"x-robots-tag": "noindex, nofollow"})}))
        self.assertEqual(result["checks"][0]["status"], "fail")


# ─────────────────────────────────────────────────────────── meta

class TestMeta(DeployBase):

    def rows(self, **kw):
        row = {"url": FAQ, "current_title": "옛 제목", "draft_title": "자주 묻는 질문 | 예시 주식회사",
               "current_description": "", "draft_description": ""}
        row.update(kw)
        self.write("meta-draft.json", json.dumps([row], ensure_ascii=False))

    def test_applied(self):
        self.rows()
        check = self.check_of(self.run_verify(), "meta.applied")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["evidence"]["draft_applied"], 1)

    def test_description_change_counted(self):
        self.rows(current_description="옛 설명")
        check = self.check_of(self.run_verify(), "meta.applied")
        self.assertEqual(check["evidence"]["description_changed"], 1)

    def test_already_matching_draft_passes(self):
        self.rows(current_title="자주 묻는 질문 | 예시 주식회사")
        self.assertEqual(self.status_of(self.run_verify(), "meta.applied"), "pass")

    def test_duplicate_titles_remaining(self):
        self.rows()
        self.write("meta-draft.json", json.dumps(
            [{"url": HOME, "current_title": "옛", "draft_title": "새"},
             {"url": FAQ, "current_title": "옛", "draft_title": "새"}], ensure_ascii=False))
        served = html(title="예시 주식회사", body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다.",
                      head=ld_script(FAQ_LD), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        self.assertEqual(self.status_of(result, "meta.duplicate"), "fail")

    def test_skipped_when_absent(self):
        self.assertEqual(self.status_of(self.run_verify(), "meta.applied"), "skip")


# ─────────────────────────────────────────────────────────── 출력

class TestOutput(DeployBase):

    def test_markdown_puts_failures_first(self):
        result = self.run_verify(live(**{BASE + "/llms.txt": resp(LLMS_TODO, ctype="text/plain")}))
        md = verify.render_md(result)
        self.assertLess(md.index("❌ 실패"), md.index("✅ 통과"))
        self.assertIn("다음 조치", md)

    def test_summary_counts_match(self):
        result = self.run_verify()
        self.assertEqual(sum(result["summary"].values()), len(result["checks"]))


# ─────────────────────────────────────────────────────────── B. diff 모드

def finding(code, lane="SEO", severity="critical", urls=1):
    return {"lane": lane, "severity": severity, "code": code, "message": code,
            "urls": ["%s/%d" % (BASE, n) for n in range(urls)], "data": {}}


class TestDiff(unittest.TestCase):

    def setUp(self):
        self.before = audit(
            findings=[finding("NOINDEX"), finding("TITLE_DUPLICATE", severity="warn", urls=3)],
            scorecard={"SEO": {"status": "bad", "evidence": []},
                       "AEO": {"status": "ok", "evidence": []},
                       "GEO": {"status": "ok", "evidence": []},
                       "LLMO": {"status": "ok", "evidence": []},
                       "NEO": {"status": "ok", "evidence": []},
                       "reputation": {"status": "na", "evidence": []}})
        self.after = audit(
            generated_at="2026-09-15T00:00:00+00:00",
            findings=[finding("TITLE_DUPLICATE", severity="warn", urls=1),
                      finding("SITEMAP_MISSING", lane="SEO", severity="warn")],
            scorecard={"SEO": {"status": "warn", "evidence": []},
                       "AEO": {"status": "bad", "evidence": []},
                       "GEO": {"status": "ok", "evidence": []},
                       "LLMO": {"status": "ok", "evidence": []},
                       "NEO": {"status": "ok", "evidence": []},
                       "reputation": {"status": "na", "evidence": []}})

    def codes(self, result, cid, key="items"):
        check = [c for c in result["checks"] if c["id"] == cid][0]
        return check, [i["code"] for i in check["evidence"][key]]

    def test_resolved_new_persisting(self):
        result = verify.verify_diff(self.before, self.after)
        self.assertEqual(result["mode"], "diff")
        _, resolved = self.codes(result, "diff.resolved")
        self.assertEqual(resolved, ["NOINDEX"])
        _, new = self.codes(result, "diff.new")
        self.assertEqual(new, ["SITEMAP_MISSING"])
        check, persisting = self.codes(result, "diff.persisting")
        self.assertEqual(persisting, ["TITLE_DUPLICATE"])
        self.assertEqual(check["evidence"]["items"][0]["urls_before"], 3)
        self.assertEqual(check["evidence"]["items"][0]["urls_after"], 1)

    def test_new_critical_is_fail(self):
        self.after["findings"].append(finding("NEW_ACCIDENT"))
        result = verify.verify_diff(self.before, self.after)
        self.assertEqual([c["status"] for c in result["checks"] if c["id"] == "diff.new"], ["fail"])
        self.assertEqual(result["exit_code"], 1)

    def test_scorecard_before_after(self):
        result = verify.verify_diff(self.before, self.after)
        check = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(check["evidence"]["lanes"]["SEO"], {"before": "bad", "after": "warn"})
        self.assertEqual(check["status"], "fail")  # AEO ok→bad
        self.assertIn("AEO ok→bad", check["message"])

    def test_scorecard_only_improved_passes(self):
        self.after["scorecard"]["AEO"] = {"status": "ok", "evidence": []}
        result = verify.verify_diff(self.before, self.after)
        check = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(check["status"], "pass")

    def test_stats_before_after(self):
        self.after["stats"]["unique_titles"] = 7
        result = verify.verify_diff(self.before, self.after)
        check = [c for c in result["checks"] if c["id"] == "diff.stats"][0]
        self.assertEqual(check["evidence"]["stats"]["unique_titles"],
                         {"before": 2, "after": 7})

    def test_page_level_gone_and_new(self):
        self.after["pages"] = [page(HOME), page(BASE + "/new")]
        result = verify.verify_diff(self.before, self.after)
        check = [c for c in result["checks"] if c["id"] == "diff.pages"][0]
        self.assertEqual(check["evidence"]["gone"], [FAQ])
        self.assertEqual(check["evidence"]["new"], [BASE + "/new"])
        self.assertEqual(check["status"], "warn")

    def test_no_network_in_diff_mode(self):
        original = verify.crawl.fetch
        verify.crawl.fetch = lambda url: self.fail("diff 모드는 네트워크를 쓰면 안 된다")
        try:
            verify.verify_diff(self.before, self.after)
        finally:
            verify.crawl.fetch = original


if __name__ == "__main__":
    unittest.main()
