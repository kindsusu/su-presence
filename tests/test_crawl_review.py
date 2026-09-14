"""Required sitemaps, mirror indexability and canonical-host redirect regressions."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import crawl
import generate
import report


BASE = "https://example.com"
PAGE = '<html><head><title>Example company</title></head><body><h1>Example</h1>' + "words " * 80 + '</body></html>'


def response(url, status=200, body="", error=None, headers=None, final_url=None):
    return {"status": status, "body": body, "error": error, "headers": headers or {},
            "final_url": final_url or url, "ms": 1, "redirects": 0,
            "content_type": "text/html"}


def site_response(url, **kwargs):
    if crawl.host_of(url) != "example.com":
        return response(url, status=None, error="dns_fail")
    if url.endswith("/robots.txt"):
        return response(url, body="User-agent: *\nAllow: /\nSitemap: " + BASE + "/sitemap.xml\n")
    if url.endswith("/sitemap.xml"):
        return response(url, body='<urlset><url><loc>' + BASE + '/</loc></url></urlset>')
    if url.rstrip("/") == BASE:
        return response(url, body=PAGE)
    return response(url, status=404)


def audit_using(fetch):
    with patch.object(crawl, "fetch", side_effect=fetch), contextlib.redirect_stderr(io.StringIO()):
        return crawl.build_report(BASE, 10, 0)


class RequiredSitemapTests(unittest.TestCase):
    def test_child_fetch_failure_blocks_replacement_and_cli_success(self):
        for status, error in ((503, None), (404, None), (None, "timeout")):
            with self.subTest(status=status, error=error):
                def fetch(url, **kwargs):
                    if url == BASE + "/sitemap.xml":
                        return response(url, body='<sitemapindex><sitemap><loc>' + BASE + '/products.xml</loc></sitemap></sitemapindex>')
                    if url == BASE + "/products.xml":
                        return response(url, status=status, error=error)
                    return site_response(url, **kwargs)

                audit = audit_using(fetch)
                self.assertFalse(audit["coverage"]["complete"])
                self.assertIn("sitemap_unavailable", audit["coverage"]["reasons"])
                child = next(s for s in audit["site"]["sitemaps"] if s["url"].endswith("/products.xml"))
                self.assertTrue(child["required"])
                with tempfile.TemporaryDirectory() as out:
                    ctx = generate.run("sitemap", audit, {}, out)
                    self.assertNotIn("sitemap.xml", ctx.files)
                    self.assertFalse(os.path.exists(os.path.join(out, "sitemap.xml")))
                    with patch.object(crawl, "build_report", return_value=audit), \
                            contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(crawl.main([BASE, "--out", out]), 2)

    def test_declared_sitemap_404_is_required_but_optional_404_is_not(self):
        good = audit_using(site_response)
        self.assertTrue(good["coverage"]["complete"])
        optional = next(s for s in good["site"]["sitemaps"] if s["url"].endswith("/sitemap_index.xml"))
        self.assertFalse(optional["required"])

        def missing(url, **kwargs):
            if url == BASE + "/sitemap.xml":
                return response(url, status=404)
            return site_response(url, **kwargs)

        self.assertFalse(audit_using(missing)["coverage"]["complete"])

    def test_previously_fetched_default_is_required_when_index_references_it(self):
        def fetch(url, **kwargs):
            if url == BASE + "/robots.txt":
                return response(url, body="User-agent: *\nAllow: /\n")
            if url == BASE + "/sitemap.xml":
                return response(url, status=404)
            if url == BASE + "/sitemap_index.xml":
                return response(url, body='<sitemapindex><sitemap><loc>' + BASE + '/sitemap.xml</loc></sitemap></sitemapindex>')
            return site_response(url, **kwargs)

        audit = audit_using(fetch)
        self.assertFalse(audit["coverage"]["complete"])
        required = next(s for s in audit["site"]["sitemaps"] if s["url"] == BASE + "/sitemap.xml")
        self.assertTrue(required["required"])

    def test_successful_child_still_generates_a_complete_sitemap(self):
        def fetch(url, **kwargs):
            if url == BASE + "/sitemap.xml":
                return response(url, body='<sitemapindex><sitemap><loc>' + BASE + '/products.xml</loc></sitemap></sitemapindex>')
            if url == BASE + "/products.xml":
                return response(url, body='<urlset><url><loc>' + BASE + '/product</loc></url></urlset>')
            if url == BASE + "/product":
                return response(url, body=PAGE)
            return site_response(url, **kwargs)

        audit = audit_using(fetch)
        self.assertTrue(audit["coverage"]["complete"])
        with tempfile.TemporaryDirectory() as out:
            generate.run("sitemap", audit, {}, out)
            with open(os.path.join(out, "sitemap.xml"), encoding="utf-8") as stream:
                self.assertIn(BASE + "/product</loc>", stream.read())


class MirrorIndexabilityTests(unittest.TestCase):
    def mirrored_audit(self, body=PAGE, headers=None, error=None, robots_status=200):
        def fetch(url, **kwargs):
            if crawl.host_of(url) == "dev.example.com":
                if url.endswith("/robots.txt"):
                    return response(url, status=robots_status, body="User-agent: *\nAllow: /\n")
                return response(url, body=body, headers=headers, error=error)
            return site_response(url, **kwargs)
        return audit_using(fetch)

    def test_meta_and_header_noindex_are_not_public_indexing_findings(self):
        cases = ((PAGE.replace("<head>", '<head><meta name="robots" content="noindex">'), {}),
                 (PAGE, {"x-robots-tag": "noindex"}),
                 (PAGE, {"x-robots-tag": "googlebot: noindex"}))
        for body, headers in cases:
            with self.subTest(headers=headers, body=body[:70]):
                audit = self.mirrored_audit(body, headers)
                mirror = audit["site"]["hygiene"]["mirrors"]["found"][0]
                self.assertEqual(mirror["indexability"], "noindex")
                self.assertIn("noindex", mirror["robots_directives"])
                codes = {f["code"] for f in audit["findings"]}
                self.assertNotIn("MIRROR_PUBLIC", codes)
                self.assertIn("MIRROR_NOINDEX", codes)
                rendered = crawl.PageParser()
                rendered.feed(report.render(audit, "en"))
                self.assertIn("noindex directive", rendered.text)

    def test_unrestricted_root_still_has_a_finding(self):
        audit = self.mirrored_audit()
        self.assertEqual(audit["site"]["hygiene"]["mirrors"]["found"][0]["indexability"], "unrestricted")
        self.assertIn("MIRROR_PUBLIC", {f["code"] for f in audit["findings"]})

    def test_incomplete_html_or_robots_response_is_unknown(self):
        for kwargs in ({"error": "body_truncated"}, {"body": ""}, {"robots_status": 503}):
            with self.subTest(**kwargs):
                audit = self.mirrored_audit(**kwargs)
                mirror = audit["site"]["hygiene"]["mirrors"]["found"][0]
                self.assertEqual(mirror["indexability"], "unknown")
                codes = {f["code"] for f in audit["findings"]}
                self.assertNotIn("MIRROR_PUBLIC", codes)
                self.assertIn("MIRROR_INDEXABILITY_UNKNOWN", codes)
                rendered = crawl.PageParser()
                rendered.feed(report.render(audit, "en"))
                self.assertIn("indexing restrictions are unknown", rendered.text)

    def test_unrelated_bot_noindex_does_not_apply_to_googlebot(self):
        audit = self.mirrored_audit(headers={"x-robots-tag": "bingbot: noindex"})
        self.assertEqual(audit["site"]["hygiene"]["mirrors"]["found"][0]["indexability"], "unrestricted")


class AltHostRedirectTests(unittest.TestCase):
    def test_redirect_to_audited_host_is_recorded_without_following_it(self):
        for status in (301, 308):
            with self.subTest(status=status):
                def fetch(url, **kwargs):
                    if url == "https://www.example.com/":
                        return response(url, status=status, error="external_redirect_blocked",
                                        final_url=BASE + "/", headers={"location": BASE + "/"})
                    return site_response(url, **kwargs)
                audit = audit_using(fetch)
                alt = audit["site"]["hygiene"]["alt_host"]
                self.assertEqual(alt["result"], "redirect")
                self.assertEqual(alt["location"], BASE + "/")
                self.assertNotIn("ALT_HOST_UNREACHABLE", {f["code"] for f in audit["findings"]})

    def test_unrelated_destination_is_still_an_error(self):
        def fetch(url, **kwargs):
            if url == "https://www.example.com/":
                return response(url, status=301, error="external_redirect_blocked", final_url="https://other.example/")
            return site_response(url, **kwargs)
        audit = audit_using(fetch)
        self.assertEqual(audit["site"]["hygiene"]["alt_host"]["result"], "error")
        self.assertIn("ALT_HOST_UNREACHABLE", {f["code"] for f in audit["findings"]})


if __name__ == "__main__":
    unittest.main()
