# -*- coding: utf-8 -*-
"""verify.py 회귀 테스트 — 네트워크를 쓰지 않는다 (fetch를 가짜로 주입).

실행: python -m unittest discover tests
"""

import contextlib
import io
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
        # 받지 못한 파일의 형식은 판정하지 않는다 — 미측정이지 "형식 문제 없음"이 아니다
        self.assertIsNone(self.status_of(result, "llms.format"))

    def test_format_clean_is_a_reference_pass(self):
        check = self.check_of(self.run_verify(), "llms.format")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["evidence"]["notes"], [])
        self.assertIn("순위·인용 요인이 아니다", check["message"])

    def test_format_notes_warn_without_changing_exit_code(self):
        clean = self.run_verify()
        body = LLMS_CLEAN + "\n## 기타\n- [소개](/about)\n- [띄어쓴 괄호] (https://example.com/x)\n"
        result = self.run_verify(live(**{BASE + "/llms.txt": resp(body, ctype="text/plain")}))
        check = self.check_of(result, "llms.format")
        self.assertEqual(check["status"], "warn")
        self.assertEqual([n["code"] for n in check["evidence"]["notes"]],
                         ["relative_links", "malformed_links"])
        self.assertIn("순위·인용 요인이 아니다", check["message"])
        self.assertEqual(result["exit_code"], clean["exit_code"])
        self.assertEqual(result["completion"], clean["completion"])
        self.assertIn("Google 검색에는 필요 없고", verify.render_md(result))

    def test_html_fallback_is_noted(self):
        body = "<!doctype html><html><head><title>앱</title></head><body>앱</body></html>"
        result = self.run_verify(live(**{BASE + "/llms.txt": resp(body)}))
        self.assertEqual(self.status_of(result, "llms.status"), "pass")
        check = self.check_of(result, "llms.format")
        self.assertEqual(check["status"], "warn")
        self.assertEqual([n["code"] for n in check["evidence"]["notes"]], ["html_document"])

    def test_unreadable_body_is_skipped_not_passed(self):
        broken = resp("", ctype="text/plain")
        broken["error"] = "invalid_gzip"
        result = self.run_verify(live(**{BASE + "/llms.txt": broken}))
        self.assertEqual(self.status_of(result, "llms.format"), "skip")


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


# ─────────────────────────────────────────────────────────── 필드 단위 · 평점 · 사실 원장

def product_ld(price="89000", **extra):
    obj = {"@context": "https://schema.org", "@type": "Product", "name": "표준 요금제",
           "url": FAQ, "offers": {"@type": "Offer", "price": price, "priceCurrency": "KRW"}}
    obj.update(extra)
    return obj


class ProductBase(DeployBase):
    """FAQ 자리에 Product LD 한 건을 배포한 사이트."""

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.dir, "jsonld", "faq.faq.json"))
        self.package = product_ld()
        self.write("jsonld/faq.product.json", json.dumps(self.package, ensure_ascii=False))

    def serve(self, ld, body="표준 요금제 89,000원"):
        blocks = ld if isinstance(ld, list) else [ld]
        served = html(title="요금", body=body,
                      head="".join(ld_script(x) for x in blocks), canonical=FAQ)
        return live(**{FAQ: resp(served)})


class TestFieldLevel(ProductBase):

    def test_price_mismatch_reports_pointer_values_and_visibility(self):
        result = self.run_verify(self.serve(product_ld(price="99000"), body="표준 요금제 99,000원"))
        check = self.check_of(result, "jsonld.type")
        self.assertEqual(check["status"], "fail")  # 기존 판정 그대로
        item = check["evidence"]["items"][0]
        self.assertIn("핵심 필드", item["reason"])  # 기존 키 유지
        fields = {f["path"]: f for f in item["fields"]}
        self.assertEqual(list(fields), ["/offers/price"])
        price = fields["/offers/price"]
        self.assertEqual((price["node"], price["package"], price["live"]),
                         ("Product", "89000", "99000"))
        self.assertTrue(price["live_present"])
        self.assertFalse(price["visible"])
        self.assertTrue(price["live_visible"])

    def test_missing_live_field_is_reported_as_absent(self):
        live_obj = product_ld()
        del live_obj["offers"]
        result = self.run_verify(self.serve(live_obj))
        fields = self.check_of(result, "jsonld.type")["evidence"]["items"][0]["fields"]
        price = [f for f in fields if f["path"] == "/offers/price"][0]
        self.assertFalse(price["live_present"])
        self.assertIsNone(price["live"])
        self.assertTrue(price["visible"])  # 89,000은 화면에 있다

    def test_number_and_string_price_are_the_same_fact(self):
        result = self.run_verify(self.serve(product_ld(price=89000)))
        self.assertEqual(self.status_of(result, "jsonld.type"), "pass")
        self.assertNotIn("field_drift", self.check_of(result, "jsonld.type")["evidence"])

    def test_non_identity_drift_is_evidence_only(self):
        self.package = product_ld(description="패키지 설명")
        self.write("jsonld/faq.product.json", json.dumps(self.package, ensure_ascii=False))
        result = self.run_verify(self.serve(product_ld(description="다른 설명")))
        check = self.check_of(result, "jsonld.type")
        self.assertEqual(check["status"], "pass")
        drift = check["evidence"]["field_drift"]
        self.assertEqual([(d["path"], d["package"], d["live"]) for d in drift],
                         [("/description", "패키지 설명", "다른 설명")])

    def test_invisible_field_has_pointer(self):
        result = self.run_verify(self.serve(product_ld(), body="표준 요금제"))
        check = self.check_of(result, "jsonld.visible")
        self.assertEqual(check["status"], "fail")
        page_item = check["evidence"]["pages"][0]
        self.assertEqual(page_item["missing"], ["price: 89000"])  # 기존 형식 유지
        self.assertEqual([(f["path"], f["package"], f["live"], f["visible"])
                          for f in page_item["fields"]],
                         [("/offers/price", "89000", "89000", False)])

    def test_faq_answer_pointer(self):
        os.remove(os.path.join(self.dir, "jsonld", "faq.product.json"))
        self.write("jsonld/faq.faq.json", json.dumps(FAQ_LD, ensure_ascii=False))
        served = html(title="자주 묻는 질문", body="배송은 얼마나 걸리나요?",
                      head=ld_script(FAQ_LD), canonical=FAQ)
        result = self.run_verify(live(**{FAQ: resp(served)}))
        fields = self.check_of(result, "jsonld.visible")["evidence"]["pages"][0]["fields"]
        self.assertEqual([f["path"] for f in fields], ["/mainEntity/0/acceptedAnswer/text"])
        self.assertEqual(fields[0]["node"], "FAQPage")


class TestPointerAndValueHelpers(unittest.TestCase):

    def test_resolve_pointer_rfc6901(self):
        obj = {"a/b": {"m~n": [10, 20]}}
        self.assertEqual(verify.resolve_pointer(obj, "/a~1b/m~0n/1"), 20)
        self.assertIs(verify.resolve_pointer(obj, "/a~1b/m~0n/01"), verify._MISSING)
        self.assertIs(verify.resolve_pointer(obj, "/a~1b/m~0n/2"), verify._MISSING)

    def test_identifiers_are_not_compared_as_numbers(self):
        self.assertFalse(verify.same_value("0123456", "123456", "gtin13"))
        self.assertTrue(verify.same_value(89000, "89000.0", "price"))

    def test_number_visibility_respects_digit_boundaries(self):
        self.assertTrue(verify.value_visible("리뷰 1,204건", 1204, "reviewCount"))
        self.assertFalse(verify.value_visible("2024년 출시", 202, "reviewCount"))
        self.assertFalse(verify.value_visible("평점 4.8점", 4, "ratingValue"))
        self.assertTrue(verify.value_visible("평점 4.8점", "4.80", "ratingValue"))
        self.assertIsNone(verify.value_visible("아무 글", FAQ, "url"))

    def test_zero_is_not_the_empty_string(self):
        """0과 ""(빈 값)는 다른 사실이다 — 수 키든 아니든 같은 값으로 접지 않는다."""
        self.assertEqual(verify.norm_text(0), "0")
        self.assertEqual(verify.norm_text(None), "")
        self.assertFalse(verify.same_value(0, "", "price"))
        self.assertFalse(verify.same_value("", 0, "price"))
        self.assertFalse(verify.same_value(0, "", "name"))
        self.assertFalse(verify.same_value(0, None, "price"))
        self.assertTrue(verify.same_value(0, "0", "price"))
        self.assertTrue(verify.same_value(0, "0.00", "price"))
        self.assertTrue(verify.value_visible("무료 0원", 0, "price"))

    def test_property_value_identifier_keeps_leading_zeros(self):
        pkg = {"@type": "Product", "name": "x",
               "identifier": {"@type": "PropertyValue", "propertyID": "사내코드", "value": "007"}}
        live_obj = json.loads(json.dumps(pkg))
        live_obj["identifier"]["value"] = "7"
        diffs = verify.ld_field_diffs(pkg, live_obj, "")
        self.assertEqual([d["path"] for d in diffs], ["/identifier/value"])
        self.assertFalse(verify.same_value("007", "7", "value", ["PropertyValue"]))
        self.assertFalse(verify.same_value("007", 7, "value", ["PropertyValue"]))
        self.assertFalse(verify.same_value("007", "7", "value"))

    def test_quantitative_value_is_still_numeric(self):
        pkg = {"@type": "Product", "weight": {"@type": "QuantitativeValue", "value": "1.50",
                                              "unitCode": "KGM"},
               "offers": {"@type": "Offer", "priceSpecification": {
                   "@type": "MonetaryAmount", "value": 89000, "currency": "KRW"}}}
        live_obj = json.loads(json.dumps(pkg))
        live_obj["weight"]["value"] = 1.5
        live_obj["offers"]["priceSpecification"]["value"] = "89000.0"
        self.assertEqual(verify.ld_field_diffs(pkg, live_obj, ""), [])
        self.assertTrue(verify.same_value("1.50", "1.5", "value", ["QuantitativeValue"]))
        self.assertEqual(verify.container_types(pkg, "/weight/value"), ["QuantitativeValue"])


def rated(rating, **product):
    obj = product_ld(**product)
    obj["aggregateRating"] = dict({"@type": "AggregateRating"}, **rating)
    return obj


class TestRatingIntegrity(ProductBase):

    def deploy_ld(self, obj):
        self.write("jsonld/faq.product.json", json.dumps(obj, ensure_ascii=False))
        return self.run_verify(self.serve(obj))

    def test_no_rating_nodes_no_check(self):
        result = self.run_verify(self.serve(self.package))
        self.assertIsNone(self.status_of(result, "jsonld.rating_scale"))
        self.assertIsNone(self.status_of(result, "jsonld.rating_count"))

    def test_valid_rating_passes(self):
        result = self.deploy_ld(rated({"ratingValue": "4.5", "ratingCount": "12",
                                       "reviewCount": 7}))
        self.assertEqual(self.status_of(result, "jsonld.rating_scale"), "pass")
        self.assertEqual(self.status_of(result, "jsonld.rating_count"), "pass")
        self.assertEqual(result["exit_code"], 0, result["checks"])

    def test_rating_above_declared_best_fails(self):
        result = self.deploy_ld(rated({"ratingValue": 4.8, "bestRating": 4}))
        check = self.check_of(result, "jsonld.rating_scale")
        self.assertEqual(check["status"], "fail")
        self.assertIn("척도", check["message"])
        sources = {i["source"] for i in check["evidence"]["items"]}
        self.assertEqual(sources, {"package", "live"})
        live_item = [i for i in check["evidence"]["items"] if i["source"] == "live"][0]
        self.assertEqual((live_item["url"], live_item["path"], live_item["node"]),
                         (FAQ, "/aggregateRating", "AggregateRating"))
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("척도", verify.NEXT["jsonld.rating_scale"])

    def test_default_scale_is_one_to_five(self):
        result = self.deploy_ld(rated({"ratingValue": "0.5"}))
        self.assertEqual(self.status_of(result, "jsonld.rating_scale"), "fail")

    def test_percentage_and_fraction_rating_values_are_accepted(self):
        """Google 리뷰 스니펫 문서는 4·"60%"·"6 / 10"을 모두 받는다 — 척도는 형식이 함축한다."""
        def issues(**node):
            return verify.rating_issues(dict({"@type": "AggregateRating"}, **node))[1]
        for value in ("60%", " 60 % ", "0%", "100%", "6 / 10", "6/10", "8.5/10"):
            self.assertEqual(issues(ratingValue=value), [], value)
        # 선언한 척도는 함축보다 우선한다
        self.assertEqual(issues(ratingValue="6 / 10", bestRating=10), [])
        self.assertEqual(issues(ratingValue="60%", worstRating=0, bestRating=100), [])
        for value, needle in (("110%", "척도"), ("11 / 10", "척도"), ("-5%", "척도")):
            self.assertIn(needle, issues(ratingValue=value)[0]["reason"], value)
        # 분모와 다른 bestRating은 모순이다
        clash = issues(ratingValue="6 / 10", bestRating=5)
        self.assertIn("분모", clash[0]["reason"])
        # 해석할 수 없는 글자는 그대로 실패
        for value in ("열", "6/", "/10", "60 percent", "6 of 10"):
            self.assertIn("숫자가 아니다", issues(ratingValue=value)[0]["reason"], value)

    def test_percentage_rating_passes_end_to_end(self):
        result = self.deploy_ld(rated({"ratingValue": "60%", "ratingCount": 12}))
        self.assertEqual(self.status_of(result, "jsonld.rating_scale"), "pass")

    def test_nested_in_graph_with_type_list(self):
        graph = {"@context": "https://schema.org", "@graph": [
            {"@type": ["LocalBusiness", "Store"], "name": "예시 매장",
             "review": [{"@type": "Review",
                         "reviewRating": {"@type": "Rating", "ratingValue": "열",
                                          "bestRating": "10"}}]}]}
        total, scale, _counts = verify.rating_issues(graph)
        self.assertEqual(total, 1)
        self.assertEqual(scale[0]["path"], "/@graph/0/review/0/reviewRating")
        self.assertIn("숫자가 아니다", scale[0]["reason"])

    def test_inverted_scale_fails(self):
        _t, scale, _c = verify.rating_issues(
            {"@type": "AggregateRating", "ratingValue": 3, "worstRating": 5, "bestRating": 1})
        self.assertIn("작지 않다", scale[0]["reason"])

    def test_non_integer_counts_fail(self):
        result = self.deploy_ld(rated({"ratingValue": 4, "ratingCount": "12.5",
                                       "reviewCount": "-1"}))
        check = self.check_of(result, "jsonld.rating_count")
        self.assertEqual(check["status"], "fail")
        fields = sorted({(i["field"], i["value"]) for i in check["evidence"]["items"]})
        self.assertEqual(fields, [("ratingCount", "12.5"), ("reviewCount", "-1")])
        self.assertEqual(self.status_of(result, "jsonld.rating_scale"), "pass")

    def test_existing_page_outside_deploy_targets_warns_not_fails(self):
        """배포 대상이 아닌 기존 페이지의 잘못된 평점은 원래 있던 마크업이다 — warn."""
        bad = rated({"ratingValue": 4.8, "bestRating": 4})

        class FakeSession:
            cache = {"https://example.com/old": {
                "status": 200, "html": "<html></html>", "url": "https://example.com/old",
                "ld_objs": [bad]}}

        checks = []
        verify.check_ratings(checks, FakeSession(), {"jsonld": {}},
                             target_urls=["https://example.com/deployed"])
        check = [c for c in checks if c["id"] == "jsonld.rating_scale"][0]
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["evidence"]["items"][0]["scope"], "existing")
        self.assertIn("기존 페이지", check["message"])

        checks = []
        verify.check_ratings(checks, FakeSession(), {"jsonld": {}},
                             target_urls=["https://example.com/old"])
        self.assertEqual([c["status"] for c in checks if c["id"] == "jsonld.rating_scale"],
                         ["fail"])

    def test_count_rules_unit(self):
        for good in (0, 12, "12", " 7 ", 3.0):
            _t, _s, counts = verify.rating_issues(
                {"@type": "AggregateRating", "ratingValue": 4, "ratingCount": good})
            self.assertEqual(counts, [], good)
        for bad in (-1, 12.5, "12.5", "-1", "", True, None, "1e2"):
            _t, _s, counts = verify.rating_issues(
                {"@type": "AggregateRating", "ratingValue": 4, "ratingCount": bad})
            self.assertEqual(len(counts), 1, bad)


def fact(**kw):
    base = {"url": FAQ, "pointer": "/offers/price", "expected": 89000,
            "evidence": "요금표 PDF 2쪽 (합성 예시)"}
    base.update(kw)
    return base


class TestFactsLedger(ProductBase):

    def verify_facts(self, facts, fetch=None):
        return self.run_verify(fetch or self.serve(self.package),
                               facts=verify.validate_facts(facts), facts_source="facts.json")

    def test_without_facts_no_key_and_no_check(self):
        result = self.run_verify(self.serve(self.package))
        self.assertNotIn("facts", result)
        self.assertIsNone(self.status_of(result, "facts.match"))

    def test_agree(self):
        result = self.verify_facts([fact(), fact(pointer="/name", expected="표준 요금제",
                                                 type="Product")])
        self.assertEqual(self.status_of(result, "facts.match"), "pass")
        self.assertEqual(self.status_of(result, "facts.visible"), "pass")
        ledger = result["facts"]
        self.assertEqual(ledger["schema"], verify.FACTS_SCHEMA)
        self.assertEqual(dict(ledger["summary"]),
                         {"agree": 2, "mismatch": 0, "missing": 0, "unverified": 0})
        first = ledger["results"][0]
        self.assertEqual(first["live_values"], [{"node": "Product", "value": "89000"}])
        self.assertTrue(first["visible"])
        self.assertTrue(first["package_claimed"])
        self.assertEqual(result["exit_code"], 0)

    def test_mismatch_fails(self):
        result = self.verify_facts([fact(expected=79000)])
        check = self.check_of(result, "facts.match")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(result["facts"]["results"][0]["status"], "mismatch")
        self.assertFalse(result["facts"]["results"][0]["visible"])
        self.assertEqual(result["exit_code"], 1)

    def test_expected_zero_against_empty_live_value_is_mismatch(self):
        result = self.verify_facts([fact(expected=0)], fetch=self.serve(product_ld(price="")))
        item = result["facts"]["results"][0]
        self.assertEqual((item["status"], item["outcome"]), ("mismatch", "fail"))
        self.assertEqual(result["exit_code"], 1)

    def test_identifier_value_with_leading_zeros_is_compared_as_text(self):
        live_obj = product_ld(identifier={"@type": "PropertyValue", "propertyID": "사내코드",
                                          "value": "7"})
        result = self.verify_facts([fact(pointer="/identifier/value", expected="007",
                                         type="Product")], fetch=self.serve(live_obj))
        item = result["facts"]["results"][0]
        self.assertEqual(item["status"], "mismatch")
        self.assertEqual(item["live_values"], [{"node": "Product", "value": "7"}])
        live_obj["identifier"]["value"] = "007"
        result = self.verify_facts([fact(pointer="/identifier/value", expected="007",
                                         type="Product")], fetch=self.serve(live_obj))
        self.assertEqual(result["facts"]["results"][0]["status"], "agree")

    def test_missing_unclaimed_is_unverified_not_pass_or_fail(self):
        result = self.verify_facts([fact(pointer="/aggregateRating/ratingValue", expected=4.5)])
        check = self.check_of(result, "facts.match")
        self.assertEqual(check["status"], "warn")
        item = result["facts"]["results"][0]
        self.assertEqual((item["status"], item["outcome"], item["package_claimed"]),
                         ("missing", "unverified", False))
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["completion"]["complete"])

    def test_missing_but_package_claimed_fails(self):
        live_obj = product_ld()
        del live_obj["offers"]
        result = self.verify_facts([fact()], fetch=self.serve(live_obj))
        item = result["facts"]["results"][0]
        self.assertEqual((item["status"], item["outcome"]), ("missing", "fail"))
        self.assertEqual(self.status_of(result, "facts.match"), "fail")

    def test_unfetched_page_is_never_verified(self):
        result = self.verify_facts([fact()], fetch=live(**{FAQ: resp("", status=503)}))
        item = result["facts"]["results"][0]
        self.assertEqual((item["status"], item["outcome"]), ("unverified", "unverified"))
        self.assertIsNone(item["visible"])
        self.assertEqual(result["facts"]["summary"]["agree"], 0)
        self.assertNotEqual(self.status_of(result, "facts.match"), "pass")

    def test_agree_but_not_on_screen_warns(self):
        result = self.verify_facts([fact()], fetch=self.serve(self.package, body="표준 요금제"))
        self.assertEqual(self.status_of(result, "facts.match"), "pass")
        self.assertEqual(self.status_of(result, "facts.visible"), "warn")

    def test_markdown_lists_non_agreeing_facts(self):
        result = self.verify_facts([fact(expected=79000)])
        md = verify.render_md(result)
        self.assertIn("## 사실 원장", md)
        self.assertIn("요금표 PDF 2쪽", md)


class TestFactsValidation(unittest.TestCase):

    def assertRejected(self, data, needle):
        with self.assertRaises(verify.FactsError) as ctx:
            verify.validate_facts(data)
        self.assertIn(needle, str(ctx.exception))

    def test_entry_without_evidence_rejected(self):
        bad = fact()
        del bad["evidence"]
        self.assertRejected([fact(), bad], "facts[1]")
        self.assertRejected([fact(evidence="   ")], "evidence")

    def test_entry_without_expected_rejected(self):
        bad = fact()
        del bad["expected"]
        self.assertRejected([bad], "expected")
        self.assertRejected([fact(expected="")], "expected")
        self.assertRejected([fact(expected=None)], "expected")

    def test_bad_pointer_url_and_shape_rejected(self):
        self.assertRejected([fact(pointer="offers/price")], "pointer")
        self.assertRejected([fact(url="example.com/faq")], "url")
        self.assertRejected([], "하나도")
        self.assertRejected({"schema": "other/1", "facts": [fact()]}, "스키마")

    def test_wrapped_form_accepted(self):
        facts = verify.validate_facts({"schema": verify.FACTS_SCHEMA, "facts": [fact()]})
        self.assertEqual(facts[0]["pointer"], "/offers/price")

    def test_cli_rejects_ledger_without_evidence(self):
        tmp = tempfile.mkdtemp(prefix="su-facts-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "deploy"))
        audit_path = os.path.join(tmp, "audit.json")
        facts_path = os.path.join(tmp, "facts.json")
        with open(audit_path, "w", encoding="utf-8") as fh:
            json.dump(audit(), fh, ensure_ascii=False)
        bad = fact()
        bad["evidence"] = ""
        with open(facts_path, "w", encoding="utf-8-sig") as fh:  # BOM이 있어도 읽는다
            json.dump([bad], fh, ensure_ascii=False)
        original = verify.crawl.fetch
        verify.crawl.fetch = lambda url: self.fail("원장이 잘못되면 네트워크 전에 멈춰야 한다")
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = verify.main(["deploy", audit_path, "--facts", facts_path])
        finally:
            verify.crawl.fetch = original
        self.assertEqual(code, 2)
        self.assertIn("evidence", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(tmp, "verify.json")))


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

    def robots_blind_pair(self):
        """before: robots를 읽고 GEO 차단을 판정. after: robots.txt 503 → GEO·NEO 미확인."""
        before = audit(findings=[finding("AI_CRAWLER_BLOCKED", lane="GEO"),
                                 finding("TITLE_DUPLICATE", severity="warn")])
        before["scorecard"]["GEO"] = {"status": "bad", "evidence": ["AI_CRAWLER_BLOCKED"]}
        before["scorecard"]["SEO"] = {"status": "warn", "evidence": ["TITLE_DUPLICATE"]}
        after = audit(generated_at="2026-09-15T00:00:00+00:00",
                      findings=[finding("ROBOTS_UNREACHABLE", severity="warn")])
        after["site"]["robots"] = {"status": 503, "present": False, "raw": "",
                                   "fetch_state": "unreachable",
                                   "policies": {ua: "unknown" for ua in crawl.ALL_UAS},
                                   "sitemap_declared": []}
        after["scorecard"]["SEO"] = {"status": "warn", "evidence": ["ROBOTS_UNREACHABLE"]}
        for lane in ("GEO", "NEO"):
            after["scorecard"][lane] = {"status": "na", "evidence": [],
                                        "unassessed": ["crawler_policy"]}
        return before, after

    def test_lane_lost_to_unreadable_robots_is_not_concluded(self):
        before, after = self.robots_blind_pair()
        result = verify.verify_diff(before, after)
        _, resolved = self.codes(result, "diff.resolved")
        self.assertEqual(resolved, ["TITLE_DUPLICATE"])  # 실제로 고친 것만 해소
        check, unverified = self.codes(result, "diff.unverified")
        self.assertEqual(unverified, ["AI_CRAWLER_BLOCKED"])
        self.assertEqual(check["status"], "warn")
        score = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(score["status"], "warn")
        self.assertIn("GEO bad→na", score["message"])
        self.assertIn("NEO ok→na", score["message"])
        self.assertFalse(result["completion"]["complete"])
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("결론 보류", verify.render_md(result))

    def test_lane_ok_to_na_without_findings_is_not_a_pass(self):
        self.after["scorecard"]["AEO"] = {"status": "ok", "evidence": []}
        self.after["scorecard"]["LLMO"] = {"status": "na", "evidence": []}
        result = verify.verify_diff(self.before, self.after)
        score = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(score["status"], "warn")
        self.assertIn("LLMO ok→na", score["message"])
        self.assertEqual(result["exit_code"], 2)

    def test_partly_unassessed_after_does_not_count_as_improvement(self):
        self.after["scorecard"]["AEO"] = {"status": "ok", "evidence": []}
        self.before["scorecard"]["GEO"] = {"status": "bad", "evidence": []}
        self.after["scorecard"]["GEO"] = {"status": "warn", "evidence": [],
                                          "unassessed": ["crawler_policy"]}
        result = verify.verify_diff(self.before, self.after)
        score = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(score["status"], "warn")
        self.assertIn("GEO bad→warn(일부 미확인)", score["message"])

    def test_newly_assessed_lane_is_not_a_regression(self):
        self.after["scorecard"]["AEO"] = {"status": "ok", "evidence": []}
        self.before["scorecard"]["GEO"] = {"status": "na", "evidence": [],
                                           "unassessed": ["crawler_policy"]}
        result = verify.verify_diff(self.before, self.after)
        score = [c for c in result["checks"] if c["id"] == "diff.scorecard"][0]
        self.assertEqual(score["status"], "pass")
        self.assertEqual(result["exit_code"], 0)

    def test_fixed_robots_findings_still_resolve_when_robots_was_read(self):
        before, after = self.robots_blind_pair()
        after["site"]["robots"] = dict(before["site"]["robots"], fetch_state="ok")
        after["scorecard"]["GEO"] = after["scorecard"]["NEO"] = {"status": "ok", "evidence": []}
        result = verify.verify_diff(before, after)
        _, resolved = self.codes(result, "diff.resolved")
        self.assertEqual(sorted(resolved), ["AI_CRAWLER_BLOCKED", "TITLE_DUPLICATE"])
        self.assertFalse([c for c in result["checks"] if c["id"] == "diff.unverified"])
        self.assertTrue(result["completion"]["complete"])

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
