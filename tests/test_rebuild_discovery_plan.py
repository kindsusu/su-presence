"""Discovery evidence integration must not manufacture exposure measurements."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_rebuild_plan import inputs
import rebuild
from rebuild_plan import build_plan

class DiscoveryPlanTests(unittest.TestCase):
    def test_discovered_source_compared_without_competitor_declaration(self):
        audit, brief, research = inputs()
        brief['competitors'] = []
        discovery = {'schema': 'su-presence/rebuild-discovery/1', 'candidates': [{
            'id': 'discovery-test', 'query_id': 'q1', 'query_text': 'product price',
            'url': 'https://rival.com/pricing', 'provider': 'brave', 'locale': 'en-US',
            'observed_at': '2026-10-13T00:00:00Z', 'position': 1}]}
        plan = build_plan(audit, brief, [], research, discovery=discovery)
        page = plan['page_blueprints'][0]
        self.assertEqual(page['source_comparison'][0]['role'], 'discovered_source')
        self.assertIn('discovery-test', page['evidence_ids'])
        self.assertTrue(all(c['status'] == 'unmeasured' for c in plan['channel_actions']))
        self.assertEqual(plan['summary']['observations'], 0)
        research['pages'][0]['headings'] = ['Product price details']
        plan = build_plan(audit, brief, [], research, discovery=discovery)
        self.assertTrue(plan['page_blueprints'][0]['section_proposals'])
        for render in (rebuild.markdown, rebuild.render_html):
            self.assertIn('Product price details', render(plan))
        for render in (rebuild.page_draft, rebuild.page_html):
            self.assertIn('Product price details', render(plan['page_blueprints'][0], plan))

    def test_stale_query_rejected(self):
        audit, brief, research = inputs()
        with self.assertRaises(ValueError):
            build_plan(audit, brief, [], research, discovery={
                'schema': 'su-presence/rebuild-discovery/1', 'candidates': [
                    {'query_id': 'q1', 'query_text': 'old query'}]})
