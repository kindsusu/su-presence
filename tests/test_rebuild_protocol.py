import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rebuild
from rebuild_protocol import build_protocol, markdown, MAX_BRIEF_BYTES


def brief():
    return {
        'schema': 'su-presence/rebuild-brief/1', 'site_url': 'https://example.com',
        'competitors': [], 'channels': ['google', 'chatgpt', 'gemini'],
        'queries': [
            {'id': 'brand', 'text': 'Example service', 'intent_id': 'i', 'page_type': 'service',
             'terms': [], 'required_sections': [], 'type': 'brand'},
            {'id': 'general', 'text': 'Compare a service', 'intent_id': 'i', 'page_type': 'service',
             'terms': [], 'required_sections': [], 'type': 'nonbrand'},
            {'id': 'unknown', 'text': 'What is available', 'intent_id': 'i', 'page_type': 'service',
             'terms': [], 'required_sections': []},
        ], 'facts': [], 'pages': [],
    }


class RebuildProtocolTests(unittest.TestCase):
    def test_only_explicit_nonbrand_gets_pending_slots(self):
        result = build_protocol(brief(), 2)
        self.assertEqual(len(result['slots']), 4)
        self.assertEqual({x['query_id'] for x in result['slots']}, {'general'})
        self.assertEqual({x['provider'] for x in result['slots']}, {'chatgpt', 'gemini'})
        self.assertEqual(result['excluded_brand_query_ids'], ['brand'])
        self.assertEqual(result['query_classification_needs_review'][0]['id'], 'unknown')
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(result['coverage']['slot_limit_complete'])
        self.assertTrue(all(x['status'] == 'pending' and x['brand_mentioned'] is None
                            and x['cited_urls'] is None for x in result['slots']))
        self.assertTrue(all(x['conditions']['product'] == x['provider']
                            and x['conditions']['model'] is None
                            and x['conditions']['web_search'] is None for x in result['slots']))
        self.assertEqual(result['slots'][0]['query_sha256'],
                         hashlib.sha256('Compare a service'.encode()).hexdigest())

    def test_worksheet_escapes_untrusted_query(self):
        value = brief()
        value['queries'][1]['text'] = '<script>alert(1)</script>| next\nline'
        report = markdown(build_protocol(value))
        self.assertNotIn('<script>', report)
        self.assertIn('&lt;script&gt;', report)
        self.assertIn('&#124;', report)
        self.assertIn('<br>line', report)

    def test_invalid_repeats(self):
        for repeats in (0, 21, True, '3'):
            with self.subTest(repeats=repeats), self.assertRaises(ValueError):
                build_protocol(brief(), repeats)

    def test_default_providers_are_explicit(self):
        value = brief()
        value['channels'] = ['google']
        result = build_protocol(value)
        self.assertEqual(result['providers'], ['chatgpt', 'claude', 'gemini'])
        self.assertIn('default', result['provider_source'])

    def test_large_brief_reports_partial_coverage(self):
        value = brief()
        value['queries'] = [dict(value['queries'][1], id=f'q{i}') for i in range(100)]
        value['channels'] = ['chatgpt', 'claude', 'gemini', 'perplexity', 'copilot',
                             'google_aio', 'naver_ai']
        result = build_protocol(value, 3)
        self.assertEqual(result['coverage']['planned_slots'], 2100)
        self.assertEqual(result['coverage']['included_slots'], 1000)
        self.assertEqual(result['coverage']['omitted_slots'], 1100)
        self.assertFalse(result['coverage']['complete'])

    def test_cli_creates_distinct_runs_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'brief.json'
            source.write_text(json.dumps(brief()), encoding='utf-8')
            output = root / 'measure'
            for _ in range(2):
                self.assertEqual(rebuild.main(['protocol', str(source), '--out', str(output),
                                               '--repeats', '2']), 0)
            runs = list(output.iterdir())
            self.assertEqual(len(runs), 2)
            self.assertTrue(all((r / 'protocol.json').exists() and (r / 'protocol.md').exists()
                                for r in runs))
            self.assertEqual(rebuild.main(['protocol', str(source), '--out', str(output),
                                           '--repeats', '0']), 2)
            self.assertEqual(len(list(output.iterdir())), 2)

    def test_cli_rejects_oversized_brief_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'brief.json'
            source.write_bytes(b' ' * (MAX_BRIEF_BYTES + 1))
            output = root / 'measure'
            self.assertEqual(rebuild.main(['protocol', str(source), '--out', str(output)]), 2)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
