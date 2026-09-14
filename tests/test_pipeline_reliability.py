# -*- coding: utf-8 -*-
"""생성→배포 검증에서 조용한 삭제와 거짓 통과를 막는 통합 회귀 테스트."""

import json
import os
import shutil
import sys
import tempfile
import unittest

# `tests` 패키지는 저장소 루트가, 도구는 tools/ 가 경로에 있어야 import 된다.
# 다른 모듈이 먼저 경로를 넣어주길 기대하지 않는다 — 이 파일 하나만 돌려도 동작해야 한다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

from tests import test_generate as genfix
from tests import test_verify as verfix

import generate
import verify


class GenerateReliability(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="su-pipeline-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_incomplete_crawl_never_emits_replacement_sitemap(self):
        report = genfix.audit(coverage={"complete": False, "max_pages": 300,
                              "pages_fetched": 300, "queued_remaining": 6,
                              "blocked_count": 0, "reasons": ["max_pages_reached"]})
        ctx = generate.run("all", report, genfix.SITE, self.out)
        self.assertFalse(any(name.endswith(".xml") for name in ctx.files))
        self.assertFalse(os.path.exists(os.path.join(self.out, "sitemap.xml")))
        with open(os.path.join(self.out, "DEPLOY.md"), encoding="utf-8") as fh:
            self.assertIn("교체 금지", fh.read())

    def test_legacy_known_sitemap_loss_is_blocked(self):
        report = genfix.audit()
        report["site"]["sitemap_urls"] = ["https://example.com/", "https://example.com/orphan"]
        ctx = generate.run("sitemap", report, {}, self.out)
        self.assertEqual(ctx.notes["sitemap_safety"]["missing"],
                         ["https://example.com/orphan"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sitemap.xml")))

    def test_declared_sitemap_failure_blocks_even_false_complete_legacy_audit(self):
        report = genfix.audit(coverage={"complete": True, "reasons": []})
        sitemap = "https://example.com/list.xml"
        report["site"]["robots"]["sitemap_declared"] = [sitemap]
        report["site"]["sitemaps"] = [{"url": sitemap, "status": 503,
                                         "parsed": False, "error": None,
                                         "is_index": False, "url_count": 0}]

        ctx = generate.run("sitemap", report, {}, self.out)

        self.assertTrue(ctx.notes["sitemap_blocked"])
        self.assertIn("sitemap 확인 실패", ctx.notes["sitemap_safety"]["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sitemap.xml")))

    def test_optional_default_sitemap_404_does_not_block_generation(self):
        report = genfix.audit(coverage={"complete": True, "reasons": []})
        report["site"]["robots"]["sitemap_declared"] = []
        report["site"]["sitemaps"] = [{"url": "https://example.com/sitemap.xml",
                                         "status": 404, "parsed": False, "error": None,
                                         "is_index": False, "url_count": 0}]

        generate.run("sitemap", report, {}, self.out)

        self.assertTrue(os.path.exists(os.path.join(self.out, "sitemap.xml")))

    def test_required_index_child_failure_blocks_generation(self):
        report = genfix.audit(coverage={"complete": True, "reasons": []})
        index = "https://example.com/sitemap-index.xml"
        child = "https://example.com/child.xml"
        report["site"]["robots"]["sitemap_declared"] = [index]
        report["site"]["sitemaps"] = [
            {"url": index, "status": 200, "parsed": True, "error": None,
             "required": True, "is_index": True, "url_count": 1},
            {"url": child, "status": 503, "parsed": False, "error": None,
             "required": True, "is_index": False, "url_count": 0},
        ]

        ctx = generate.run("sitemap", report, {}, self.out)

        self.assertTrue(ctx.notes["sitemap_blocked"])
        self.assertIn(child, ctx.notes["sitemap_safety"]["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sitemap.xml")))

    def test_legacy_index_child_without_provenance_also_blocks_generation(self):
        report = genfix.audit(coverage={"complete": True, "reasons": []})
        report["site"]["robots"]["sitemap_declared"] = ["https://example.com/sitemap.xml"]
        report["site"]["sitemaps"] = [
            {"url": "https://example.com/sitemap.xml", "status": 200, "parsed": True,
             "is_index": True, "url_count": 1},
            {"url": "https://example.com/products.xml", "status": 503,
             "parsed": False, "is_index": False, "url_count": 0},
        ]
        ctx = generate.run("sitemap", report, {}, self.out)
        self.assertTrue(ctx.notes["sitemap_blocked"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "sitemap.xml")))

    def test_redirect_source_is_not_put_in_sitemap(self):
        report = genfix.audit(pages=[genfix.page("https://example.com/old",
                                      final_url="https://example.com/new", canonical=None),
                                     genfix.page("https://example.com/new")],
                              coverage={"complete": True, "max_pages": 10,
                                        "pages_fetched": 2, "queued_remaining": 0,
                                        "blocked_count": 0, "reasons": []})
        generate.run("sitemap", report, {}, self.out)
        with open(os.path.join(self.out, "sitemap.xml"), encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn("/old</loc>", raw)
        self.assertIn("/new</loc>", raw)

    def test_colliding_readable_slugs_have_unique_files_and_manifest(self):
        report = genfix.audit(pages=[genfix.page("https://example.com/a/b"),
                                     genfix.page("https://example.com/a-b")])
        site = dict(genfix.SITE)
        site["products"] = [
            {"page_url": "https://example.com/a/b", "name": "첫 상품", "offers": {}},
            {"page_url": "https://example.com/a-b", "name": "둘째 상품", "offers": {}},
        ]
        site["faqs"] = []
        generate.run("jsonld", report, site, self.out)
        with open(os.path.join(self.out, generate.JSONLD_MANIFEST), encoding="utf-8") as fh:
            manifest = json.load(fh)
        product_files = [name for name in manifest["files"] if name.endswith(".product.json")]
        self.assertEqual(len(product_files), 2)
        self.assertEqual(len(set(product_files)), 2)
        self.assertEqual(set(manifest["files"][name] for name in product_files),
                         {"https://example.com/a/b", "https://example.com/a-b"})

    def test_snippet_escapes_html_raw_text_terminator(self):
        raw = generate.snippet([{"@context": "https://schema.org", "@type": "Thing",
                                 "name": "</script><img src=x>&"}])
        payload = raw.split(">", 1)[1].split("</script>", 1)[0]
        self.assertNotIn("</script>", payload.lower())
        self.assertIn("\\u003c/script\\u003e", payload)
        self.assertEqual(json.loads(payload)["name"], "</script><img src=x>&")

    def test_rerun_removes_only_previously_owned_stale_files(self):
        generate.run("jsonld", genfix.audit(), genfix.SITE, self.out)
        stale = [name for name in os.listdir(os.path.join(self.out, "jsonld"))
                 if name.endswith(".product.json")]
        self.assertTrue(stale)
        with open(os.path.join(self.out, "user-note.txt"), "w", encoding="utf-8") as fh:
            fh.write("keep")
        generate.run("jsonld", genfix.audit(), {}, self.out)
        self.assertFalse(any(os.path.exists(os.path.join(self.out, "jsonld", name))
                             for name in stale))
        self.assertTrue(os.path.exists(os.path.join(self.out, "user-note.txt")))


class VerifyReliability(verfix.DeployBase):
    def test_same_type_with_different_faq_object_fails_identity(self):
        other = {"@context": "https://schema.org", "@type": "FAQPage",
                 "mainEntity": [{"@type": "Question", "name": "다른 질문",
                                 "acceptedAnswer": {"@type": "Answer", "text": "다른 답"}}]}
        served = verfix.html(title="FAQ",
                             body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다. 다른 질문 다른 답",
                             head=verfix.ld_script(other), canonical=verfix.FAQ)
        result = self.run_verify(verfix.live(**{verfix.FAQ: verfix.resp(served)}))
        check = self.check_of(result, "jsonld.type")
        self.assertEqual(check["status"], "fail")
        self.assertTrue(any("핵심 필드" in item["reason"] for item in check["evidence"]["items"]))

    def test_unrelated_meta_change_is_not_draft_application(self):
        rows = [{"url": verfix.FAQ, "current_title": "옛 제목", "draft_title": "원하는 제목",
                 "current_description": "", "draft_description": ""}]
        self.write("meta-draft.json", json.dumps(rows, ensure_ascii=False))
        served = verfix.html(title="CMS가 바꾼 다른 제목",
                             body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다.",
                             head=verfix.ld_script(verfix.FAQ_LD), canonical=verfix.FAQ)
        result = self.run_verify(verfix.live(**{verfix.FAQ: verfix.resp(served)}))
        self.assertEqual(self.status_of(result, "meta.applied"), "fail")

    def test_partial_exact_meta_application_is_a_failure(self):
        rows = [
            {"url": verfix.HOME, "current_title": "옛 홈", "draft_title": "예시 주식회사",
             "current_description": "", "draft_description": ""},
            {"url": verfix.FAQ, "current_title": "옛 FAQ", "draft_title": "다른 FAQ 제목",
             "current_description": "", "draft_description": ""},
        ]
        self.write("meta-draft.json", json.dumps(rows, ensure_ascii=False))
        result = self.run_verify()
        check = self.check_of(result, "meta.applied")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["evidence"]["matched_fields"], 1)

    def test_broken_meta_draft_is_a_failure(self):
        self.write("meta-draft.json", "[")
        result = self.run_verify()
        self.assertEqual(self.status_of(result, "meta.applied"), "fail")

    def test_zero_indexable_pages_checked_cannot_pass(self):
        report = verfix.audit(pages=[])
        result = self.run_verify(audit_report=report)
        check = self.check_of(result, "noindex")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["evidence"]["pages_checked"], 0)

    def test_manifest_mapping_must_be_same_host_and_complete(self):
        self.write("jsonld/manifest.json", json.dumps({
            "schema": verify.JSONLD_MANIFEST_SCHEMAS[0],
            "files": {"faq.faq.json": "https://evil.example/faq"}}))
        result = self.run_verify()
        self.assertEqual(self.status_of(result, "jsonld.mapping"), "fail")

    def test_malformed_manifest_cannot_fall_back_to_slug_guess(self):
        self.write("jsonld/manifest.json", "{}")
        result = self.run_verify()
        self.assertEqual(self.status_of(result, "jsonld.mapping"), "fail")

    def test_sitemap_index_checks_child_after_old_twelve_file_boundary(self):
        index = verfix.BASE + "/sitemap-index.xml"
        children = [verfix.BASE + "/child-%02d.xml" % i for i in range(12)]
        index_xml = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
                     ''.join("<sitemap><loc>%s</loc></sitemap>" % u for u in children) +
                     '</sitemapindex>')
        empty = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        responses = {verfix.BASE + "/robots.txt": verfix.resp(
            "User-agent: *\nAllow: /\nSitemap: %s\n" % index, ctype="text/plain"),
                     index: verfix.resp(index_xml, ctype="application/xml")}
        responses.update({u: verfix.resp(empty, ctype="application/xml") for u in children[:-1]})
        responses[children[-1]] = verfix.resp("", status=404, ctype="application/xml")
        result = self.run_verify(verfix.live(**responses))
        check = self.check_of(result, "sitemap.reachable")
        self.assertEqual(check["status"], "fail")
        self.assertTrue(any(x["url"] == children[-1] for x in check["evidence"]["sitemaps"]))

    def test_unverified_cap_has_exit_two_and_completion_reason(self):
        result = self.run_verify(max_urls=1)
        self.assertEqual(result["summary"]["fail"], 0)
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["completion"]["complete"])
        self.assertTrue(result["completion"]["reasons"])

    def test_sitemap_fetch_error_fails_even_when_xml_parses(self):
        bad = verfix.resp(verfix.SITEMAP, ctype="application/xml")
        bad["error"] = "truncated"
        result = self.run_verify(verfix.live(**{verfix.BASE + "/sitemap.xml": bad}))
        self.assertEqual(self.status_of(result, "sitemap.reachable"), "fail")

    def test_sitemap_redirect_without_canonical_is_still_a_mismatch(self):
        body = verfix.html(title="FAQ", body="배송은 얼마나 걸리나요? 주문 다음 영업일에 출고된다.",
                           head=verfix.ld_script(verfix.FAQ_LD), canonical=None)
        redirected = verfix.resp(body, final_url=verfix.BASE + "/new-faq")
        result = self.run_verify(verfix.live(**{verfix.FAQ: redirected}))
        check = self.check_of(result, "sitemap.canonical")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["evidence"]["mismatch"][0]["final_url"],
                         verfix.BASE + "/new-faq")


if __name__ == "__main__":
    unittest.main()
