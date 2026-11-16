"""Product evidence, review gates and fixed-question round trip (offline)."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import measure
import rebuild
import rebuild_plan
import rebuild_protocol
import rebuild_questions as questions

EX = ROOT / 'templates/questions'


class ProductQuestions(unittest.TestCase):
    def setUp(self):
        self.catalog = questions.read(EX / 'catalog.json')
        self.log = measure.load_log(str(EX / 'baseline.jsonl'))
        self.selected = questions.read(EX / 'selected.json')
        self.fixed = {'schema': measure.SCHEMA_QUERIES, 'queries': [
            {'id': 'Q01', 'text': 'Original wording?', 'type': 'brand', 'note': 'keep'}]}

    def review(self, log=None):
        rows, held = questions.review_questions(self.catalog, EX)
        questions.add_priorities(rows, self.log if log is None else log, self.catalog['channels'])
        return rows, held

    def test_official_evidence_gate(self):
        for patch in ({'source_url': 'https://example.com.other.org/service/'},
                      {'strength_quote': '보증금 무료'}, {'scope_quote': ''},
                      {'verified': 'true'}, {'snapshot': '../rebuild/brief.json'},
                      {'checked_at': 'not a date'}):
            with self.subTest(patch=patch):
                catalog = copy.deepcopy(self.catalog)
                catalog['products'][0].update(patch)
                rows, held = questions.review_questions(catalog, EX)
                self.assertFalse(rows)
                self.assertEqual(len(held), 4)

    def test_wording_cohorts_and_holds(self):
        rows, held = self.review()
        self.assertEqual({r['id'] for r in held}, {'cordless', 'retired-saw', 'free-deposit'})
        nonbrand = [r for r in rows if r['type'] == 'nonbrand']
        self.assertEqual({r['intent'] for r in nonbrand}, {'find', 'compare', 'check'})
        self.assertEqual(nonbrand[0]['text'], self.catalog['products'][0]['wording'][0]['text'])
        self.assertEqual(nonbrand[0]['wording_origin'], 'cs')
        self.assertEqual(nonbrand[1]['wording_origin'], 'product_hypothesis')
        self.assertTrue(all('주말공방' not in r['text'] for r in nonbrand))
        self.assertEqual([r['wording_origin'] for r in rows if r['type'] == 'brand'], ['search'])
        self.catalog['products'][0]['need'] = '위켄드킷'
        with self.assertRaisesRegex(ValueError, 'proprietary'):
            self.review()

    def test_unknown_and_noncomparable_runs_never_become_gaps(self):
        rows, _ = self.review()
        self.assertEqual(rows[0]['priority'], 1)
        for change in ({'outcome': 'blocked'}, {'answer_complete': False},
                       {'citation_coverage': 'unknown'}, {'login_state': 'signed_in'},
                       {'mode': 'api'}, {'search_enabled': False}, {'query_fingerprint': 'old'}):
            r = self.review([dict(self.log[0], **change)])[0][0]
            self.assertEqual(r['citation_evidence']['known_runs'], 0)
            self.assertEqual(r['priority'], 2)

    def test_refuse_stale_review_blank_reason_and_id_collisions(self):
        rows, _ = self.review()
        for bad in ([], [dict(self.selected[0], reason=' ')],
                    [dict(self.selected[0], signature='old')], self.selected * 2):
            with self.assertRaises(ValueError):
                questions.freeze(self.catalog, rows, bad, self.fixed)
        for conflict in (dict(rows[0], text='old wording'), dict(rows[0], id='Q02')):
            with self.assertRaises(ValueError):
                questions.freeze(self.catalog, rows, self.selected, dict(self.fixed, queries=[conflict]))

    def test_cli_preserves_inputs_and_round_trips_into_plan_and_remeasurement(self):
        with tempfile.TemporaryDirectory() as d:
            fixed = Path(d) / 'queries.json'
            fixed.write_text(json.dumps(self.fixed), encoding='utf-8')
            out = Path(d) / 'accepted'
            cmd = ['questions', str(EX/'catalog.json'), '--log', str(EX/'baseline.jsonl'),
                   '--selection', str(EX/'selected.json'), '--existing', str(fixed), '--out', str(out)]
            self.assertEqual(rebuild.main(cmd), 0)
            before = (out/'queries.json').read_bytes()
            self.assertNotEqual(rebuild.main(cmd), 0)
            self.assertEqual((out/'queries.json').read_bytes(), before)
            self.assertEqual(questions.read(fixed), self.fixed)
            self.assertEqual(questions.read(out/'queries.json')['queries'][0], self.fixed['queries'][0])
            fixed_rows = measure.load_queries(str(out))
            self.assertEqual(len(fixed_rows), 4)
            brief = questions.read(out/'brief.json')
            q = brief['queries'][0]
            self.assertEqual(q['provenance']['evidence']['source_url'], 'https://example.com/service/')
            self.assertEqual(q['provenance']['selection_reason'], self.selected[0]['reason'])
            self.assertEqual(fixed_rows[1]['text'], q['text'])
            obs, _ = rebuild.from_log(EX/'baseline.jsonl', brief)
            self.assertTrue(self.log[0]['brand_mentioned'])
            self.assertFalse(self.log[0]['cited'])
            self.assertEqual([r['url'] for r in obs], ['https://example.org/weekend-drill/'])
            plan = rebuild_plan.build_plan(rebuild.audit_input(ROOT/'templates/rebuild/audit.json'),
                                           brief, obs, rebuild.load_research(EX/'research.json'))
            page = plan['page_blueprints'][0]
            self.assertIn('https://example.com/service/', page['existing_candidates'])
            self.assertIn('observed_publisher', [p['role'] for p in page['source_comparison']])
            protocol = rebuild_protocol.build_protocol(brief, 1)
            self.assertIn('drill-day.check.brand', protocol['excluded_brand_query_ids'])
            slot = protocol['slots'][0]
            self.assertEqual((slot['query_id'], slot['query_text']), (q['id'], q['text']))
            self.assertEqual(slot['status'], 'pending')
