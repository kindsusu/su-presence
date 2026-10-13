import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rebuild_analysis import IMPORT_SCHEMA, SCHEMA, NeedsInput, analyze, enrich_plan


class RebuildAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.brief = {"site_url": "https://example.com", "queries": [
            {"id": "q1", "intent_id": "choose", "text": "choose a service", "terms": ["service"]}]}
        self.research = {"schema": "su-presence/rebuild-research/1", "pages": [
            {"outcome": "observed", "url": "https://publisher.example.org/compare", "query_ids": ["q1"],
             "title": "Service comparison", "h1": "Compare services",
             "headings": ["Eligibility"], "text_excerpt": "Read the published eligibility terms before applying."},
            {"outcome": "error", "title": "Hidden", "text_excerpt": "Made up offer"}]}

    def test_structural_fallback_is_explicit(self):
        result = analyze(self.brief, self.research)
        self.assertEqual((result["schema"], result["mode"]), (SCHEMA, "structural_only"))
        self.assertEqual(result["intents"][0]["findings"], [])
        self.assertTrue(result["warnings"])

    def test_import_requires_literal_observed_quote(self):
        imported = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "findings": [
            {"kind": "condition", "summary": "It discusses eligibility", "source_id": "research-page-0",
             "quote": "published eligibility terms"},
            {"kind": "evidence", "summary": "Unseen claim", "source_id": "research-page-0", "quote": "unseen claim"},
            {"kind": "evidence", "summary": "Failed page", "source_id": "research-page-1", "quote": "Made up offer"}],
            "section_suggestions": [{"heading": "Eligibility review", "purpose": "Explain verified conditions",
                                     "source_id": "research-page-0", "quote": "Eligibility"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            result = analyze(self.brief, self.research, {"analysis_file": str(path)})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["intents"][0]["findings"]), 1)
        self.assertEqual(len(result["intents"][0]["section_suggestions"]), 1)

    def test_enrichment_preserves_unverified_boundary(self):
        analysis = {"schema": SCHEMA, "status": "complete", "mode": "validated_import", "warnings": [], "intents": [
            {"intent_id": "choose", "findings": [], "section_suggestions": [
                {"heading": "Eligibility review", "purpose": "Explain conditions", "source_id": "research-page-0",
                 "quote": "Eligibility"}]}]}
        original = {"page_blueprints": [{"intent_id": "choose", "action": "update", "rationale": "mapped",
                   "existing_candidates": ["https://example.com/page"], "sections": [], "section_proposals": [],
                   "verified_facts": []}], "evidence": [{"id": "research-page-0"}], "limits": []}
        result = enrich_plan(original, analysis)
        page = result["page_blueprints"][0]
        self.assertEqual(page["page_decision"]["action"], "improve")
        self.assertEqual(page["verified_facts"], [])
        self.assertEqual(page["section_proposals"][0]["supporting_quote"], "Eligibility")
        self.assertEqual(original["page_blueprints"][0]["section_proposals"], [])

    def test_openai_provider_requires_explicit_model_and_key(self):
        with self.assertRaises(NeedsInput):
            analyze(self.brief, self.research, {"provider": "openai"})
        with patch.dict("os.environ", {"TEST_ANALYSIS_KEY": ""}):
            with self.assertRaises(NeedsInput):
                analyze(self.brief, self.research, {"provider": "openai", "model": "test-model",
                                                    "api_key_env": "TEST_ANALYSIS_KEY"})

    def test_openai_provider_validates_output_without_web_tools(self):
        import rebuild_analysis

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, *_):
                document = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "findings": [
                    {"kind": "answer_scope", "summary": "Eligibility is addressed", "source_id": "research-page-0",
                     "quote": "published eligibility terms"}]}]}
                return json.dumps({"status": "completed", "output": [{"content": [
                    {"type": "output_text", "text": json.dumps(document)}]}]}).encode()

        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        opener = Opener()
        with patch.dict("os.environ", {"TEST_ANALYSIS_KEY": "secret"}), patch.object(
                rebuild_analysis.urllib.request, "build_opener", return_value=opener):
            result = analyze(self.brief, self.research, {"provider": "openai", "model": "test-model",
                                                         "api_key_env": "TEST_ANALYSIS_KEY"})
        self.assertEqual(result["intents"][0]["findings"][0]["kind"], "answer_scope")
        request_body = json.loads(opener.request.data)
        self.assertNotIn("tools", request_body)
        self.assertIs(request_body["store"], False)
        self.assertEqual(request_body["input"] and json.loads(request_body["input"])["sources"][0]["role"], "external")
        self.assertEqual(opener.timeout, 30)

    def test_real_planner_shape_and_conservative_decision(self):
        from rebuild_plan import build_plan

        site = "https://example.com"
        brief = {"schema": "su-presence/rebuild-brief/1", "site_url": site,
                 "queries": [{"id": "q1", "text": "service terms", "intent_id": "terms", "terms": ["terms"],
                              "required_sections": [], "page_type": "guide", "priority": 2}],
                 "competitors": [], "channels": [], "facts": [{"id": "fact1", "text": "Terms published",
                 "source_url": site + "/terms", "verified": True, "intent_ids": ["terms"]}],
                 "pages": [{"intent_id": "terms", "url": site + "/terms"}]}
        audit = {"schema": "su-presence/audit/1", "target": {"base": site}, "coverage": {"complete": True},
                 "pages": [{"url": site + "/terms", "status": 200, "title": "Terms", "h1": "Terms"}],
                 "findings": []}
        research = {"schema": "su-presence/rebuild-research/1", "pages": [
            {"outcome": "observed", "url": site + "/terms", "final_url": site + "/terms",
             "title": "Terms", "h1": "Terms", "headings": ["Coverage"], "text_excerpt": "Published coverage terms"}]}
        imported = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "terms", "findings": [
                    {"kind": "answer_scope", "summary": "Terms described", "source_id": "research-page-0",
                     "quote": "Published coverage terms"}],
                    "decision": {"action": "keep", "reason": "Check current terms", "source_id": "research-page-0",
                                 "quote": "Published coverage terms"}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            analysis = analyze(brief, research, {"analysis_file": str(path)})
        result = enrich_plan(build_plan(audit, brief, [], research), analysis)
        page = result["page_blueprints"][0]
        self.assertEqual(page["intent_id"], "terms")
        self.assertEqual(page["page_decision"]["action"], "keep")
        self.assertTrue(page["page_decision"]["review_required"])
        self.assertEqual(page["page_decision"]["evidence_ids"], ["research-page-0"])
        self.assertEqual(analysis["intents"][0]["comparison"]["owned_source_ids"], ["research-page-0"])

    def test_malformed_import_entries_are_partial(self):
        imported = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "findings": {},
                    "section_suggestions": [], "decision": {"action": "new", "source_id": ["research-page-0"],
                    "quote": "Eligibility", "reason": "invalid source"}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            result = analyze(self.brief, self.research, {"analysis_file": str(path)})
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["intents"][0]["decision"])

    def test_consolidate_requires_two_explicit_and_observed_owned_pages(self):
        plan = {"page_blueprints": [{"intent_id": "choose", "action": "review", "rationale": "overlap",
                 "existing_candidates": ["https://example.com/a", "https://example.com/b"],
                 "missing_facts": [], "comparison_gaps": [], "sections": []}],
                "evidence": [{"id": "research-page-0", "url": "https://example.com/a"},
                             {"id": "research-page-1", "url": "https://example.com/b"}]}
        analysis = {"schema": SCHEMA, "mode": "validated_import", "status": "complete", "warnings": [],
                    "intents": [{"intent_id": "choose", "findings": [], "section_suggestions": [],
                    "decision": {"action": "consolidate", "reason": "consider combining", "source_id": "research-page-0",
                                 "quote": "A"}, "comparison": {"owned_source_ids": ["research-page-0"]},
                    "explicit_owned_urls": ["https://example.com/a", "https://example.com/b"]}]}
        self.assertEqual(enrich_plan(plan, analysis)["page_blueprints"][0]["page_decision"]["action"], "review")
        analysis["intents"][0]["comparison"]["owned_source_ids"].append("research-page-1")
        self.assertEqual(enrich_plan(plan, analysis)["page_blueprints"][0]["page_decision"]["action"], "consolidate")
        analysis["intents"][0]["explicit_owned_urls"] = []
        self.assertEqual(enrich_plan(plan, analysis)["page_blueprints"][0]["page_decision"]["action"], "review")

    def test_conflicts_are_quote_bound_and_unresolved(self):
        research = {"schema": "su-presence/rebuild-research/1", "pages": [
            {"outcome": "observed", "url": "https://example.com/a", "query_ids": ["q1"], "text_excerpt": "Applies to all."},
            {"outcome": "observed", "url": "https://rival.com/a", "query_ids": ["q1"], "text_excerpt": "Only some qualify."}]}
        brief = {**self.brief, "site_url": "https://example.com", "pages": []}
        imported = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "conflicts": [{
            "summary": "Qualification differs", "left": {"source_id": "research-page-0", "quote": "Applies to all."},
            "right": {"source_id": "research-page-1", "quote": "Only some qualify."}}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            analysis = analyze(brief, research, {"analysis_file": str(path)})
        row = analysis["intents"][0]
        self.assertEqual(row["comparison"]["owned_source_ids"], ["research-page-0"])
        self.assertEqual(row["comparison"]["external_source_ids"], ["research-page-1"])
        self.assertEqual(row["conflicts"][0]["status"], "review")
        plan = {"page_blueprints": [{"intent_id": "choose", "action": "review", "existing_candidates": [],
                 "sections": [], "missing_facts": [], "comparison_gaps": []}],
                "evidence": [{"id": "research-page-0"}, {"id": "research-page-1"}]}
        page = enrich_plan(plan, analysis)["page_blueprints"][0]
        self.assertTrue(any("conflicting" in check for check in page["unresolved_checks"]))

    def test_other_intent_source_cannot_support_claim(self):
        brief = {**self.brief, "queries": self.brief["queries"] + [
            {"id": "q2", "intent_id": "apply", "text": "apply now", "terms": ["apply"]}]}
        research = {"schema": "su-presence/rebuild-research/1", "pages": [
            {"outcome": "observed", "url": "https://publisher.example.org/compare", "query_ids": ["q2"],
             "text_excerpt": "Only some qualify."}]}
        imported = {"schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "findings": [
            {"kind": "condition", "summary": "Qualification", "source_id": "research-page-0",
             "quote": "Only some qualify."}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            result = analyze(brief, research, {"analysis_file": str(path)})
        self.assertEqual(result["intents"][0]["findings"], [])
        self.assertEqual(result["status"], "partial")

    def test_api_context_balances_owned_and_external(self):
        import rebuild_analysis

        research = {"schema": "su-presence/rebuild-research/1", "pages": [
            {"outcome": "observed", "url": "https://example.com/%d" % i, "query_ids": ["q1"],
             "text_excerpt": "Owned page %d" % i} for i in range(4)] + [
            {"outcome": "observed", "url": "https://rival.com/a", "query_ids": ["q1"],
             "text_excerpt": "External comparison"}]}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, *_):
                return json.dumps({"status": "completed", "output_text": json.dumps({
                    "schema": IMPORT_SCHEMA, "intents": [{"intent_id": "choose", "findings": []}]})}).encode()
        class Opener:
            def open(self, request, timeout):
                self.payload = json.loads(request.data)
                return Response()
        opener = Opener()
        with patch.dict("os.environ", {"TEST_ANALYSIS_KEY": "secret"}), patch.object(
                rebuild_analysis.urllib.request, "build_opener", return_value=opener):
            analyze(self.brief, research, {"provider": "openai", "model": "test-model",
                                           "api_key_env": "TEST_ANALYSIS_KEY", "max_pages": 2})
        selected = json.loads(opener.payload["input"])["sources"]
        self.assertEqual([item["role"] for item in selected], ["owned", "external"])


if __name__ == "__main__":
    unittest.main()
