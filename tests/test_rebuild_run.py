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


if __name__ == '__main__':
    unittest.main()
