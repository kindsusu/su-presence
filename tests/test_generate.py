# -*- coding: utf-8 -*-
"""generate.py 회귀 테스트 — 네트워크를 쓰지 않는다.

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
import generate  # noqa: E402


# ─────────────────────────────────────────────────────────── 픽스처

def page(url, **kw):
    base = {
        "url": url, "status": 200, "final_url": url, "title": "제목 | 예시 주식회사",
        "meta_description": None, "meta_robots": None, "x_robots_tag": None,
        "canonical": url, "h1": ["대표 제목"], "jsonld_count": 0, "jsonld_types": [],
        "text_chars": 900, "og": {}, "naver_site_verification": False, "lang": "ko",
        "response_ms": 10, "error": None,
    }
    base.update(kw)
    return base


ROBOTS_RAW = (
    "User-agent: *\n"
    "Disallow: /admin/\n"
    "\n"
    "User-agent: GPTBot\n"
    "Disallow: /\n"
    "\n"
    "Sitemap: https://example.com/sitemap.xml\n"
)


def audit(**kw):
    policies = {ua: "star-partial" for ua in crawl.ALL_UAS}
    policies["GPTBot"] = "explicit-block"
    base = {
        "schema": crawl.SCHEMA,
        "generated_at": "2026-09-03T00:00:00+00:00",
        "target": {"input": "example.com", "base": "https://example.com",
                   "host": "example.com"},
        "site": {
            "robots": {"status": 200, "present": True, "raw": ROBOTS_RAW,
                       "policies": policies,
                       "sitemap_declared": ["https://example.com/sitemap.xml"]},
            "sitemaps": [{"url": "https://example.com/sitemap.xml", "status": 200,
                          "is_index": False, "url_count": 1}],
            "sitemap_vs_crawl": {"only_in_sitemap": [],
                                 "only_in_crawl": ["https://example.com/pricing"]},
            "llms": {"llms.txt": 404, "llms-full.txt": 404},
            "hygiene": {"probe_404": 404, "redirect_hops": 0, "home_response_ms": 10,
                        "alt_host": {"host": "www.example.com", "result": "redirect",
                                     "status": 301, "location": "https://example.com/"}},
        },
        "pages": [
            page("https://example.com/", h1=["예시 주식회사"], title="예시 주식회사"),
            page("https://example.com/pricing", h1=["요금 안내"],
                 meta_description="차종별 일·월 요금표를 매일 갱신해 공개한다."),
            page("https://example.com/branches/seoul", h1=["서울 지점"]),
            page("https://example.com/hidden", meta_robots="noindex"),
            page("https://example.com/dup", canonical="https://example.com/pricing"),
            page("https://example.com/gone", status=404, title=None, h1=[]),
        ],
        "stats": {"pages_crawled": 6, "unique_titles": 4, "unique_descriptions": 1,
                  "pages_with_jsonld": 0, "pages_noindex": 1},
        "findings": [],
        "scorecard": {lane: {"status": "warn", "evidence": []} for lane in crawl.LANES},
    }
    base.update(kw)
    return base


SITE = {
    "name": "예시 주식회사",
    "legal_name": "주식회사 예시",
    "url": "https://example.com/",
    "logo": "",
    "description": "우리는 예시 고객이 예시 문제를 검증 가능한 방식으로 풀도록 돕는다.",
    "same_as": ["https://www.linkedin.com/company/example"],
    "contact": {"phone": "+82-2-0000-0000", "email": ""},
    "address": {"locality": "서울", "country": "KR", "street": ""},
    "founding_year": "2015",
    "faqs": [
        {"page_url": "https://example.com/pricing", "q": "요금은 언제 갱신되나요?",
         "a": "매일 오전 9시에 갱신합니다."},
        {"page_url": "https://example.com/no-such-page", "q": "여긴 크롤 안 됐다",
         "a": "그래서 빠져야 한다."},
        {"page_url": "https://example.com/pricing", "q": "", "a": "질문이 비었다"},
    ],
    "products": [
        {"page_url": "https://example.com/pricing", "name": "예시 표준 요금제",
         "offers": {"price": "30000", "currency": "KRW", "unit": "1일 기준"}},
        {"page_url": "https://example.com/branches/seoul", "name": "가격 없는 상품",
         "offers": {}},
    ],
}


class Base(unittest.TestCase):
    SITE_JSON = SITE

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sumultigeo-")
        self.ctx = generate.run("all", audit(), self.SITE_JSON, self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as fh:
            return fh.read()


# ─────────────────────────────────────────────────────────── sitemap

class TestSitemap(Base):
    def test_excludes_noindex_non200_and_foreign_canonical(self):
        xml = self.read("sitemap.xml")
        self.assertIn("<loc>https://example.com/</loc>", xml)
        self.assertIn("<loc>https://example.com/pricing</loc>", xml)
        self.assertNotIn("/hidden", xml)   # noindex
        self.assertNotIn("/gone", xml)     # 404
        self.assertNotIn("/dup", xml)      # canonical이 남을 가리킨다

    def test_no_fabricated_lastmod(self):
        self.assertNotIn("lastmod", self.read("sitemap.xml"))

    def test_new_urls_are_reported_in_deploy(self):
        self.assertIn("https://example.com/pricing", self.read("DEPLOY.md"))
        self.assertIn("크롤엔 있는데 기존 사이트맵에 없던 URL", self.read("DEPLOY.md"))

    def test_splits_over_the_limit(self):
        many = audit()
        many["pages"] = [page("https://example.com/p%d" % i)
                         for i in range(generate.MAX_URLS_PER_FILE + 5)]
        out = tempfile.mkdtemp(prefix="sumultigeo-big-")
        try:
            ctx = generate.run("sitemap", many, {}, out)
            self.assertIn("sitemap_index.xml", ctx.files)
            self.assertIn("sitemap-1.xml", ctx.files)
            self.assertIn("sitemap-2.xml", ctx.files)
            with open(os.path.join(out, "sitemap_index.xml"), encoding="utf-8") as fh:
                self.assertIn("https://example.com/sitemap-2.xml", fh.read())
        finally:
            shutil.rmtree(out, ignore_errors=True)


# ─────────────────────────────────────────────────────────── robots

class TestRobots(Base):
    def test_existing_content_is_preserved_verbatim(self):
        after = self.read("robots.txt")
        self.assertTrue(after.startswith(ROBOTS_RAW.rstrip("\n")))
        self.assertIn("Disallow: /admin/", after)

    def test_blocked_ua_is_not_flipped_to_allow(self):
        after = self.read("robots.txt")
        added = after[len(ROBOTS_RAW):]
        self.assertNotIn("GPTBot", added)
        self.assertIn("차단 유지 — 의도 확인 필요", self.read("DEPLOY.md"))

    def test_star_partial_rules_are_copied_not_widened(self):
        added = self.read("robots.txt")[len(ROBOTS_RAW):]
        self.assertIn("User-agent: ClaudeBot", added)
        # `*` 그룹이 /admin/을 막고 있으므로 Allow: / 로 넓히지 않는다
        self.assertIn("Disallow: /admin/", added)
        self.assertNotIn("Allow: /\n", added)

    def test_plain_site_gets_explicit_allow_for_every_ua(self):
        clean = audit()
        clean["site"]["robots"]["raw"] = ""
        clean["site"]["robots"]["policies"] = {ua: "none" for ua in crawl.ALL_UAS}
        clean["site"]["robots"]["sitemap_declared"] = []
        out = tempfile.mkdtemp(prefix="sumultigeo-clean-")
        try:
            generate.run("all", clean, {}, out)
            with open(os.path.join(out, "robots.txt"), encoding="utf-8") as fh:
                text = fh.read()
            for ua in crawl.ALL_UAS:
                self.assertIn("User-agent: %s" % ua, text)
            self.assertIn("Sitemap: https://example.com/sitemap.xml", text)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_sitemap_declaration_is_added_only_when_missing(self):
        # 픽스처는 이미 sitemap.xml을 선언하고 있다 → 중복 선언하지 않는다
        self.assertEqual(self.read("robots.txt").count("Sitemap: https://example.com/sitemap.xml"), 1)

    def test_diff_is_recorded(self):
        self.assertIn("```diff", self.read("DEPLOY.md"))


# ─────────────────────────────────────────────────────────── llms.txt

class TestLlms(Base):
    def test_uses_site_facts_and_marks_the_rest_todo(self):
        text = self.read("llms.txt")
        self.assertTrue(text.startswith("# 예시 주식회사"))
        self.assertIn("> 우리는 예시 고객이", text)
        self.assertIn("<<TODO: 한 줄 설명>>", text)
        self.assertIn("## 데이터 출처와 이용", text)
        self.assertIn("인용 시 출처 표기: example.com", text)

    def test_section_representatives_come_from_the_crawl(self):
        text = self.read("llms.txt")
        self.assertIn("](https://example.com/pricing)", text)
        self.assertIn("[요금 안내]", text)
        self.assertNotIn("/hidden", text)

    def test_todo_marks_when_site_json_is_absent(self):
        out = tempfile.mkdtemp(prefix="sumultigeo-nosite-")
        try:
            generate.run("llms", audit(), {}, out)
            with open(os.path.join(out, "llms.txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("# example.com"))
            self.assertIn("<<TODO: 우산 메시지", text)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_shared_h1_falls_back_to_the_url_path(self):
        # 페이지마다 같은 로고 h1을 쓰는 사이트 — 라벨을 지어내지 않고 경로에서 가져온다
        same = audit()
        same["pages"] = [page("https://example.com/", h1=["예시 주식회사"]),
                         page("https://example.com/pricing", h1=["예시 주식회사"]),
                         page("https://example.com/branches", h1=["예시 주식회사"])]
        out = tempfile.mkdtemp(prefix="sumultigeo-h1-")
        try:
            generate.run("llms", same, {}, out)
            with open(os.path.join(out, "llms.txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("- [pricing](https://example.com/pricing)", text)
            self.assertIn("- [branches](https://example.com/branches)", text)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_llms_full_is_not_generated(self):
        self.assertFalse(os.path.exists(os.path.join(self.dir, "llms-full.txt")))


# ─────────────────────────────────────────────────────────── JSON-LD

class TestJsonLd(Base):
    def artifact(self, url, suffix):
        return "jsonld/%s.%s" % (generate.slug_of(url), suffix)

    def test_organization_omits_empty_fields_and_never_invents_sameas(self):
        org = json.loads(self.read("jsonld/organization.json"))
        self.assertEqual(org["@id"], "https://example.com#organization")
        self.assertEqual(org["name"], "예시 주식회사")
        self.assertEqual(org["sameAs"], ["https://www.linkedin.com/company/example"])
        self.assertNotIn("logo", org)      # site.json에서 빈 문자열
        self.assertNotIn("email", org)     # contact.email이 비었다
        self.assertEqual(org["address"], {"addressLocality": "서울",
                                          "addressCountry": "KR",
                                          "@type": "PostalAddress"})

    def test_no_organization_without_site_facts(self):
        out = tempfile.mkdtemp(prefix="sumultigeo-noorg-")
        try:
            ctx = generate.run("jsonld", audit(), {}, out)
            self.assertNotIn("jsonld/organization.json", ctx.files)
            self.assertTrue(any("Organization" in t for t in ctx.todos))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_faq_only_for_crawled_pages_with_both_q_and_a(self):
        faq = json.loads(self.read(self.artifact("https://example.com/pricing", "faq.json")))
        self.assertEqual(len(faq["mainEntity"]), 1)
        self.assertEqual(faq["mainEntity"][0]["name"], "요금은 언제 갱신되나요?")
        for rel in self.ctx.files:
            self.assertNotIn("no-such-page", rel)
        self.assertIn("크롤되지 않은 URL", self.read("DEPLOY.md"))

    def test_faq_verification_duty_is_stated(self):
        self.assertIn("글자 그대로", self.read("DEPLOY.md"))

    def test_product_offer_needs_a_real_price(self):
        product = json.loads(self.read(self.artifact("https://example.com/pricing", "product.json")))
        self.assertEqual(product["offers"]["price"], "30000")
        self.assertEqual(product["offers"]["priceCurrency"], "KRW")
        other = json.loads(self.read(self.artifact("https://example.com/branches/seoul", "product.json")))
        self.assertNotIn("offers", other)

    def test_breadcrumb_is_built_from_crawled_paths(self):
        crumb = json.loads(self.read(self.artifact("https://example.com/branches/seoul", "breadcrumb.json")))
        names = [item["name"] for item in crumb["itemListElement"]]
        self.assertEqual(names[0], "예시 주식회사")
        self.assertEqual(names[-1], "서울 지점")
        self.assertEqual(crumb["itemListElement"][-1]["item"],
                         "https://example.com/branches/seoul")
        self.assertFalse(os.path.exists(os.path.join(
            self.dir, self.artifact("https://example.com/", "breadcrumb.json"))))

    def test_website_has_name_and_url_only(self):
        website = json.loads(self.read("jsonld/website.json"))
        self.assertEqual(set(website), {"@context", "@type", "name", "url"})

    def test_every_generated_jsonld_parses(self):
        found = 0
        for rel in self.ctx.files:
            if rel.endswith(".json"):
                obj = json.loads(self.read(rel))
                found += 1
                if rel.startswith("jsonld/") and rel != generate.JSONLD_MANIFEST:
                    self.assertEqual((obj[0] if isinstance(obj, list) else obj)["@context"],
                                     "https://schema.org")
        self.assertGreater(found, 3)

    def test_snippets_are_script_tags(self):
        snippet = self.read(self.artifact("https://example.com/pricing", "snippet.html"))
        self.assertIn('<script type="application/ld+json">', snippet)
        json.loads(snippet.split(">", 1)[1].split("</script>")[0])


# ─────────────────────────────────────────────────────────── meta 초안

class TestMeta(Base):
    def rows(self):
        return {r["url"]: r for r in json.loads(self.read("meta-draft.json"))}

    def test_title_draft_uses_h1_plus_brand(self):
        row = self.rows()["https://example.com/pricing"]
        self.assertEqual(row["draft_title"], "요금 안내 | 예시 주식회사")
        self.assertEqual(row["title_source"], "h1")

    def test_length_verdicts_follow_the_korean_rule(self):
        rows = self.rows()
        # "요금 안내 | 예시 주식회사" = 15자 → 한글 권장 25~30에 못 미친다
        self.assertEqual(rows["https://example.com/pricing"]["title_len"], 15)
        self.assertEqual(rows["https://example.com/pricing"]["title_verdict"], "짧음")
        long_audit = audit()
        long_audit["pages"] = [page("https://example.com/x", h1=["가" * 40])]
        out = tempfile.mkdtemp(prefix="sumultigeo-len-")
        try:
            generate.run("meta", long_audit, {}, out)
            with open(os.path.join(out, "meta-draft.json"), encoding="utf-8") as fh:
                row = json.load(fh)[0]
            self.assertEqual(row["title_verdict"], "김")
            self.assertEqual(row["title_len"], 40)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_description_is_never_invented(self):
        rows = self.rows()
        self.assertEqual(rows["https://example.com/pricing"]["draft_description"],
                         "차종별 일·월 요금표를 매일 갱신해 공개한다.")
        self.assertTrue(rows["https://example.com/branches/seoul"]["draft_description"]
                        .startswith("<<TODO"))
        self.assertEqual(rows["https://example.com/branches/seoul"]["description_verdict"],
                         "없음")

    def test_csv_matches_json(self):
        import csv as csvmod
        with open(os.path.join(self.dir, "meta-draft.csv"), encoding="utf-8", newline="") as fh:
            rows = list(csvmod.DictReader(fh))
        self.assertEqual(len(rows), len(self.rows()))
        self.assertIn("draft_description", rows[0])

    def test_duplicate_drafts_are_flagged_not_invented(self):
        same = audit()
        same["pages"] = [page("https://example.com/a", h1=["예시 주식회사"], title="예시 주식회사"),
                         page("https://example.com/b", h1=["예시 주식회사"], title="예시 주식회사")]
        out = tempfile.mkdtemp(prefix="sumultigeo-dup-")
        try:
            ctx = generate.run("meta", same, {}, out)
            with open(os.path.join(out, "meta-draft.json"), encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertTrue(all(r["title_duplicate"] == "중복" for r in rows))
            self.assertTrue(any("겹친다" in t for t in ctx.todos))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_noindex_and_error_pages_are_not_drafted(self):
        urls = set(self.rows())
        self.assertNotIn("https://example.com/hidden", urls)
        self.assertNotIn("https://example.com/gone", urls)


# ─────────────────────────────────────────────────────────── DEPLOY.md

class TestDeploy(Base):
    def test_contains_placement_verification_rollback_and_todos(self):
        text = self.read("DEPLOY.md")
        for needle in ("## 0. 파일과 놓을 위치", "Laravel", "WordPress",
                       "## 6. 배포 후 검증", "curl -sI https://example.com/robots.txt",
                       "## 7. 롤백", "## 8. 사람이 채워야 할 TODO",
                       "사람이 검토하고 사람이 배포한다"):
            self.assertIn(needle, text)

    def test_todo_list_is_not_empty(self):
        self.assertTrue(self.ctx.todos)
        self.assertIn("- [ ]", self.read("DEPLOY.md"))

    def test_files_are_utf8_lf(self):
        for rel in self.ctx.files:
            with open(os.path.join(self.dir, rel), "rb") as fh:
                self.assertNotIn(b"\r\n", fh.read(), rel)


if __name__ == "__main__":
    unittest.main()
