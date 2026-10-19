# -*- coding: utf-8 -*-
"""Offline rebuild planning behavior and evidence boundaries."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
from rebuild_plan import build_plan  # noqa: E402

SITE = "https://example.com"


def inputs(complete=True):
    brief = {"schema": "su-presence/rebuild-brief/1", "site_url": SITE,
             "queries": [
                 {"id": "q1", "text": "product price", "intent_id": "price", "terms": ["price"],
                  "required_sections": ["Pricing", "FAQ"], "page_type": "landing", "priority": 1},
                 {"id": "q2", "text": "product cost", "intent_id": "price", "terms": ["cost"],
                  "required_sections": ["Pricing"], "page_type": "landing", "priority": 2}],
             "competitors": ["https://rival.com"], "channels": ["google", "chatgpt", "video"],
             "facts": [{"id": "f1", "text": "Starts at $10", "source_url": SITE + "/source",
                        "verified": True, "intent_ids": ["price"]},
                       {"id": "f2", "text": "Fastest", "source_url": SITE + "/source",
                        "verified": False, "intent_ids": ["price"]}],
             "pages": []}
    audit = {"schema": "su-presence/audit/1", "target": {"base": SITE},
             "coverage": {"complete": complete},
             "pages": [{"url": SITE + "/guide", "status": 200, "title": "Guide", "h1": ["Guide"]}],
             "findings": []}
    research = {"schema": "su-presence/rebuild-research/1",
                "pages": [{"url": "https://rival.com/pricing", "final_url": "https://rival.com/pricing",
                           "status": 200, "outcome": "observed", "title": "Price", "h1": "Prices",
                           "headings": ["FAQ"], "text_excerpt": "Prices and terms", "fetched_at": "2026-10-01T00:00:00Z",
                           "features": {"table": True, "faq": True, "sources": False}, "error": None}],
                "coverage": {}}
    return audit, brief, research


class PlanTests(unittest.TestCase):
    def test_post_design_protocol_preserves_observation_boundary(self):
        import rebuild
        audit, brief, research = inputs()
        brief['queries'][0]['type'] = 'nonbrand'
        brief['queries'][1]['type'] = 'brand'
        result = build_plan(audit, brief, [], research)
        protocol = result['measurement_protocol']
        self.assertEqual(len(protocol['slots']), 3)
        self.assertTrue(all(slot['status'] == 'pending' for slot in protocol['slots']))
        self.assertEqual(protocol['excluded_brand_query_ids'], ['q2'])
        self.assertIn('rebuild review', rebuild.markdown(result))
        self.assertIn('rebuild review', rebuild.render_html(result))
        self.assertTrue(any('rebuild review' in check for check in result['page_blueprints'][0]['acceptance_checks']))

    def test_group_variants_and_trace_comparison(self):
        audit, brief, research = inputs()
        obs = [{"id": "hit1", "query_id": "q1", "channel": "google", "outcome": "observed",
                "url": "https://rival.com/pricing", "observed_at": "2026-10-01T00:00:00Z"}]
        plan = build_plan(audit, brief, obs, research)
        self.assertEqual(len(plan["page_blueprints"]), 1)
        page = plan["page_blueprints"][0]
        self.assertEqual(page["query_ids"], ["q1", "q2"])
        self.assertEqual(page["action"], "create")
        self.assertEqual(page["verified_facts"], ["f1"])
        self.assertIn("f2", page["missing_facts"])
        self.assertEqual([s["heading"] for s in page["sections"]], ["Pricing", "FAQ"])
        comparison = page["source_comparison"][0]
        self.assertEqual(comparison["role"], "observed_publisher")
        self.assertTrue(set(comparison["evidence_ids"]).issubset({e["id"] for e in plan["evidence"]}))
        self.assertIn("observation-hit1", comparison["evidence_ids"])

    def test_incomplete_audit_and_unmeasured_are_not_absence(self):
        audit, brief, research = inputs(False)
        plan = build_plan(audit, brief, [], research)
        self.assertEqual(plan["page_blueprints"][0]["action"], "review")
        self.assertTrue(all(c["status"] == "unmeasured" for c in plan["channel_actions"]))
        self.assertTrue(all(c["status"] != "not_applicable" for c in plan["channel_actions"]))
        self.assertTrue(any("incomplete" in value for value in plan["limits"]))

    def test_hostile_domain_boundary_and_explicit_conflict(self):
        audit, brief, research = inputs()
        audit["pages"].append({"url": "https://example.com.evil.test/price", "status": 200,
                               "title": "Price", "h1": ["Price"]})
        brief["pages"] = [{"intent_id": "price", "url": SITE + "/pricing"}]
        plan = build_plan(audit, brief, [], research)
        self.assertEqual(plan["page_blueprints"][0]["existing_candidates"], [SITE + "/pricing"])
        self.assertEqual(plan["page_blueprints"][0]["action"], "update")
        brief["queries"].append({"id": "q3", "text": "support", "intent_id": "support", "terms": [],
                                 "required_sections": [], "page_type": "guide", "priority": 2})
        brief["pages"].append({"intent_id": "support", "url": SITE + "/pricing"})
        conflicted = build_plan(audit, brief, [], research)
        self.assertEqual([p["action"] for p in conflicted["page_blueprints"]], ["review", "review"])

    def test_critical_blocker_precedes_page(self):
        audit, brief, research = inputs()
        audit["findings"] = [{"severity": "critical", "code": "NOINDEX", "message": "Blocked",
                              "urls": [SITE + "/guide"]}]
        plan = build_plan(audit, brief, [], research)
        self.assertEqual(plan["backlog"][0]["id"], "technical-1")
        self.assertEqual(plan["backlog"][1]["depends_on"], ["technical-1"])
        self.assertIn("audit-finding-0", plan["backlog"][0]["evidence_ids"])

    def test_declared_competitor_is_not_visibility(self):
        audit, brief, research = inputs()
        plan = build_plan(audit, brief, [], research)
        comparison = plan["page_blueprints"][0]["source_comparison"][0]
        self.assertEqual(comparison["role"], "declared_competitor")
        self.assertEqual(plan["channel_actions"][0]["status"], "unmeasured")

    def test_colliding_slugs_have_distinct_stable_ids_and_urls(self):
        audit, brief, research = inputs()
        brief["queries"].append({"id": "q3", "text": "alpha", "intent_id": "a_b", "terms": [],
                                 "required_sections": [], "page_type": "guide", "priority": 2})
        brief["queries"].append({"id": "q4", "text": "beta", "intent_id": "a-b", "terms": [],
                                 "required_sections": [], "page_type": "guide", "priority": 2})
        pages = build_plan(audit, brief, [], research)["page_blueprints"]
        self.assertEqual(len({p["id"] for p in pages}), 3)
        self.assertEqual(len({p["suggested_url"] for p in pages}), 3)

    def test_unscoped_fact_requires_relevance_review(self):
        audit, brief, research = inputs()
        brief["facts"][0].pop("intent_ids")
        page = build_plan(audit, brief, [], research)["page_blueprints"][0]
        self.assertNotIn("f1", page["verified_facts"])
        self.assertIn("f1", page["facts_requiring_relevance_review"])
        self.assertIn("source-backed facts for this intent", page["missing_facts"])
        self.assertIn("fact-f1", page["evidence_ids"])

    def test_intent_scopes_do_not_leak_to_other_pages(self):
        audit, brief, research = inputs()
        brief["queries"].append({"id": "q3", "text": "support", "intent_id": "support", "terms": [],
                                 "required_sections": ["Help"], "page_type": "guide", "priority": 2})
        pages = {p["intent_id"]: p for p in build_plan(audit, brief, [], research)["page_blueprints"]}
        self.assertEqual(pages["price"]["verified_facts"], ["f1"])
        self.assertEqual(pages["support"]["verified_facts"], [])
        self.assertEqual(pages["support"]["sections"][0]["required_fact_ids"], [])
        brief["facts"][0]["intent_ids"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "intent_ids"):
            build_plan(audit, brief, [], research)

    def test_channel_guidance_and_page_draft(self):
        audit, brief, research = inputs()
        brief["channels"] += ["naver_blog", "community", "social", "maps", "news", "wiki"]
        plan = build_plan(audit, brief, [], research)
        channels = {row["channel"]: row for row in plan["channel_actions"]}
        self.assertEqual(channels["google"]["surface"], "site")
        self.assertEqual(channels["chatgpt"]["surface"], "site")
        for name in ("video", "naver_blog", "community", "social", "maps", "news", "wiki"):
            self.assertEqual(channels[name]["surface"], "external_publisher")
            self.assertGreaterEqual(len(channels[name]["actions"]), 3)
        page = plan["page_blueprints"][0]
        self.assertEqual(page["page_type"], "landing")
        self.assertEqual(page["draft_copy"]["status"], "editorial_draft")
        self.assertEqual(page["section_layout"], ["Pricing", "FAQ"])
        self.assertTrue(page["cta_guidance"])
        self.assertIn("FAQPage", [item["type"] for item in page["schema_candidates"]])
        self.assertTrue(any("no rich-result promise" in item["review"] for item in page["schema_candidates"]))

    def test_comparison_gaps_distinguish_unknown_and_missing(self):
        audit, brief, research = inputs()
        brief["pages"] = [{"intent_id": "price", "url": SITE + "/pricing"}]
        unknown = build_plan(audit, brief, [], research)["page_blueprints"][0]
        self.assertTrue(unknown["comparison_gaps"])
        self.assertTrue(all(g["state"] == "unknown" and g["evidence_ids"] for g in unknown["comparison_gaps"]))
        research["pages"].append({"url": SITE + "/pricing", "final_url": SITE + "/pricing",
                                  "outcome": "observed", "status": 200, "title": "Prices", "h1": "Prices",
                                  "headings": ["Overview"], "features": {"table": False, "faq": False, "sources": False}})
        missing = build_plan(audit, brief, [], research)["page_blueprints"][0]
        gaps = {(g["kind"], g["item"]): g for g in missing["comparison_gaps"]}
        self.assertEqual(gaps[("required_heading", "FAQ")]["state"], "missing")
        self.assertEqual(gaps[("feature", "table")]["state"], "missing")
        self.assertIn("research-page-1", gaps[("feature", "table")]["evidence_ids"])

    def test_redirect_observation_and_context_preserved(self):
        audit, brief, research = inputs()
        research["pages"][0]["final_url"] = "https://rival.com/new-pricing"
        observation = {"id": "redirect", "query_id": "q1", "channel": "google_aio",
                       "url": "https://rival.com/pricing", "outcome": "observed", "observed_at": "2025-01-01",
                       "evidence_url": "https://archive.example.net/1", "mode": "manual", "locale": "ko-KR",
                       "login_state": "out", "search_enabled": True, "model": "model-a", "campaign_id": "c1"}
        brief["channels"].append("google_aio")
        plan = build_plan(audit, brief, [observation], research)
        row = next(e for e in plan["evidence"] if e["id"] == "observation-redirect")
        self.assertEqual(row["evidence_url"], observation["evidence_url"])
        self.assertEqual(row["context"]["campaign_id"], "c1")
        self.assertEqual(plan["page_blueprints"][0]["source_comparison"][0]["role"], "observed_publisher")
        self.assertEqual(next(c for c in plan["channel_actions"] if c["channel"] == "google_aio")["surface"], "site")
        self.assertTrue(any("Historical observations" in limit for limit in plan["limits"]))
        self.assertTrue(any("Research coverage" in limit for limit in plan["limits"]))

    def test_no_terms_without_mapping_requires_review_even_when_audit_complete(self):
        audit, brief, research = inputs(True)
        brief["queries"] = [{"id": "q3", "text": "help", "intent_id": "help", "terms": [],
                             "required_sections": [], "page_type": "guide", "priority": 2}]
        brief["facts"] = []
        plan = build_plan(audit, brief, [], research)
        page = plan["page_blueprints"][0]
        self.assertEqual(page["action"], "review")
        self.assertIn("No explicit page mapping", page["rationale"])
        self.assertEqual([s["heading"] for s in page["sections"]],
                         ["Prerequisites", "Steps", "Checks and next step"])

    def test_page_type_outlines_and_unknown_feature(self):
        audit, brief, research = inputs()
        cases = {"service": "Service scope", "product": "Product fit", "comparison": "Comparison criteria",
                 "guide": "Prerequisites", "article": "Key answer", "case-study": "Starting situation",
                 "local": "Location and coverage", "home": "What is offered"}
        brief["pages"] = [{"intent_id": "price", "url": SITE + "/pricing"}]
        brief["queries"][0]["required_sections"] = []
        brief["queries"][1]["required_sections"] = []
        research["pages"].append({"url": SITE + "/pricing", "final_url": SITE + "/pricing",
                                  "outcome": "observed", "status": 200, "title": "Pricing", "h1": "Pricing",
                                  "headings": [], "features": {}})
        for kind, first in cases.items():
            with self.subTest(kind):
                for q in brief["queries"]:
                    q["page_type"] = kind
                page = build_plan(audit, brief, [], research)["page_blueprints"][0]
                self.assertEqual(page["sections"][0]["heading"], first)
                self.assertTrue(all(s["purpose"] for s in page["sections"]))
                feature = next(g for g in page["comparison_gaps"] if g["kind"] == "feature" and g["item"] == "table")
                self.assertEqual(feature["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
