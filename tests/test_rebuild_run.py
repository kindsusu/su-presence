"""Run state, evidence boundaries and resumption for the connected rebuild CLI."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rebuild_run
import rebuild
from test_rebuild_plan import inputs


class RunTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / 'out' / 'test-rebuild-run'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        audit, _, research = inputs()
        self.audit = audit
        self.audit_file = self.root / 'audit.json'
        self.audit_file.write_text(json.dumps(audit), encoding='utf-8')
        self.research_page = research['pages'][0]
        self.config_file = self.root / 'config.json'
        self.config = {'audit_file': 'audit.json', 'max_research_pages': 4, 'max_queries': 1,
                       'discovery': {'provider': 'import', 'file': 'discovery.json'},
                       'analysis': {'provider': 'none'}}
        self._write_config()
        self.discovery_file = self.root / 'discovery.json'
        self._write_discovery()

    def _write_config(self):
        self.config_file.write_text(json.dumps(self.config), encoding='utf-8')

    def _write_discovery(self):
        self.discovery_file.write_text(json.dumps({'schema': 'su-presence/rebuild-discovery/1',
            'candidates': [{'query_id': 'q1', 'query_text': 'product price',
                'url': 'https://rival.com/pricing', 'title': 'Pricing', 'provider': 'import',
                'locale': 'en-US', 'observed_at': '2026-10-13T00:00:00Z', 'position': 1}]}), encoding='utf-8')

    def _run(self):
        return argparse.Namespace(site_url='https://example.com', topic='product price', locale='en-US',
                                  config=str(self.config_file), out=str(self.root / 'out'))

    def _folder(self):
        return next((self.root / 'out').iterdir())

    def _state(self):
        return json.loads((self._folder() / 'run.json').read_text(encoding='utf-8'))

    def _fake_fetch(self, url):
        row = dict(self.research_page)
        row.update(url=url, final_url=url)
        return row

    def test_research_deduplicates_equivalent_roots_and_merges_provenance(self):
        brief = {'site_url': 'https://www.example.com',
                 'pages': [{'url': 'https://www.example.com/'}],
                 'competitors': ['https://rival.com/']}
        audit = {'pages': [{'url': 'https://www.example.com:443/#top'}]}
        discovery = {'provider': 'import', 'candidates': [
            {'id': 'hit-1', 'query_id': 'q1', 'provider': 'import', 'url': 'https://rival.com'},
            {'id': 'hit-2', 'query_id': 'q2', 'provider': 'import', 'url': 'https://rival.com:443/#result'},
            {'id': 'hit-3', 'query_id': 'q2', 'provider': 'import', 'url': 'https://rival.com/?ref=other'}]}
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch) as fetch:
            research = rebuild_run._make_research(brief, audit, discovery, {'max_research_pages': 3})
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(research['coverage']['requested'], 3)
        self.assertTrue(research['coverage']['complete'])
        self.assertEqual([page['url'] for page in research['pages']],
                         ['https://www.example.com', 'https://rival.com', 'https://rival.com/?ref=other'])
        self.assertEqual(research['pages'][1]['query_ids'], ['q1', 'q2'])
        self.assertEqual(research['pages'][1]['discovery_ids'], ['hit-1', 'hit-2'])
        self.assertEqual(research['pages'][2]['discovery_ids'], ['hit-3'])

    def test_run_resume_reuses_outputs_and_refreshes_import(self):
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch) as fetch:
            self.assertEqual(rebuild_run.run(self._run()), 0, self._state())
            first = fetch.call_count
            self.assertGreater(first, 0)
            folder = self._folder()
            state = self._state()
            self.assertEqual(state['status'], 'complete')
            self.assertEqual(len(state['stages']['plan']['outputs']), 6)
            plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
            self.assertTrue(any(row.get('role') == 'discovered_source'
                                for page in plan['page_blueprints']
                                for row in page['source_comparison']))
            self.assertTrue(any(row.get('kind') == 'search_candidate' for row in plan['evidence']))
            self.assertFalse(any(row.get('kind') == 'observed_result' for row in plan['evidence']))
            research = json.loads((folder / 'research.json').read_text(encoding='utf-8'))
            external = next(page for page in research['pages'] if page['url'] == 'https://rival.com/pricing')
            self.assertEqual(external['query_ids'], ['q1'])
            self.assertEqual(len(external['discovery_ids']), 1)
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                                refresh=False, recover_lock=False)), 0)
            self.assertEqual(fetch.call_count, first)
            self.config['analysis']['max_output_tokens'] = 1000
            self._write_config()
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                                refresh=False, recover_lock=False)), 0)
            self.assertEqual(fetch.call_count, first)
            changed = self._state()
            self.assertEqual(changed['stages']['research']['attempts'], 1)
            self.assertEqual(changed['stages']['analysis']['attempts'], 2)
            self.assertEqual(changed['stages']['plan']['attempts'], 1)
            self.assertEqual(changed['stages']['audit']['attempts'], 1)
            self.assertEqual(changed['stages']['discovery']['attempts'], 1)

    def test_missing_provider_needs_input_and_resume_after_config(self):
        self.config['discovery'] = {}
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2)
            self.assertEqual(self._state()['status'], 'needs_input')
            self.assertTrue((self._folder() / 'plan.json').exists())
            self.config['discovery'] = {'provider': 'import', 'file': 'discovery.json'}
            self._write_config()
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(self._folder()), config=None,
                               refresh=False, recover_lock=False)), 0, self._state())
            self.assertEqual(self._state()['status'], 'complete')

    def test_invalid_output_rebuilt_and_preserved(self):
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 0, self._state())
            folder = self._folder()
            page = next((folder / 'pages').glob('*.md'))
            page.write_text('damaged', encoding='utf-8')
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                                refresh=False, recover_lock=False)), 0)
            self.assertNotEqual(page.read_text(encoding='utf-8'), 'damaged')
            self.assertTrue(any((folder / 'history' / 'plan').rglob(page.name)))

    def test_lock_and_corrupt_state(self):
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 0, self._state())
        folder = self._folder()
        lock = rebuild_run._lock(folder)
        try:
            with self.assertRaises(ValueError):
                rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                                    refresh=False, recover_lock=False))
        finally:
            rebuild_run._unlock(lock)
        state = self._state()
        state['stages']['plan']['outputs']['../escape'] = 'x'
        (folder / 'run.json').write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaises(ValueError):
            rebuild_run.status(argparse.Namespace(folder=str(folder)))

    def test_rebuild_cli_parser_connects_run_and_status(self):
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild.main(['run', 'https://example.com', '--topic', 'product price',
                                           '--locale', 'en-US', '--config', str(self.config_file),
                                           '--out', str(self.root / 'out')]), 0)
        folder = self._folder()
        self.assertEqual(rebuild.main(['status', str(folder)]), 0)
        self.assertEqual(rebuild.main(['resume', str(folder)]), 0)

    def test_bounded_research_keeps_external_candidate_among_many_owned_pages(self):
        self.audit['pages'] = [{'url': 'https://example.com/page-%d' % i, 'status': 200}
                               for i in range(30)]
        self.audit_file.write_text(json.dumps(self.audit), encoding='utf-8')
        self.config['max_research_pages'] = 2
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2)
        result = json.loads((self._folder() / 'research.json').read_text(encoding='utf-8'))
        self.assertEqual([p['url'] for p in result['pages']],
                         ['https://example.com', 'https://rival.com/pricing'])
        self.assertFalse(result['coverage']['complete'])

    def test_analysis_key_missing_then_resume_with_provider(self):
        self.config['analysis'] = {'provider': 'openai', 'model': 'test-model',
                                   'api_key_env': 'TEST_REBUILD_ANALYSIS_KEY'}
        self._write_config()
        with patch.dict(os.environ, {'TEST_REBUILD_ANALYSIS_KEY': ''}), \
             patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2)
        state = self._state()
        self.assertEqual(state['status'], 'needs_input')
        self.assertEqual(state['stages']['analysis']['status'], 'needs_input')
        plan = json.loads((self._folder() / 'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['analysis']['mode'], 'structural_only')
        imported = {'schema': rebuild_run.rebuild_analysis.IMPORT_SCHEMA,
                    'intents': [{'intent_id': 'topic', 'findings': [], 'section_suggestions': []}]}
        with patch.dict(os.environ, {'TEST_REBUILD_ANALYSIS_KEY': 'test-secret'}), \
             patch.object(rebuild_run.rebuild_analysis, '_openai_document', return_value=imported), \
             patch.object(rebuild_run.evidence, 'fetch_page', side_effect=AssertionError('research repeated')):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(self._folder()), config=None,
                               refresh=False, recover_lock=False)), 0)
        state = self._state()
        self.assertEqual(state['stages']['research']['attempts'], 1)
        self.assertEqual(state['stages']['analysis']['attempts'], 2)
        self.assertEqual(state['stages']['plan']['attempts'], 2)
        plan = json.loads((self._folder() / 'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['analysis']['mode'], 'validated_provider')
        self.assertNotIn('test-secret', json.dumps(state) + json.dumps(plan))

    def test_resume_uses_saved_settings_if_config_file_is_deleted(self):
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 0)
        self.config_file.unlink()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=AssertionError('network repeated')):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(self._folder()), config=None,
                               refresh=False, recover_lock=False)), 0)
        self.assertEqual(self._state()['stages']['research']['attempts'], 1)

    def test_interrupted_stage_resumes_without_repeating_audit(self):
        with patch.object(rebuild_run.rebuild_discovery, 'discover', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rebuild_run.run(self._run())
        self.assertEqual(self._state()['stages']['discovery']['status'], 'running')
        self.assertFalse((self._folder() / '.run.lock').exists())
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(self._folder()), config=None,
                               refresh=False, recover_lock=False)), 0)
        self.assertEqual(self._state()['stages']['audit']['attempts'], 1)
        self.assertEqual(self._state()['stages']['discovery']['attempts'], 2)

    def test_cycle_imports_observed_url_and_invalidates_review(self):
        brief = {'schema': 'su-presence/rebuild-brief/1', 'site_url': 'https://example.com',
                 'queries': [{'id': 'q1', 'text': 'product price', 'type': 'nonbrand',
                              'intent_id': 'topic', 'terms': ['product price'], 'required_sections': [],
                              'page_type': 'guide', 'priority': 2}], 'competitors': [],
                 'channels': ['chatgpt'], 'facts': [], 'pages': []}
        (self.root / 'brief.json').write_text(json.dumps(brief), encoding='utf-8')
        self.config['brief_file'] = 'brief.json'
        self.config['cycle'] = {'repeats': 1, 'observations_file': 'observations.json'}
        observations = {'schema': rebuild.SCHEMA_OBS, 'observations': [
            {'id': 'seen', 'query_id': 'q1', 'query_text': 'product price', 'channel': 'chatgpt',
             'outcome': 'observed', 'url': 'https://cited.example.org/answer',
             'evidence_url': 'https://cited.example.org/answer',
             'observed_at': '2026-10-19T00:00:00Z'}]}
        (self.root / 'observations.json').write_text(json.dumps(observations), encoding='utf-8')
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2, self._state())
        folder = self._folder()
        plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
        self.assertTrue(any(row.get('kind') == 'visibility_observation' for row in plan['evidence']))
        research = json.loads((folder / 'research.json').read_text(encoding='utf-8'))
        self.assertIn('https://cited.example.org/answer', [page['url'] for page in research['pages']])
        cycle = json.loads((folder / 'cycle-report.json').read_text(encoding='utf-8'))
        self.assertEqual(cycle['measurement']['observed_rows'], 1)
        self.assertEqual(cycle['measurement']['pending_slots'], 1)
        self.assertEqual(cycle['review']['status'], 'pending')
        self.assertTrue((folder / 'browser-queue.json').is_file())
        page = plan['page_blueprints'][0]
        review_input = {'schema': rebuild_run.rebuild_review.INPUT_SCHEMA, 'pages': [
            {'id': page['id'], 'url': 'https://example.com/review', 'intent': 'topic',
             'body': '# Pricing\nPublic pricing details.', 'claims': []}]}
        (self.root / 'review.json').write_text(json.dumps(review_input), encoding='utf-8')
        self.config['cycle']['review_file'] = 'review.json'
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=AssertionError('research repeated')):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                               refresh=False, recover_lock=False)), 2)
        first = json.loads((folder / 'review.json').read_text(encoding='utf-8'))
        self.assertEqual(first['input_sha256'], rebuild_run._file_hash(self.root / 'review.json'))
        review_input['pages'][0]['body'] += '\nUpdated details.'
        (self.root / 'review.json').write_text(json.dumps(review_input), encoding='utf-8')
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=AssertionError('research repeated')):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                               refresh=False, recover_lock=False)), 2)
        second = json.loads((folder / 'review.json').read_text(encoding='utf-8'))
        self.assertNotEqual(first['input_sha256'], second['input_sha256'])
        self.assertEqual(self._state()['stages']['research']['attempts'], 1)

    def test_cycle_counts_completed_zero_citation_queue(self):
        brief = {'schema': 'su-presence/rebuild-brief/1', 'site_url': 'https://example.com',
                 'queries': [{'id': 'q1', 'text': 'product price', 'type': 'nonbrand',
                              'intent_id': 'topic', 'terms': [], 'required_sections': [],
                              'page_type': 'guide'}], 'competitors': [], 'channels': ['chatgpt'],
                 'facts': [], 'pages': []}
        (self.root / 'brief.json').write_text(json.dumps(brief), encoding='utf-8')
        self.config.update(brief_file='brief.json', cycle={'repeats': 1})
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2)
        folder = self._folder()
        queue_path = folder / 'browser-queue.json'
        job = rebuild_run.rebuild_browser.claim_next(queue_path)
        rebuild_run.rebuild_browser.mark_submitted(queue_path, job['slot_id'], job['dispatch_id'], 'session-1')
        rebuild_run.rebuild_browser.record_result(queue_path, {
            'run_id': job['run_id'], 'slot_id': job['slot_id'], 'dispatch_id': job['dispatch_id'],
            'query_sha256': job['query_sha256'], 'query_text': job['query_text'], 'provider': job['provider'],
            'conditions': {'product': job['provider'], 'model': 'test', 'locale': 'en-US',
                           'web_search': True, 'session_mode': 'fresh', 'measurement_date': '2026-10-19'},
            'observed_at': '2026-10-19T00:00:00Z', 'session_evidence': 'session-1',
            'raw_response_reference': 'response-1', 'answer_complete': True,
            'brand_mentioned': False, 'citation_coverage': 'complete', 'cited_urls': []})
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                               refresh=False, recover_lock=False)), 2)
        cycle = json.loads((folder / 'cycle-report.json').read_text(encoding='utf-8'))
        self.assertEqual(cycle['measurement']['status'], 'complete', self._state())
        self.assertEqual(cycle['measurement']['completed_slots'], 1)
        self.assertEqual(cycle['measurement']['zero_citation_completed_slots'], 1)
        self.assertEqual(cycle['measurement']['observed_rows'], 0)

    def test_cycle_preserves_queue_and_flags_changed_query_and_unsupported_provider(self):
        brief = {'schema': 'su-presence/rebuild-brief/1', 'site_url': 'https://example.com',
                 'queries': [{'id': 'q1', 'text': 'product price', 'type': 'nonbrand',
                              'intent_id': 'topic', 'terms': [], 'required_sections': [],
                              'page_type': 'guide'}], 'competitors': [],
                 'channels': ['chatgpt', 'copilot'], 'facts': [], 'pages': []}
        brief_path = self.root / 'brief.json'
        brief_path.write_text(json.dumps(brief), encoding='utf-8')
        self.config.update(brief_file='brief.json', cycle={'repeats': 1})
        self._write_config()
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.run(self._run()), 2)
        folder = self._folder()
        queue_path = folder / 'browser-queue.json'
        initial = queue_path.read_bytes()
        report = json.loads((folder / 'cycle-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['measurement']['browser_unsupported_providers'], ['copilot'])
        self.assertEqual(len(json.loads(initial)['slots']), 1)
        brief['queries'][0]['text'] = 'product pricing'
        brief_path.write_text(json.dumps(brief), encoding='utf-8')
        self._write_discovery()
        discovery = json.loads(self.discovery_file.read_text(encoding='utf-8'))
        discovery['candidates'][0]['query_text'] = 'product pricing'
        self.discovery_file.write_text(json.dumps(discovery), encoding='utf-8')
        with patch.object(rebuild_run.evidence, 'fetch_page', side_effect=self._fake_fetch):
            self.assertEqual(rebuild_run.resume(argparse.Namespace(folder=str(folder), config=None,
                               refresh=False, recover_lock=False)), 2)
        self.assertEqual(queue_path.read_bytes(), initial)
        report = json.loads((folder / 'cycle-report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['measurement']['queue_stale'])
        self.assertEqual(report['measurement']['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
